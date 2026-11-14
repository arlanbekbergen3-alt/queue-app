from fastapi import APIRouter, Depends, Response, status

from core.dependencies import CurrentTeacher, EntryToken, ProtocolServiceDep
from core.ratelimit import limit_token
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
    GradesIn,
    LevelsIn,
    MySubmissionOut,
    PresetApplyIn,
    PresetOut,
    QuestionCreate,
    QuestionImportIn,
    QuestionImportOut,
    QuestionOut,
    QuestionUpdate,
    SessionAssignmentIn,
    SubmissionOut,
)

router = APIRouter(tags=["Protocol"])


@router.post("/assignments", response_model=AssignmentOut, status_code=status.HTTP_201_CREATED)
async def create_assignment(
    data: AssignmentCreate, teacher: CurrentTeacher, service: ProtocolServiceDep
):
    return await service.create_assignment(teacher.id, data)


@router.get("/assignments", response_model=list[AssignmentOut])
async def list_assignments(teacher: CurrentTeacher, service: ProtocolServiceDep):
    return await service.list_assignments(teacher.id)


@router.get("/assignments/{assignment_id}", response_model=AssignmentOut)
async def get_assignment(assignment_id: str, teacher: CurrentTeacher, service: ProtocolServiceDep):
    return await service.get_assignment(teacher.id, assignment_id)


@router.patch("/assignments/{assignment_id}", response_model=AssignmentOut)
async def update_assignment(
    assignment_id: str,
    data: AssignmentUpdate,
    teacher: CurrentTeacher,
    service: ProtocolServiceDep,
):
    return await service.update_assignment(teacher.id, assignment_id, data)


@router.delete("/assignments/{assignment_id}", status_code=status.HTTP_204_NO_CONTENT)
async def delete_assignment(
    assignment_id: str, teacher: CurrentTeacher, service: ProtocolServiceDep
):
    await service.delete_assignment(teacher.id, assignment_id)
    return Response(status_code=status.HTTP_204_NO_CONTENT)


@router.get("/assignments/{assignment_id}/questions", response_model=list[QuestionOut])
async def list_questions(assignment_id: str, teacher: CurrentTeacher, service: ProtocolServiceDep):
    return await service.list_questions(teacher.id, assignment_id)


@router.post(
    "/assignments/{assignment_id}/questions",
    response_model=QuestionOut,
    status_code=status.HTTP_201_CREATED,
)
async def add_question(
    assignment_id: str,
    data: QuestionCreate,
    teacher: CurrentTeacher,
    service: ProtocolServiceDep,
):
    return await service.add_question(teacher.id, assignment_id, data)


@router.post("/assignments/{assignment_id}/questions/import", response_model=QuestionImportOut)
async def import_questions(
    assignment_id: str,
    data: QuestionImportIn,
    teacher: CurrentTeacher,
    service: ProtocolServiceDep,
):
    return await service.import_questions(teacher.id, assignment_id, data)


@router.get("/criteria-presets", response_model=list[PresetOut])
async def criteria_presets(teacher: CurrentTeacher, service: ProtocolServiceDep):
    return service.presets()


@router.post("/assignments/{assignment_id}/criteria/preset", response_model=list[CriterionOut])
async def apply_preset(
    assignment_id: str,
    data: PresetApplyIn,
    teacher: CurrentTeacher,
    service: ProtocolServiceDep,
):
    return await service.apply_preset(teacher.id, assignment_id, data)


@router.patch("/assignments/{assignment_id}/questions/{question_id}", response_model=QuestionOut)
async def update_question(
    assignment_id: str,
    question_id: str,
    data: QuestionUpdate,
    teacher: CurrentTeacher,
    service: ProtocolServiceDep,
):
    return await service.update_question(teacher.id, assignment_id, question_id, data)


@router.delete(
    "/assignments/{assignment_id}/questions/{question_id}",
    status_code=status.HTTP_204_NO_CONTENT,
)
async def delete_question(
    assignment_id: str, question_id: str, teacher: CurrentTeacher, service: ProtocolServiceDep
):
    await service.delete_question(teacher.id, assignment_id, question_id)
    return Response(status_code=status.HTTP_204_NO_CONTENT)


@router.get("/assignments/{assignment_id}/criteria", response_model=list[CriterionOut])
async def list_criteria(assignment_id: str, teacher: CurrentTeacher, service: ProtocolServiceDep):
    return await service.list_criteria(teacher.id, assignment_id)


@router.post(
    "/assignments/{assignment_id}/criteria",
    response_model=CriterionOut,
    status_code=status.HTTP_201_CREATED,
)
async def add_criterion(
    assignment_id: str,
    data: CriterionCreate,
    teacher: CurrentTeacher,
    service: ProtocolServiceDep,
):
    return await service.add_criterion(teacher.id, assignment_id, data)


@router.patch("/assignments/{assignment_id}/criteria/{criterion_id}", response_model=CriterionOut)
async def update_criterion(
    assignment_id: str,
    criterion_id: str,
    data: CriterionUpdate,
    teacher: CurrentTeacher,
    service: ProtocolServiceDep,
):
    return await service.update_criterion(teacher.id, assignment_id, criterion_id, data)


@router.put(
    "/assignments/{assignment_id}/criteria/{criterion_id}/levels", response_model=CriterionOut
)
async def set_levels(
    assignment_id: str,
    criterion_id: str,
    data: LevelsIn,
    teacher: CurrentTeacher,
    service: ProtocolServiceDep,
):
    return await service.set_levels(teacher.id, assignment_id, criterion_id, data)


@router.delete(
    "/assignments/{assignment_id}/criteria/{criterion_id}",
    status_code=status.HTTP_204_NO_CONTENT,
)
async def delete_criterion(
    assignment_id: str, criterion_id: str, teacher: CurrentTeacher, service: ProtocolServiceDep
):
    await service.delete_criterion(teacher.id, assignment_id, criterion_id)
    return Response(status_code=status.HTTP_204_NO_CONTENT)


@router.put("/sessions/{code}/assignment", status_code=status.HTTP_204_NO_CONTENT)
async def set_session_assignment(
    code: str,
    data: SessionAssignmentIn,
    teacher: CurrentTeacher,
    service: ProtocolServiceDep,
):
    await service.set_session_assignment(code, teacher.id, data.assignment_id)
    return Response(status_code=status.HTTP_204_NO_CONTENT)


@router.get("/sessions/{code}/submissions", response_model=list[SubmissionOut])
async def list_submissions(code: str, teacher: CurrentTeacher, service: ProtocolServiceDep):
    return await service.list_submissions(code, teacher.id)


@router.get("/sessions/{code}/submissions/current", response_model=SubmissionOut)
async def current_submission(code: str, teacher: CurrentTeacher, service: ProtocolServiceDep):
    return await service.current_submission(code, teacher.id)


@router.get(
    "/sessions/{code}/me/submission",
    response_model=MySubmissionOut,
    dependencies=[Depends(limit_token)],
)
async def my_submission(code: str, token: EntryToken, service: ProtocolServiceDep):
    return await service.my_submission(code, token)


@router.get("/submissions/{submission_id}", response_model=SubmissionOut)
async def get_submission(submission_id: str, teacher: CurrentTeacher, service: ProtocolServiceDep):
    return await service.get_submission(teacher.id, submission_id)


@router.put("/submissions/{submission_id}/grades", response_model=SubmissionOut)
async def save_grades(
    submission_id: str, data: GradesIn, teacher: CurrentTeacher, service: ProtocolServiceDep
):
    return await service.save_grades(teacher.id, submission_id, data)


@router.post("/submissions/{submission_id}/complete", response_model=SubmissionOut)
async def complete_submission(
    submission_id: str,
    teacher: CurrentTeacher,
    service: ProtocolServiceDep,
    data: GradesIn | None = None,
):
    return await service.complete(teacher.id, submission_id, data)


@router.post("/submissions/{submission_id}/correct", response_model=SubmissionOut)
async def correct_submission(
    submission_id: str, data: CorrectionIn, teacher: CurrentTeacher, service: ProtocolServiceDep
):
    return await service.correct(teacher.id, submission_id, data)


@router.post("/submissions/{submission_id}/bonus/remove", response_model=SubmissionOut)
async def remove_bonus(
    submission_id: str, data: BonusRemoveIn, teacher: CurrentTeacher, service: ProtocolServiceDep
):
    return await service.remove_bonus(teacher.id, submission_id, data)


@router.get("/submissions/{submission_id}/audit", response_model=list[AuditOut])
async def submission_audit(
    submission_id: str, teacher: CurrentTeacher, service: ProtocolServiceDep
):
    return await service.audit(teacher.id, submission_id)
