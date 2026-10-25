import uuid

from fastapi import APIRouter, Depends, HTTPException, Query, Response, status
from sqlalchemy import case, func, select, text
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session, selectinload

from app.database import get_session
from app.engine.status import TaskStatus
from app.engine.workflow import get_workflow
from app.models import StepAttempt, Task, TaskStep
from app.schemas import AttemptRead, EventRead, StepRead, TaskCreate, TaskListRead, TaskRead
from app.services.tasks import (
    create_task,
    get_task,
    list_events,
    request_cancel,
    retry_failed_task,
)

router = APIRouter()


def require_task(session: Session, task_id: uuid.UUID) -> Task:
    task = get_task(session, task_id)
    if task is None:
        raise HTTPException(status_code=404, detail="Task not found")
    return task


@router.get("/health")
def health(session: Session = Depends(get_session)) -> dict[str, str]:
    session.execute(text("SELECT 1"))
    return {"status": "ok", "database": "reachable"}


@router.post("/api/tasks", response_model=TaskRead, status_code=status.HTTP_201_CREATED)
def post_task(
    payload: TaskCreate,
    response: Response,
    session: Session = Depends(get_session),
) -> Task:
    try:
        task, created = create_task(
            session,
            task_type=payload.task_type,
            input_json=payload.input,
            idempotency_key=payload.idempotency_key,
        )
        session.commit()
    except ValueError as exc:
        session.rollback()
        raise HTTPException(status_code=422, detail=str(exc)) from exc
    except IntegrityError:
        session.rollback()
        if not payload.idempotency_key:
            raise
        task = session.scalar(select(Task).where(Task.idempotency_key == payload.idempotency_key))
        if task is None:
            raise
        created = False
    if not created:
        response.status_code = status.HTTP_200_OK
    return task


@router.get("/api/tasks", response_model=TaskListRead)
def read_tasks(
    task_status: TaskStatus | None = Query(default=None, alias="status"),
    limit: int = Query(default=50, ge=1, le=100),
    offset: int = Query(default=0, ge=0),
    session: Session = Depends(get_session),
) -> TaskListRead:
    filters = [Task.status == task_status] if task_status else []
    total = session.scalar(select(func.count()).select_from(Task).where(*filters)) or 0
    items = list(
        session.scalars(
            select(Task)
            .where(*filters)
            .order_by(Task.created_at.desc(), Task.id.desc())
            .offset(offset)
            .limit(limit)
        )
    )
    counts = {
        row.status: row.count
        for row in session.execute(
            select(Task.status, func.count().label("count")).group_by(Task.status)
        )
    }
    return TaskListRead(
        items=[TaskRead.model_validate(task) for task in items],
        total=total,
        limit=limit,
        offset=offset,
        status_counts=counts,
    )


@router.get("/api/tasks/{task_id}", response_model=TaskRead)
def read_task(task_id: uuid.UUID, session: Session = Depends(get_session)) -> Task:
    return require_task(session, task_id)


@router.get("/api/tasks/{task_id}/steps", response_model=list[StepRead])
def read_steps(task_id: uuid.UUID, session: Session = Depends(get_session)) -> list[TaskStep]:
    task = require_task(session, task_id)
    step_order = {
        definition.key: index for index, definition in enumerate(get_workflow(task.task_type))
    }
    return list(
        session.scalars(
            select(TaskStep)
            .where(TaskStep.task_id == task_id)
            .order_by(
                case(step_order, value=TaskStep.step_key, else_=len(step_order)),
                TaskStep.created_at,
            )
        )
    )


@router.get("/api/tasks/{task_id}/steps/{step_key}", response_model=StepRead)
def read_step(
    task_id: uuid.UUID, step_key: str, session: Session = Depends(get_session)
) -> TaskStep:
    step = session.scalar(
        select(TaskStep).where(TaskStep.task_id == task_id, TaskStep.step_key == step_key)
    )
    if step is None:
        raise HTTPException(status_code=404, detail="Step not found")
    return step


@router.get(
    "/api/tasks/{task_id}/steps/{step_key}/attempts",
    response_model=list[AttemptRead],
)
def read_attempts(
    task_id: uuid.UUID, step_key: str, session: Session = Depends(get_session)
) -> list[StepAttempt]:
    step = read_step(task_id, step_key, session)
    return list(
        session.scalars(
            select(StepAttempt)
            .where(StepAttempt.step_id == step.id)
            .order_by(StepAttempt.attempt_no)
        )
    )


@router.get("/api/tasks/{task_id}/events", response_model=list[EventRead])
def read_events(task_id: uuid.UUID, session: Session = Depends(get_session)):
    require_task(session, task_id)
    return list_events(session, task_id)


@router.post("/api/tasks/{task_id}/retry", response_model=TaskRead)
def retry_task(task_id: uuid.UUID, session: Session = Depends(get_session)) -> Task:
    task = require_task(session, task_id)
    try:
        retry_failed_task(session, task)
    except ValueError as exc:
        raise HTTPException(status_code=409, detail=str(exc)) from exc
    session.commit()
    return task


@router.post("/api/tasks/{task_id}/cancel", response_model=TaskRead)
def cancel_task(task_id: uuid.UUID, session: Session = Depends(get_session)) -> Task:
    task = session.scalar(select(Task).where(Task.id == task_id).options(selectinload(Task.steps)))
    if task is None:
        raise HTTPException(status_code=404, detail="Task not found")
    request_cancel(session, task)
    session.commit()
    return task
