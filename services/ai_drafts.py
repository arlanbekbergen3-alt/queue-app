import asyncio
import logging
from datetime import UTC, datetime, timedelta

from pydantic import BaseModel, Field
from sqlalchemy import func, select, update
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from core.config import settings
from core.exceptions import (
    ConflictError,
    NotFoundError,
    RateLimitError,
    ServiceUnavailableError,
)
from db.database import AsyncSessionLocal
from models.ai import AIJobORM, JobStatus
from models.protocol import CriterionORM, QuestionORM
from repositories.protocol import ProtocolRepository
from schemas.protocol import CriterionCreate, QuestionCreate
from services.ai import (
    DRAFT_SCHEMA,
    DRAFT_SYSTEM,
    AIBadResponseError,
    AIError,
    DraftResult,
    draft_user_message,
    gate,
    get_provider,
    validate_draft,
)

logger = logging.getLogger(__name__)

MAX_ACTIVE_JOBS = 3


class DraftRequest(BaseModel):
    source_text: str = Field(min_length=20, max_length=settings.AI_MAX_SOURCE_CHARS)


class JobOut(BaseModel):
    id: str
    status: str
    assignment_id: str
    result: dict | None = None
    error_code: str | None = None
    approved: bool = False


class ApproveIn(BaseModel):
    questions: list[QuestionCreate] = Field(default_factory=list, max_length=40)
    criteria: list[CriterionCreate] = Field(default_factory=list, max_length=15)


class ApproveOut(BaseModel):
    added_questions: int
    skipped_questions: int
    added_criteria: int
    skipped_criteria: int


class JobRuntime:
    def __init__(self) -> None:
        self.session_factory: async_sessionmaker[AsyncSession] = AsyncSessionLocal
        self.tasks: set[asyncio.Task[None]] = set()
        self.active = 0

    def start(self, job_id: str, source_text: str) -> None:
        self.active += 1
        task = asyncio.create_task(self._run(job_id, source_text))
        self.tasks.add(task)
        task.add_done_callback(self.tasks.discard)

    async def wait_idle(self) -> None:
        while self.tasks:
            await asyncio.gather(*list(self.tasks), return_exceptions=True)

    async def shutdown(self) -> None:
        for task in list(self.tasks):
            task.cancel()
        await asyncio.gather(*list(self.tasks), return_exceptions=True)

    async def sweep_interrupted(self) -> None:
        async with self.session_factory() as db:
            await db.execute(
                update(AIJobORM)
                .where(AIJobORM.status.in_([JobStatus.QUEUED, JobStatus.RUNNING]))
                .values(
                    status=JobStatus.ERROR,
                    error_code="ai_interrupted",
                    finished_at=datetime.now(UTC),
                )
            )
            await db.commit()

    async def _set(self, job_id: str, **values: object) -> None:
        async with self.session_factory() as db:
            await db.execute(update(AIJobORM).where(AIJobORM.id == job_id).values(**values))
            await db.commit()

    async def _run(self, job_id: str, source_text: str) -> None:
        try:
            async with gate.lock():
                await self._set(job_id, status=JobStatus.RUNNING)
                try:
                    draft = await self._generate(source_text)
                except AIError as error:
                    await self._finish_error(job_id, error.code)
                except TimeoutError:
                    await self._finish_error(job_id, "ai_timeout")
                else:
                    result = draft.model_dump()
                    result["min_questions"] = settings.AI_MIN_QUESTIONS
                    result["below_recommended"] = len(draft.questions) < settings.AI_MIN_QUESTIONS
                    await self._set(
                        job_id,
                        status=JobStatus.DONE,
                        result=result,
                        finished_at=datetime.now(UTC),
                    )
        except asyncio.CancelledError:
            raise
        except Exception:
            logger.exception("Draft job %s failed", job_id)
            await self._finish_error(job_id, "ai_internal")
        finally:
            self.active = max(0, self.active - 1)

    @staticmethod
    async def _attempt(source_text: str, min_questions: int | None) -> DraftResult:
        raw = await asyncio.wait_for(
            get_provider().generate_json(
                system=DRAFT_SYSTEM,
                user=draft_user_message(source_text, min_questions=min_questions),
                schema=DRAFT_SCHEMA,
                max_tokens=settings.AI_MAX_OUTPUT_TOKENS,
            ),
            timeout=settings.AI_TIMEOUT_SECONDS + 5,
        )
        return validate_draft(raw)

    async def _generate(self, source_text: str) -> DraftResult:
        wanted = settings.AI_MIN_QUESTIONS
        draft = await self._attempt(source_text, None)
        if len(draft.questions) < wanted:
            try:
                retry = await self._attempt(source_text, wanted)
            except (AIError, TimeoutError):
                retry = None
            if retry is not None and len(retry.questions) > len(draft.questions):
                draft = retry
        if not draft.questions:
            raise AIBadResponseError("no usable questions")
        return draft

    async def _finish_error(self, job_id: str, code: str) -> None:
        try:
            await self._set(
                job_id, status=JobStatus.ERROR, error_code=code, finished_at=datetime.now(UTC)
            )
        except Exception:
            logger.exception("Could not record the failure of job %s", job_id)


runtime = JobRuntime()


class DraftService:
    def __init__(self, db: AsyncSession):
        self.db = db
        self.repo = ProtocolRepository(db)

    async def create(self, teacher_id: str, assignment_id: str, source_text: str) -> JobOut:
        if not settings.AI_ENABLED:
            raise ServiceUnavailableError("The AI draft feature is turned off", code="ai_disabled")
        if await self.repo.get_assignment(assignment_id, teacher_id) is None:
            raise NotFoundError("Assignment not found", code="assignment_not_found")
        since = datetime.now(UTC) - timedelta(hours=1)
        recent = (
            await self.db.execute(
                select(func.count())
                .select_from(AIJobORM)
                .where(AIJobORM.teacher_id == teacher_id, AIJobORM.created_at >= since)
            )
        ).scalar_one()
        if recent >= settings.AI_DRAFTS_PER_TEACHER_PER_HOUR:
            raise RateLimitError(
                "Too many draft requests, try again later",
                code="ai_rate_limited",
                headers={"Retry-After": "600"},
            )
        if runtime.active >= MAX_ACTIVE_JOBS:
            raise RateLimitError(
                "The model is busy, try again in a few minutes",
                code="ai_busy",
                headers={"Retry-After": "60"},
            )
        job = AIJobORM(teacher_id=teacher_id, assignment_id=assignment_id)
        self.db.add(job)
        await self.db.commit()
        runtime.start(job.id, source_text)
        return self._out(job)

    @staticmethod
    def _out(job: AIJobORM) -> JobOut:
        return JobOut(
            id=job.id,
            status=job.status,
            assignment_id=job.assignment_id,
            result=job.result,
            error_code=job.error_code,
            approved=job.approved_at is not None,
        )

    async def _job(self, teacher_id: str, job_id: str, *, lock: bool = False) -> AIJobORM:
        stmt = select(AIJobORM).where(AIJobORM.id == job_id, AIJobORM.teacher_id == teacher_id)
        if lock:
            stmt = stmt.with_for_update()
        stmt = stmt.execution_options(populate_existing=True)
        job = (await self.db.execute(stmt)).scalar_one_or_none()
        if job is None:
            raise NotFoundError("Job not found", code="job_not_found")
        return job

    async def get(self, teacher_id: str, job_id: str) -> JobOut:
        return self._out(await self._job(teacher_id, job_id))

    async def approve(self, teacher_id: str, job_id: str, data: ApproveIn) -> ApproveOut:
        job = await self._job(teacher_id, job_id, lock=True)
        if job.status != JobStatus.DONE:
            raise ConflictError("The draft is not ready", code="draft_not_ready")
        if job.approved_at is not None:
            raise ConflictError("The draft was already approved", code="already_approved")
        assignment = await self.repo.get_assignment(job.assignment_id, teacher_id, lock=True)
        if assignment is None:
            raise NotFoundError("Assignment not found", code="assignment_not_found")
        questions = await self.repo.list_questions(assignment.id)
        known = {q.text.casefold() for q in questions}
        position = await self.repo.next_question_position(assignment.id)
        added_q = skipped_q = 0
        for item in data.questions:
            if item.text.casefold() in known or len(known) >= settings.MAX_QUESTIONS_PER_ASSIGNMENT:
                skipped_q += 1
                continue
            known.add(item.text.casefold())
            self.db.add(
                QuestionORM(
                    assignment_id=assignment.id,
                    text=item.text,
                    difficulty=item.difficulty,
                    topic=item.topic,
                    position=position,
                )
            )
            position += 1
            added_q += 1
        added_c = skipped_c = 0
        if data.criteria:
            if (await self.repo.assignment_counts(assignment.id))[2] > 0:
                raise ConflictError(
                    "Criteria cannot be changed after the first submission",
                    code="criteria_locked",
                )
            criteria = await self.repo.list_criteria(assignment.id)
            names = {c.name.casefold() for c in criteria}
            cposition = await self.repo.next_criterion_position(assignment.id)
            for crit in data.criteria:
                if (
                    crit.name.casefold() in names
                    or len(names) >= settings.MAX_CRITERIA_PER_ASSIGNMENT
                ):
                    skipped_c += 1
                    continue
                names.add(crit.name.casefold())
                self.db.add(
                    CriterionORM(
                        assignment_id=assignment.id,
                        name=crit.name,
                        max_points=crit.max_points,
                        position=cposition,
                    )
                )
                cposition += 1
                added_c += 1
        job.approved_at = datetime.now(UTC)
        await self.db.commit()
        return ApproveOut(
            added_questions=added_q,
            skipped_questions=skipped_q,
            added_criteria=added_c,
            skipped_criteria=skipped_c,
        )
