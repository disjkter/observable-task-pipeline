from datetime import UTC, datetime

from sqlalchemy.orm import Session

from app.engine.status import StepStatus, TaskStatus
from app.models import Task, TaskEvent
from app.services.notifications import notify_task_ready


def add_event(
    session: Session,
    task: Task,
    event_type: str,
    *,
    step_id=None,
    attempt_id=None,
    old_status: str | None = None,
    new_status: str | None = None,
    details: dict | None = None,
) -> None:
    session.add(
        TaskEvent(
            task_id=task.id,
            step_id=step_id,
            attempt_id=attempt_id,
            event_type=event_type,
            old_status=old_status,
            new_status=new_status,
            details_json=details or {},
        )
    )


def refresh_task(session: Session, task: Task) -> None:
    steps = list(task.steps)
    by_key = {step.step_key: step for step in steps}

    for step in steps:
        if step.status != StepStatus.BLOCKED:
            continue
        dependencies = [by_key[key] for key in step.depends_on]
        if dependencies and all(dep.status == StepStatus.SUCCEEDED for dep in dependencies):
            old_status = step.status
            step.status = StepStatus.PENDING
            add_event(
                session,
                task,
                "STEP_READY",
                step_id=step.id,
                old_status=old_status,
                new_status=step.status,
            )
            notify_task_ready(session, task.id, "STEP_READY")

    succeeded = sum(step.status == StepStatus.SUCCEEDED for step in steps)
    task.progress = 100 if succeeded == len(steps) else int(succeeded * 100 / len(steps))
    old_status = task.status

    if all(step.status == StepStatus.SUCCEEDED for step in steps):
        task.status = TaskStatus.SUCCEEDED
        task.finished_at = datetime.now(UTC)
        package_step = by_key.get("package_result")
        task.result_json = package_step.output_json if package_step else {}
    elif task.status == TaskStatus.CANCEL_REQUESTED:
        if not any(
            step.status in {StepStatus.RUNNING, StepStatus.CANCEL_REQUESTED} for step in steps
        ):
            task.status = TaskStatus.CANCELED
            task.finished_at = datetime.now(UTC)
    elif any(step.status == StepStatus.RUNNING for step in steps):
        task.status = TaskStatus.RUNNING
        task.started_at = task.started_at or datetime.now(UTC)
    elif any(step.status == StepStatus.RETRY_WAIT for step in steps):
        task.status = TaskStatus.RETRY_WAIT
    elif any(step.status == StepStatus.FAILED for step in steps):
        task.status = TaskStatus.FAILED
        task.finished_at = datetime.now(UTC)
        failed = next(step for step in steps if step.status == StepStatus.FAILED)
        task.error_code = "STEP_FAILED"
        task.error_message = f"Step {failed.step_key} failed"
    else:
        task.status = TaskStatus.QUEUED

    if task.status != old_status:
        add_event(
            session,
            task,
            "TASK_STATUS_CHANGED",
            old_status=old_status,
            new_status=task.status,
        )
