import random
import secrets
from datetime import UTC, datetime

from sqlalchemy import select, update
from sqlalchemy.ext.asyncio import AsyncSession

from models.entry import EntryORM
from models.protocol import (
    SessionEventORM,
    SubmissionORM,
    SubmissionQuestionORM,
    SubmissionStatus,
)
from models.queue_session import QueueSessionORM
from repositories.protocol import ProtocolRepository


def choose_question_ids(
    seed: str,
    pool_ids: list[str],
    count: int,
    usage: dict[str, int] | None = None,
    session_usage: dict[str, int] | None = None,
) -> list[str]:
    count = min(count, len(pool_ids))
    if count <= 0:
        return []
    rng = random.Random(seed)
    usage = usage or {}
    session_usage = session_usage or {}
    tiers: dict[tuple[int, int], list[str]] = {}
    for question_id in pool_ids:
        key = (usage.get(question_id, 0), session_usage.get(question_id, 0))
        tiers.setdefault(key, []).append(question_id)
    chosen: list[str] = []
    for key in sorted(tiers):
        need = count - len(chosen)
        if need <= 0:
            break
        tier = tiers[key]
        chosen += [tier[j] for j in rng.sample(range(len(tier)), min(need, len(tier)))]
    return chosen


def draw_round(session_usage: dict[str, int] | None, pool_ids: list[str] | None) -> int:
    if not pool_ids:
        return 1
    usage = session_usage or {}
    return min(usage.get(question_id, 0) for question_id in pool_ids) + 1


async def start_submission(db: AsyncSession, session: QueueSessionORM, entry: EntryORM) -> None:
    if session.assignment_id is None:
        return
    repo = ProtocolRepository(db)
    existing = await repo.submission_by_entry(entry.id)
    if existing is not None:
        if existing.status == SubmissionStatus.CANCELLED:
            existing.status = SubmissionStatus.IN_PROGRESS
            existing.cancel_reason = None
            existing.started_at = datetime.now(UTC)
            existing.ended_at = None
            repo.add_audit(
                submission_id=existing.id,
                actor_id=session.teacher_id,
                action="reopen",
                field="status",
                old_value=SubmissionStatus.CANCELLED,
                new_value=SubmissionStatus.IN_PROGRESS,
            )
            if entry.student_id is not None:
                await db.flush()
                await repo.renumber_attempts(entry.student_id, existing.assignment_id)
        return
    assignment = await repo.lock_assignment_by_id(session.assignment_id)
    if assignment is None:
        return
    pool = await repo.list_questions(assignment.id)
    pool_ids = [q.id for q in pool]
    by_id = {q.id: q for q in pool}
    seed = secrets.token_hex(16)
    usage: dict[str, int] = {}
    if entry.student_id is not None:
        usage = await repo.question_usage(entry.student_id, assignment.id)
    usage = {qid: n for qid, n in usage.items() if qid in by_id}
    session_usage = {
        qid: n for qid, n in (await repo.session_question_usage(session.id)).items() if qid in by_id
    }
    wanted = assignment.questions_per_submission
    if session.questions_override:
        wanted = min(wanted, session.questions_override)
    if session.ready_priority and entry.ready_now and session.ready_skip_questions:
        wanted = max(1, wanted - session.ready_skip_questions)
    chosen = choose_question_ids(seed, pool_ids, wanted, usage, session_usage)
    submission = SubmissionORM(
        entry_id=entry.id,
        session_id=session.id,
        assignment_id=assignment.id,
        started_at=datetime.now(UTC),
        selection_seed=seed,
        pool_question_ids=pool_ids,
        selection_usage=usage or None,
        selection_session_usage=session_usage or None,
        questions_target=wanted,
    )
    db.add(submission)
    await db.flush()
    db.add(
        SessionEventORM(
            session_id=session.id,
            actor_id=session.teacher_id,
            kind="questions_drawn",
            details={
                "submission_id": submission.id,
                "round": draw_round(session_usage, pool_ids),
                "pool": len(pool_ids),
                "wanted": wanted,
                "chosen": chosen,
                "short": len(chosen) < wanted,
            },
        )
    )
    for position, question_id in enumerate(chosen, start=1):
        question = by_id[question_id]
        db.add(
            SubmissionQuestionORM(
                submission_id=submission.id,
                question_id=question.id,
                text=question.text,
                difficulty=question.difficulty,
                topic=question.topic,
                position=position,
            )
        )
    if entry.student_id is not None:
        await db.flush()
        await repo.renumber_attempts(entry.student_id, assignment.id)


async def cancel_open_submission(
    db: AsyncSession, session: QueueSessionORM, entry: EntryORM
) -> None:
    if session.assignment_id is None:
        return
    submission = await ProtocolRepository(db).submission_by_entry(entry.id)
    if submission is not None and submission.status == SubmissionStatus.IN_PROGRESS:
        submission.status = SubmissionStatus.CANCELLED


async def has_open_submission(db: AsyncSession, session: QueueSessionORM, entry: EntryORM) -> bool:
    if session.assignment_id is None:
        return False
    submission = await ProtocolRepository(db).submission_by_entry(entry.id)
    return submission is not None and submission.status == SubmissionStatus.IN_PROGRESS


SESSION_CLOSED_REASON = "session_closed"


async def cancel_open_submissions(db: AsyncSession, session_ids: list[str]) -> int:
    if not session_ids:
        return 0
    result = await db.execute(
        update(SubmissionORM)
        .where(
            SubmissionORM.session_id.in_(session_ids),
            SubmissionORM.status == SubmissionStatus.IN_PROGRESS,
        )
        .values(status=SubmissionStatus.CANCELLED, cancel_reason=SESSION_CLOSED_REASON)
        .returning(SubmissionORM.id)
        .execution_options(synchronize_session=False)
    )
    return len(result.all())


async def restore_closed_submissions(db: AsyncSession, session_id: str) -> int:
    result = await db.execute(
        update(SubmissionORM)
        .where(
            SubmissionORM.session_id == session_id,
            SubmissionORM.status == SubmissionStatus.CANCELLED,
            SubmissionORM.cancel_reason == SESSION_CLOSED_REASON,
        )
        .values(status=SubmissionStatus.IN_PROGRESS, cancel_reason=None)
        .returning(SubmissionORM.id)
        .execution_options(synchronize_session=False)
    )
    return len(result.all())


async def session_ids_by_code(db: AsyncSession, codes: list[str]) -> list[str]:
    if not codes:
        return []
    rows = await db.execute(select(QueueSessionORM.id).where(QueueSessionORM.code.in_(codes)))
    return list(rows.scalars())
