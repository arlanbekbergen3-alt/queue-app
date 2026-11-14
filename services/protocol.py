from datetime import UTC, datetime

from sqlalchemy.exc import IntegrityError
from sqlalchemy.ext.asyncio import AsyncSession

from core.config import settings
from core.events import notify_changed
from core.exceptions import ConflictError, NotFoundError, UnprocessableError
from core.security import hash_token
from models.entry import EntryStatus
from models.protocol import (
    AssignmentORM,
    CriterionORM,
    QuestionORM,
    ScoreORM,
    SessionEventORM,
    SubmissionORM,
    SubmissionStatus,
)
from models.queue_session import QueueSessionORM
from repositories.protocol import ProtocolRepository
from repositories.queue import QueueRepository
from schemas.protocol import (
    AssignmentCreate,
    AssignmentOut,
    AssignmentUpdate,
    AuditOut,
    BonusRemoveIn,
    CorrectionIn,
    CriterionCreate,
    CriterionOut,
    CriterionUpdate,
    DeviationIn,
    DeviationOut,
    GradesIn,
    ImportLineOut,
    LevelIn,
    LevelOut,
    LevelsIn,
    MySubmissionOut,
    OriginOut,
    PresetApplyIn,
    PresetCriterionOut,
    PresetOut,
    QuestionCreate,
    QuestionImportIn,
    QuestionImportOut,
    QuestionOut,
    QuestionUpdate,
    ScoreOut,
    SubmissionOut,
    SubmissionQuestionOut,
)
from services.criteria_presets import PRESETS, find_preset
from services.grading_checks import (
    check_deviation,
    deviation_text,
    is_extreme,
    reason_given,
)
from services.protocol_calls import start_submission
from services.question_import import parse
from services.queue import normalize_code
from services.serving import mark_entry_done


class ProtocolService:
    def __init__(self, db: AsyncSession):
        self.db = db
        self.repo = ProtocolRepository(db)
        self.queue_repo = QueueRepository(db)

    async def _assignment(
        self, assignment_id: str, teacher_id: str, *, lock: bool = False
    ) -> AssignmentORM:
        assignment = await self.repo.get_assignment(assignment_id, teacher_id, lock=lock)
        if assignment is None:
            raise NotFoundError("Assignment not found", code="assignment_not_found")
        return assignment

    async def _mutable(self, assignment_id: str, teacher_id: str) -> AssignmentORM:
        assignment = await self._assignment(assignment_id, teacher_id, lock=True)
        if assignment.is_system:
            raise ConflictError(
                "Эта работа служебная (быстрая оценка) и не редактируется", code="system_assignment"
            )
        return assignment

    async def _assignment_out(self, assignment: AssignmentORM) -> AssignmentOut:
        questions, criteria, submissions = await self.repo.assignment_counts(assignment.id)
        out = AssignmentOut.model_validate(assignment)
        out.question_count = questions
        out.criteria_count = criteria
        out.submissions_count = submissions
        out.criteria_locked = submissions > 0
        if assignment.origin_author is not None:
            out.origin = OriginOut(
                author=assignment.origin_author,
                space=assignment.origin_space or "",
                title=assignment.origin_title or "",
            )
        return out

    async def create_assignment(self, teacher_id: str, data: AssignmentCreate) -> AssignmentOut:
        if await self.repo.count_assignments(teacher_id) >= settings.MAX_ASSIGNMENTS_PER_TEACHER:
            raise ConflictError("Too many assignments", code="assignment_limit")
        assignment = AssignmentORM(teacher_id=teacher_id, **data.model_dump())
        self.db.add(assignment)
        try:
            await self.db.commit()
        except IntegrityError:
            await self.db.rollback()
            raise ConflictError(
                "An assignment with this title already exists", code="assignment_title_taken"
            ) from None
        return await self._assignment_out(assignment)

    async def list_assignments(self, teacher_id: str) -> list[AssignmentOut]:
        return [await self._assignment_out(a) for a in await self.repo.list_assignments(teacher_id)]

    async def get_assignment(self, teacher_id: str, assignment_id: str) -> AssignmentOut:
        return await self._assignment_out(await self._assignment(assignment_id, teacher_id))

    async def update_assignment(
        self, teacher_id: str, assignment_id: str, data: AssignmentUpdate
    ) -> AssignmentOut:
        assignment = await self._mutable(assignment_id, teacher_id)
        for key, value in data.model_dump(exclude_unset=True).items():
            if value is not None:
                setattr(assignment, key, value)
        try:
            await self.db.commit()
        except IntegrityError:
            await self.db.rollback()
            raise ConflictError(
                "An assignment with this title already exists", code="assignment_title_taken"
            ) from None
        return await self._assignment_out(assignment)

    async def delete_assignment(self, teacher_id: str, assignment_id: str) -> None:
        assignment = await self._mutable(assignment_id, teacher_id)
        if (await self.repo.assignment_counts(assignment.id))[2] > 0:
            raise ConflictError(
                "The assignment already has submissions and cannot be deleted",
                code="assignment_has_submissions",
            )
        await self.db.delete(assignment)
        await self.db.commit()

    async def list_questions(self, teacher_id: str, assignment_id: str) -> list[QuestionOut]:
        await self._assignment(assignment_id, teacher_id)
        return [
            QuestionOut.model_validate(q) for q in await self.repo.list_questions(assignment_id)
        ]

    async def add_question(
        self, teacher_id: str, assignment_id: str, data: QuestionCreate
    ) -> QuestionOut:
        await self._mutable(assignment_id, teacher_id)
        if (
            len(await self.repo.list_questions(assignment_id))
            >= settings.MAX_QUESTIONS_PER_ASSIGNMENT
        ):
            raise ConflictError("Too many questions", code="question_limit")
        question = QuestionORM(
            assignment_id=assignment_id,
            position=await self.repo.next_question_position(assignment_id),
            **data.model_dump(),
        )
        self.db.add(question)
        await self._commit_unique("A question with this text already exists", "question_duplicate")
        return QuestionOut.model_validate(question)

    async def import_questions(
        self, teacher_id: str, assignment_id: str, data: QuestionImportIn
    ) -> QuestionImportOut:
        await self._mutable(assignment_id, teacher_id)
        existing = [q.text for q in await self.repo.list_questions(assignment_id)]
        capacity = max(0, settings.MAX_QUESTIONS_PER_ASSIGNMENT - len(existing))
        parsed = parse(data.text, set(existing), capacity)
        if not data.dry_run:
            position = await self.repo.next_question_position(assignment_id)
            for line in parsed:
                if line.status == "ok":
                    self.db.add(
                        QuestionORM(
                            assignment_id=assignment_id,
                            text=line.text,
                            difficulty=data.difficulty,
                            topic=data.topic,
                            position=position,
                        )
                    )
                    position += 1
            await self._commit_unique("Вопрос с таким текстом уже есть", "question_duplicate")
        added = sum(1 for line in parsed if line.status == "ok") if not data.dry_run else 0
        return QuestionImportOut(
            lines=[ImportLineOut(text=line.text, status=line.status) for line in parsed],
            added=added,
            remaining_capacity=capacity - added,
        )

    @staticmethod
    def presets() -> list[PresetOut]:
        return [
            PresetOut(
                key=p.key,
                title=p.title,
                criteria=[PresetCriterionOut(name=n, max_points=m) for n, m in p.criteria],
            )
            for p in PRESETS
        ]

    async def apply_preset(
        self, teacher_id: str, assignment_id: str, data: PresetApplyIn
    ) -> list[CriterionOut]:
        preset = find_preset(data.preset)
        if preset is None:
            raise NotFoundError("Набор критериев не найден", code="preset_not_found")
        await self._mutable(assignment_id, teacher_id)
        await self._criteria_editable(assignment_id)
        present = await self.repo.list_criteria(assignment_id)
        names = {c.name.casefold() for c in present}
        fresh = [(n, m) for n, m in preset.criteria if n.casefold() not in names]
        if len(present) + len(fresh) > settings.MAX_CRITERIA_PER_ASSIGNMENT:
            raise ConflictError(
                "Набор не помещается: превышен лимит критериев в работе", code="criteria_limit"
            )
        position = await self.repo.next_criterion_position(assignment_id)
        for name, points in fresh:
            self.db.add(
                CriterionORM(
                    assignment_id=assignment_id, name=name, max_points=points, position=position
                )
            )
            position += 1
        await self._commit_unique("Критерий с таким названием уже есть", "criterion_duplicate")
        return [
            CriterionOut.model_validate(c) for c in await self.repo.list_criteria(assignment_id)
        ]

    async def update_question(
        self, teacher_id: str, assignment_id: str, question_id: str, data: QuestionUpdate
    ) -> QuestionOut:
        await self._mutable(assignment_id, teacher_id)
        question = await self.repo.get_question(assignment_id, question_id)
        if question is None:
            raise NotFoundError("Question not found", code="question_not_found")
        for key, value in data.model_dump(exclude_unset=True).items():
            if value is not None:
                setattr(question, key, value)
        await self._commit_unique("A question with this text already exists", "question_duplicate")
        return QuestionOut.model_validate(question)

    async def delete_question(self, teacher_id: str, assignment_id: str, question_id: str) -> None:
        await self._mutable(assignment_id, teacher_id)
        question = await self.repo.get_question(assignment_id, question_id)
        if question is None:
            raise NotFoundError("Question not found", code="question_not_found")
        await self.db.delete(question)
        await self.db.commit()

    async def list_criteria(self, teacher_id: str, assignment_id: str) -> list[CriterionOut]:
        await self._assignment(assignment_id, teacher_id)
        return [
            CriterionOut.model_validate(c) for c in await self.repo.list_criteria(assignment_id)
        ]

    async def _criteria_editable(self, assignment_id: str) -> None:
        if (await self.repo.assignment_counts(assignment_id))[2] > 0:
            raise ConflictError(
                "Criteria cannot be changed after the first submission", code="criteria_locked"
            )

    async def add_criterion(
        self, teacher_id: str, assignment_id: str, data: CriterionCreate
    ) -> CriterionOut:
        await self._mutable(assignment_id, teacher_id)
        await self._criteria_editable(assignment_id)
        if (
            len(await self.repo.list_criteria(assignment_id))
            >= settings.MAX_CRITERIA_PER_ASSIGNMENT
        ):
            raise ConflictError("Too many criteria", code="criteria_limit")
        levels = self._levels_payload(data.levels, data.max_points)
        criterion = CriterionORM(
            assignment_id=assignment_id,
            position=await self.repo.next_criterion_position(assignment_id),
            name=data.name,
            max_points=data.max_points,
            levels=levels,
        )
        self.db.add(criterion)
        await self._commit_unique(
            "A criterion with this name already exists", "criterion_duplicate"
        )
        return CriterionOut.model_validate(criterion)

    async def update_criterion(
        self, teacher_id: str, assignment_id: str, criterion_id: str, data: CriterionUpdate
    ) -> CriterionOut:
        await self._mutable(assignment_id, teacher_id)
        await self._criteria_editable(assignment_id)
        criterion = await self.repo.get_criterion(assignment_id, criterion_id)
        if criterion is None:
            raise NotFoundError("Criterion not found", code="criterion_not_found")
        for key, value in data.model_dump(exclude_unset=True).items():
            if value is not None:
                setattr(criterion, key, value)
        self._levels_payload(
            [LevelIn(**level) for level in criterion.levels or []], criterion.max_points
        )
        await self._commit_unique(
            "A criterion with this name already exists", "criterion_duplicate"
        )
        return CriterionOut.model_validate(criterion)

    @staticmethod
    def _levels_payload(levels: list[LevelIn] | None, max_points: int) -> list[dict] | None:
        if not levels:
            return None
        if any(level.points > max_points for level in levels):
            raise UnprocessableError(
                f"Баллы уровней должны быть от 0 до {max_points}", code="levels_out_of_range"
            )
        return [level.model_dump() for level in levels]

    async def set_levels(
        self, teacher_id: str, assignment_id: str, criterion_id: str, data: LevelsIn
    ) -> CriterionOut:
        await self._mutable(assignment_id, teacher_id)
        criterion = await self.repo.get_criterion(assignment_id, criterion_id)
        if criterion is None:
            raise NotFoundError("Criterion not found", code="criterion_not_found")
        criterion.levels = self._levels_payload(data.levels, criterion.max_points)
        await self.db.commit()
        return CriterionOut.model_validate(criterion)

    async def delete_criterion(
        self, teacher_id: str, assignment_id: str, criterion_id: str
    ) -> None:
        await self._mutable(assignment_id, teacher_id)
        await self._criteria_editable(assignment_id)
        criterion = await self.repo.get_criterion(assignment_id, criterion_id)
        if criterion is None:
            raise NotFoundError("Criterion not found", code="criterion_not_found")
        await self.db.delete(criterion)
        await self.db.commit()

    async def _commit_unique(self, detail: str, code: str) -> None:
        try:
            await self.db.commit()
        except IntegrityError:
            await self.db.rollback()
            raise ConflictError(detail, code=code) from None

    async def _owned_session(
        self, code: str, teacher_id: str, *, lock: bool = False
    ) -> QueueSessionORM:
        session = await self.queue_repo.get_session(normalize_code(code), lock=lock)
        if session is None or session.teacher_id != teacher_id:
            raise NotFoundError("Session not found", code="session_not_found")
        return session

    async def set_session_assignment(
        self, code: str, teacher_id: str, assignment_id: str | None
    ) -> None:
        session = await self._owned_session(code, teacher_id, lock=True)
        if session.session_mode != "protocol":
            raise ConflictError(
                "В этом режиме работа с критериями не подключается", code="mode_no_assignment"
            )
        if assignment_id is not None:
            if (await self._assignment(assignment_id, teacher_id)).is_system:
                raise NotFoundError("Assignment not found", code="assignment_not_found")
        if await self.repo.session_submission_count(session.id) > 0:
            raise ConflictError(
                "The session already has submissions", code="session_has_submissions"
            )
        session.assignment_id = assignment_id
        if assignment_id is not None:
            called = await QueueRepository(self.db).get_called(session.id)
            if called is not None:
                await start_submission(self.db, session, called)
        await self.db.commit()
        notify_changed(session.code)

    async def _submission_out(
        self, submission: SubmissionORM, ticket: int, *, audit_view: bool = True
    ) -> SubmissionOut:
        questions = await self.repo.submission_questions(submission.id)
        scores = await self.repo.scores(submission.id)
        pool = [str(x) for x in submission.pool_question_ids]
        wanted = (
            submission.questions_target
            or (await self._per_submission(submission.assignment_id))
            or 0
        )
        return SubmissionOut(
            id=submission.id,
            entry_id=submission.entry_id,
            ticket=ticket,
            status=submission.status,
            started_at=submission.started_at,
            ended_at=submission.ended_at,
            duration_seconds=submission.duration_seconds,
            total_points=submission.total_points,
            max_points=submission.max_points,
            comment=submission.comment,
            questions=[SubmissionQuestionOut.model_validate(q) for q in questions],
            scores=[self._score_out(s, c) for s, c in scores],
            questions_short=len(questions) < wanted,
            attempt_number=submission.attempt_number,
            bonus_points=submission.bonus_points,
            selection_seed=submission.selection_seed if audit_view else None,
            pool_question_ids=pool if audit_view else None,
            selection_usage=(submission.selection_usage or None) if audit_view else None,
        )

    async def _per_submission(self, assignment_id: str) -> int | None:
        assignment = await self.db.get(AssignmentORM, assignment_id)
        return assignment.questions_per_submission if assignment else None

    @staticmethod
    def _score_out(score: ScoreORM, criterion: CriterionORM) -> ScoreOut:
        return ScoreOut(
            criterion_id=criterion.id,
            name=criterion.name,
            points=score.points,
            max_points=criterion.max_points,
            comment=score.comment,
            levels=[LevelOut(**level) for level in criterion.levels or []],
        )

    async def list_submissions(self, code: str, teacher_id: str) -> list[SubmissionOut]:
        session = await self._owned_session(code, teacher_id)
        rows = await self.repo.list_session_submissions(session.id)
        return [await self._submission_out(s, ticket) for s, ticket in rows]

    async def current_submission(self, code: str, teacher_id: str) -> SubmissionOut:
        session = await self._owned_session(code, teacher_id)
        row = await self.repo.current_submission(session.id)
        if row is None:
            raise NotFoundError("No submission in progress", code="submission_not_found")
        return await self._submission_out(row[0], row[1])

    async def get_submission(self, teacher_id: str, submission_id: str) -> SubmissionOut:
        found = await self.repo.owned_submission(submission_id, teacher_id)
        if found is None:
            raise NotFoundError("Submission not found", code="submission_not_found")
        submission, _ = found
        return await self._submission_out(submission, await self.repo.submission_ticket(submission))

    async def _locked(
        self, teacher_id: str, submission_id: str
    ) -> tuple[SubmissionORM, QueueSessionORM]:
        found = await self.repo.owned_submission(submission_id, teacher_id)
        if found is None:
            raise NotFoundError("Submission not found", code="submission_not_found")
        session = await self.repo.lock_session_by_id(found[0].session_id)
        return await self.repo.refresh_submission(submission_id), session

    async def _criteria_map(self, assignment_id: str) -> dict[str, CriterionORM]:
        return {c.id: c for c in await self.repo.list_criteria(assignment_id)}

    @staticmethod
    def _validate_points(data: GradesIn | CorrectionIn, criteria: dict[str, CriterionORM]) -> None:
        for score in data.scores:
            criterion = criteria.get(score.criterion_id)
            if criterion is None:
                raise NotFoundError("Criterion not found", code="criterion_not_found")
            if score.points > criterion.max_points:
                raise UnprocessableError(
                    f"Points for '{criterion.name}' must be between 0 and {criterion.max_points}",
                    code="points_out_of_range",
                )

    async def _reasons_required(self, assignment_id: str) -> bool:
        assignment = await self.db.get(AssignmentORM, assignment_id)
        return bool(assignment and assignment.require_extreme_reason)

    @staticmethod
    def _reason_error(names: list[str]) -> UnprocessableError:
        return UnprocessableError(
            f"Нужно короткое пояснение (от {settings.EXTREME_REASON_MIN_LENGTH} символов) "
            f"к крайней оценке: {', '.join(names)}",
            code="reason_required",
        )

    async def deviation_check(
        self, teacher_id: str, submission_id: str, data: DeviationIn
    ) -> DeviationOut:
        found = await self.repo.owned_submission(submission_id, teacher_id)
        if found is None:
            raise NotFoundError("Submission not found", code="submission_not_found")
        submission, _ = found
        assignment = await self._assignment(submission.assignment_id, teacher_id)
        quiet = DeviationOut(
            enabled=assignment.deviation_warning,
            points=data.points,
            sigma=assignment.deviation_sigma,
            min_samples=settings.DEVIATION_MIN_SAMPLES,
        )
        if not assignment.deviation_warning:
            return quiet
        criterion = await self.repo.get_criterion(assignment.id, data.criterion_id)
        if criterion is None:
            raise NotFoundError("Criterion not found", code="criterion_not_found")
        if data.points > criterion.max_points:
            raise UnprocessableError(
                f"Points for '{criterion.name}' must be between 0 and {criterion.max_points}",
                code="points_out_of_range",
            )
        history = await self.repo.criterion_points(
            assignment.id, criterion.id, exclude_submission_id=submission.id
        )
        result = check_deviation(data.points, history, assignment.deviation_sigma)
        if result is None:
            quiet.samples = len(history)
            return quiet
        quiet.samples = result.samples
        quiet.mean = round(result.mean, 2)
        if not result.warn:
            return quiet
        self.db.add(
            SessionEventORM(
                session_id=submission.session_id,
                actor_id=teacher_id,
                kind="deviation_shown",
                details={"criterion_id": criterion.id},
            )
        )
        await self.db.commit()
        quiet.warn = True
        quiet.text = deviation_text(result.mean, data.points)
        return quiet

    async def save_grades(
        self, teacher_id: str, submission_id: str, data: GradesIn
    ) -> SubmissionOut:
        submission, _ = await self._locked(teacher_id, submission_id)
        if submission.status != SubmissionStatus.IN_PROGRESS:
            raise ConflictError(
                "Only a submission in progress can be edited this way",
                code="submission_not_editable",
            )
        criteria = await self._criteria_map(submission.assignment_id)
        self._validate_points(data, criteria)
        await self.repo.replace_scores(
            submission.id,
            [
                ScoreORM(
                    submission_id=submission.id,
                    criterion_id=s.criterion_id,
                    points=s.points,
                    comment=s.comment,
                )
                for s in data.scores
            ],
        )
        submission.comment = data.comment
        await self.db.commit()
        return await self._submission_out(submission, await self.repo.submission_ticket(submission))

    async def complete(
        self, teacher_id: str, submission_id: str, data: GradesIn | None
    ) -> SubmissionOut:
        submission, session = await self._locked(teacher_id, submission_id)
        if submission.status == SubmissionStatus.COMPLETED:
            raise ConflictError("The submission is already completed", code="already_completed")
        if submission.status != SubmissionStatus.IN_PROGRESS:
            raise ConflictError("The submission was cancelled", code="submission_cancelled")
        criteria = await self._criteria_map(submission.assignment_id)
        if data is not None:
            self._validate_points(data, criteria)
            await self.repo.replace_scores(
                submission.id,
                [
                    ScoreORM(
                        submission_id=submission.id,
                        criterion_id=s.criterion_id,
                        points=s.points,
                        comment=s.comment,
                    )
                    for s in data.scores
                ],
            )
            submission.comment = data.comment
        scored = {s.criterion_id: s for s, _ in await self.repo.scores(submission.id)}
        missing = [c.name for cid, c in criteria.items() if cid not in scored]
        if missing:
            await self.db.rollback()
            raise ConflictError(
                "Score every criterion first: " + ", ".join(missing), code="grades_incomplete"
            )
        if await self._reasons_required(submission.assignment_id):
            lacking = [
                criteria[cid].name
                for cid, s in scored.items()
                if cid in criteria
                and is_extreme(s.points, criteria[cid].max_points)
                and not reason_given(s.comment)
            ]
            if lacking:
                await self.db.rollback()
                raise self._reason_error(lacking)
        now = datetime.now(UTC)
        submission.status = SubmissionStatus.COMPLETED
        submission.ended_at = now
        submission.duration_seconds = max(0, int((now - submission.started_at).total_seconds()))
        submission.total_points = sum(s.points for s in scored.values())
        submission.max_points = sum(c.max_points for c in criteria.values())
        for cid, score in scored.items():
            self.repo.add_audit(
                submission_id=submission.id,
                actor_id=teacher_id,
                action="complete",
                field=f"score:{cid}",
                old_value=None,
                new_value=str(score.points),
                occurred_at=now,
            )
        self.repo.add_audit(
            submission_id=submission.id,
            actor_id=teacher_id,
            action="complete",
            field="comment",
            old_value=None,
            new_value=submission.comment,
            occurred_at=now,
        )
        entry = await self.queue_repo.get_entry(session.id, submission.entry_id)
        if (
            entry is not None
            and session.ready_priority
            and entry.ready_now
            and session.ready_bonus_points
        ):
            submission.bonus_points = session.ready_bonus_points
            self.repo.add_audit(
                submission_id=submission.id,
                actor_id=teacher_id,
                action="bonus",
                field="bonus_points",
                old_value="0",
                new_value=str(submission.bonus_points),
                occurred_at=now,
            )
        if entry is not None and entry.status == EntryStatus.CALLED:
            await mark_entry_done(self.queue_repo, session, entry)
        await self.db.commit()
        notify_changed(session.code)
        return await self._submission_out(submission, await self.repo.submission_ticket(submission))

    async def correct(
        self,
        teacher_id: str,
        submission_id: str,
        data: CorrectionIn,
        request_key: str | None = None,
    ) -> SubmissionOut:
        submission, session = await self._locked(teacher_id, submission_id)
        if request_key and await self.repo.audit_key_used(submission.id, request_key):
            await self.db.commit()
            return await self._submission_out(
                submission, await self.repo.submission_ticket(submission)
            )
        if submission.status != SubmissionStatus.COMPLETED:
            raise ConflictError(
                "Only a completed submission can be corrected", code="not_completed"
            )
        criteria = await self._criteria_map(submission.assignment_id)
        self._validate_points(data, criteria)
        current = {s.criterion_id: s for s, _ in await self.repo.scores(submission.id)}
        if await self._reasons_required(submission.assignment_id):
            lacking = [
                criteria[item.criterion_id].name
                for item in data.scores
                if item.criterion_id in current
                and current[item.criterion_id].points != item.points
                and is_extreme(item.points, criteria[item.criterion_id].max_points)
                and not reason_given(item.comment or current[item.criterion_id].comment)
            ]
            if lacking:
                await self.db.rollback()
                raise self._reason_error(lacking)
        changes: list[tuple[str, str | None, str | None]] = []
        for item in data.scores:
            existing = current.get(item.criterion_id)
            if existing is None or existing.points == item.points:
                continue
            changes.append((f"score:{item.criterion_id}", str(existing.points), str(item.points)))
            existing.points = item.points
            if item.comment:
                existing.comment = item.comment
        if data.comment is not None and data.comment != submission.comment:
            changes.append(("comment", submission.comment, data.comment))
            submission.comment = data.comment
        if not changes:
            await self.db.rollback()
            raise ConflictError("Nothing to correct", code="nothing_to_correct")
        submission.total_points = sum(s.points for s in current.values())
        now = datetime.now(UTC)
        for field, old, new in changes:
            self.repo.add_audit(
                submission_id=submission.id,
                actor_id=teacher_id,
                action="correct",
                field=field,
                old_value=old,
                new_value=new,
                reason=data.reason,
                occurred_at=now,
                request_key=request_key,
            )
        await self.db.commit()
        notify_changed(session.code)
        return await self._submission_out(submission, await self.repo.submission_ticket(submission))

    async def remove_bonus(
        self, teacher_id: str, submission_id: str, data: BonusRemoveIn
    ) -> SubmissionOut:
        submission, session = await self._locked(teacher_id, submission_id)
        if submission.bonus_points <= 0:
            raise ConflictError("У этой сдачи нет бонуса", code="no_bonus")
        old = submission.bonus_points
        submission.bonus_points = 0
        self.repo.add_audit(
            submission_id=submission.id,
            actor_id=teacher_id,
            action="bonus_remove",
            field="bonus_points",
            old_value=str(old),
            new_value="0",
            reason=data.reason,
        )
        await self.db.commit()
        notify_changed(session.code)
        return await self._submission_out(submission, await self.repo.submission_ticket(submission))

    async def audit(self, teacher_id: str, submission_id: str) -> list[AuditOut]:
        found = await self.repo.owned_submission(submission_id, teacher_id)
        if found is None:
            raise NotFoundError("Submission not found", code="submission_not_found")
        return [AuditOut.model_validate(e) for e in await self.repo.audit_entries(submission_id)]

    async def my_submission(self, code: str, token: str) -> MySubmissionOut:
        session = await self.queue_repo.get_session(normalize_code(code))
        if session is None:
            raise NotFoundError("Session not found", code="session_not_found")
        entry = await self.queue_repo.get_entry_by_token(session.id, hash_token(token))
        if entry is None:
            raise NotFoundError("Entry not found", code="entry_not_found")
        submission = await self.repo.submission_by_entry(entry.id)
        if submission is None:
            raise NotFoundError("Submission not found", code="submission_not_found")
        if submission.status != SubmissionStatus.COMPLETED:
            return MySubmissionOut(
                status=submission.status, attempt_number=submission.attempt_number
            )
        questions = await self.repo.submission_questions(submission.id)
        scores = await self.repo.scores(submission.id)
        return MySubmissionOut(
            status=submission.status,
            attempt_number=submission.attempt_number,
            questions=[SubmissionQuestionOut.model_validate(q) for q in questions],
            scores=[self._score_out(s, c) for s, c in scores],
            total_points=submission.total_points,
            max_points=submission.max_points,
            comment=submission.comment,
            bonus_points=submission.bonus_points,
        )
