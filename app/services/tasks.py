import uuid

from sqlalchemy import select
from sqlalchemy.orm import Session, selectinload

from app.engine.status import StepStatus, TaskStatus
from app.engine.workflow import get_workflow
from app.models import Task, TaskEvent, TaskStep
from app.services.state import add_event, refresh_task


def create_task(
    session: Session,
    *,
    task_type: str,
    input_json: dict,
    idempotency_key: str | None,
) -> tuple[Task, bool]:
    get_workflow(task_type)
    if idempotency_key:
        existing = session.scalar(
            select(Task)
            .where(Task.idempotency_key == idempotency_key)
            .options(selectinload(Task.steps))
        )
        if existing:
            return existing, False

    task = Task(
        task_no=f"TASK-{uuid.uuid4().hex[:16].upper()}",
        task_type=task_type,
        status=TaskStatus.QUEUED,
        progress=0,
        input_json=input_json,
        idempotency_key=idempotency_key,
    )
    session.add(task)
    session.flush()

    for definition in get_workflow(task_type):
        session.add(
            TaskStep(
                task_id=task.id,
                step_key=definition.key,
                step_name=definition.name,
                status=StepStatus.PENDING if not definition.depends_on else StepStatus.BLOCKED,
                depends_on=list(definition.depends_on),
                resource_requirements=definition.resources,
                input_json={},
                max_attempts=definition.max_attempts,
            )
        )
    add_event(session, task, "TASK_CREATED", new_status=task.status)
    session.flush()
    session.refresh(task)
    return task, True


def get_task(session: Session, task_id: uuid.UUID) -> Task | None:
    return session.scalar(select(Task).where(Task.id == task_id).options(selectinload(Task.steps)))


def request_cancel(session: Session, task: Task) -> Task:
    if task.status in {TaskStatus.SUCCEEDED, TaskStatus.FAILED, TaskStatus.CANCELED}:
        return task
    old_status = task.status
    task.status = TaskStatus.CANCEL_REQUESTED
    add_event(
        session,
        task,
        "CANCEL_REQUESTED",
        old_status=old_status,
        new_status=task.status,
    )
    for step in task.steps:
        if step.status in {StepStatus.PENDING, StepStatus.BLOCKED, StepStatus.RETRY_WAIT}:
            step.status = StepStatus.CANCELED
    refresh_task(session, task)
    return task


def retry_failed_task(session: Session, task: Task) -> Task:
    if task.status != TaskStatus.FAILED:
        raise ValueError("Only failed tasks can be retried")
    task.error_code = None
    task.error_message = None
    task.finished_at = None
    for step in task.steps:
        if step.status == StepStatus.FAILED:
            step.status = StepStatus.PENDING
            step.next_retry_at = None
        elif step.status == StepStatus.BLOCKED:
            step.status = StepStatus.BLOCKED
    add_event(session, task, "MANUAL_RETRY_REQUESTED")
    refresh_task(session, task)
    return task


def list_events(session: Session, task_id: uuid.UUID) -> list[TaskEvent]:
    return list(
        session.scalars(
            select(TaskEvent)
            .where(TaskEvent.task_id == task_id)
            .order_by(TaskEvent.created_at, TaskEvent.id)
        )
    )
