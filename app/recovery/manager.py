import uuid
from datetime import UTC, datetime, timedelta

from sqlalchemy import select
from sqlalchemy.orm import Session, selectinload

from app.config import get_settings
from app.engine.status import StepStatus, TaskStatus
from app.models import StepAttempt, StepResultCache, Task, TaskStep
from app.resources.manager import reclaim_expired_resources
from app.services.execution import clear_resource_lease
from app.services.state import add_event, refresh_task
from app.storage.artifacts import ArtifactStore


def recover_expired_steps(session: Session) -> int:
    now = datetime.now(UTC)
    steps = list(
        session.scalars(
            select(TaskStep)
            .where(TaskStep.status == StepStatus.RUNNING, TaskStep.lease_until < now)
            .with_for_update(skip_locked=True)
        )
    )
    recovered = 0
    for step in steps:
        task = session.scalar(
            select(Task).where(Task.id == step.task_id).options(selectinload(Task.steps))
        )
        assert task is not None
        old_token = step.lease_token
        attempt = session.scalar(
            select(StepAttempt).where(
                StepAttempt.step_id == step.id,
                StepAttempt.lease_token == old_token,
                StepAttempt.status == StepStatus.RUNNING,
            )
        )
        add_event(
            session,
            task,
            "LEASE_EXPIRED",
            step_id=step.id,
            attempt_id=attempt.id if attempt else None,
            details={"previous_worker_id": step.worker_id},
        )

        if task.status == TaskStatus.CANCEL_REQUESTED:
            if attempt:
                attempt.status = StepStatus.CANCELED
                attempt.finished_at = now
                attempt.duration_ms = max(0, int((now - attempt.started_at).total_seconds() * 1000))
            step.status = StepStatus.CANCELED
            step.finished_at = now
            step.lease_token = uuid.uuid4()
            step.lease_until = None
            step.worker_id = None
            clear_resource_lease(session, step.id)
            add_event(
                session,
                task,
                "STEP_CANCELED",
                step_id=step.id,
                attempt_id=attempt.id if attempt else None,
                new_status=step.status,
            )
            refresh_task(session, task)
            recovered += 1
            continue

        cache = None
        if step.input_hash:
            cache = session.scalar(
                select(StepResultCache).where(StepResultCache.input_hash == step.input_hash)
            )
            artifact_path = ArtifactStore().path_for(step.step_key, step.input_hash)
            published_checksum = ArtifactStore.published_checksum(artifact_path)
            if cache is None and published_checksum:
                cache = StepResultCache(
                    input_hash=step.input_hash,
                    status=StepStatus.SUCCEEDED,
                    output_uri=str(artifact_path),
                    checksum=published_checksum,
                )
                session.add(cache)
        if cache and ArtifactStore.validate(cache.output_uri, cache.checksum):
            output = ArtifactStore.read(cache.output_uri)
            step.status = StepStatus.SUCCEEDED
            step.output_json = output
            step.finished_at = now
            if attempt:
                attempt.status = StepStatus.SUCCEEDED
                attempt.finished_at = now
                attempt.duration_ms = max(0, int((now - attempt.started_at).total_seconds() * 1000))
                attempt.output_uri = cache.output_uri
            event_type = "ARTIFACT_RECOVERED"
        else:
            if attempt:
                attempt.status = StepStatus.FAILED
                attempt.finished_at = now
                attempt.duration_ms = max(0, int((now - attempt.started_at).total_seconds() * 1000))
                attempt.error_code = "WORKER_LOST"
                attempt.error_message = "Worker lease expired"
                attempt.retryable = True
            if step.attempt_count < step.max_attempts:
                step.status = StepStatus.RETRY_WAIT
                step.next_retry_at = now + timedelta(seconds=get_settings().retry_base_seconds)
            else:
                step.status = StepStatus.FAILED
                step.finished_at = now
            event_type = "STEP_RECOVERED"

        step.lease_token = uuid.uuid4()
        step.lease_until = None
        step.worker_id = None
        clear_resource_lease(session, step.id)
        add_event(
            session,
            task,
            event_type,
            step_id=step.id,
            attempt_id=attempt.id if attempt else None,
            new_status=step.status,
        )
        refresh_task(session, task)
        recovered += 1
    reclaim_expired_resources(session)
    return recovered
