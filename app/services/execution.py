import hashlib
import json
import uuid
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta

from sqlalchemy import delete, or_, select, update
from sqlalchemy.dialects.postgresql import insert as pg_insert
from sqlalchemy.orm import Session, selectinload

from app.config import get_settings
from app.engine.status import StepStatus, TaskStatus
from app.models import ResourceLease, StepAttempt, StepResultCache, Task, TaskStep
from app.resources.manager import release_gpu, renew_gpu, reserve_gpu
from app.services.state import add_event, refresh_task
from app.storage.artifacts import ArtifactStore


@dataclass(frozen=True)
class Claim:
    task_id: uuid.UUID
    step_id: uuid.UUID
    step_key: str
    attempt_id: uuid.UUID
    attempt_no: int
    lease_token: uuid.UUID


def claim_step(session: Session, worker_id: str) -> Claim | None:
    now = datetime.now(UTC)
    step = session.scalar(
        select(TaskStep)
        .join(Task)
        .where(
            Task.status.notin_([TaskStatus.CANCEL_REQUESTED, TaskStatus.CANCELED]),
            or_(
                TaskStep.status == StepStatus.PENDING,
                (TaskStep.status == StepStatus.RETRY_WAIT) & (TaskStep.next_retry_at <= now),
            ),
        )
        .order_by(TaskStep.created_at, TaskStep.id)
        .with_for_update(skip_locked=True)
        .limit(1)
    )
    if step is None:
        return None

    task = session.scalar(
        select(Task).where(Task.id == step.task_id).options(selectinload(Task.steps))
    )
    assert task is not None
    token = uuid.uuid4()
    lease_until = now + timedelta(seconds=get_settings().lease_seconds)
    if not reserve_gpu(session, task, step, token, lease_until):
        step.status = StepStatus.RETRY_WAIT
        step.next_retry_at = now + timedelta(seconds=get_settings().poll_interval_seconds)
        refresh_task(session, task)
        return None

    old_status = step.status
    step.status = StepStatus.RUNNING
    step.worker_id = worker_id
    step.lease_token = token
    step.lease_until = lease_until
    step.heartbeat_at = now
    step.started_at = step.started_at or now
    step.next_retry_at = None
    step.attempt_count += 1
    attempt = StepAttempt(
        step_id=step.id,
        attempt_no=step.attempt_count,
        worker_id=worker_id,
        lease_token=token,
        status=StepStatus.RUNNING,
        input_hash="0" * 64,
    )
    session.add(attempt)
    session.flush()
    add_event(
        session,
        task,
        "STEP_STARTED",
        step_id=step.id,
        attempt_id=attempt.id,
        old_status=old_status,
        new_status=step.status,
        details={"worker_id": worker_id},
    )
    refresh_task(session, task)
    return Claim(task.id, step.id, step.step_key, attempt.id, attempt.attempt_no, token)


def prepare_execution(session: Session, claim: Claim) -> tuple[dict, dict, str]:
    task = session.get(Task, claim.task_id)
    step = session.get(TaskStep, claim.step_id)
    assert task is not None and step is not None
    upstream = {
        candidate.step_key: candidate.output_json
        for candidate in session.scalars(select(TaskStep).where(TaskStep.task_id == task.id))
        if candidate.step_key in step.depends_on
    }
    fingerprint_data = {
        "task_input": task.input_json,
        "step_key": step.step_key,
        "upstream": upstream,
        "model_version": task.input_json.get("model_version", "mock-v1"),
        "handler_version": "mock-handler-v1",
    }
    input_hash = hashlib.sha256(
        json.dumps(fingerprint_data, sort_keys=True, separators=(",", ":")).encode()
    ).hexdigest()
    result = session.execute(
        update(TaskStep)
        .where(
            TaskStep.id == claim.step_id,
            TaskStep.lease_token == claim.lease_token,
            TaskStep.status == StepStatus.RUNNING,
        )
        .values(input_hash=input_hash)
    )
    if result.rowcount != 1:
        raise RuntimeError("Execution lease is no longer valid")
    attempt = session.get(StepAttempt, claim.attempt_id)
    assert attempt is not None
    attempt.input_hash = input_hash
    return task.input_json, upstream, input_hash


def find_cache(session: Session, input_hash: str) -> StepResultCache | None:
    cache = session.scalar(
        select(StepResultCache).where(
            StepResultCache.input_hash == input_hash,
            StepResultCache.status == StepStatus.SUCCEEDED,
            or_(
                StepResultCache.expires_at.is_(None), StepResultCache.expires_at > datetime.now(UTC)
            ),
        )
    )
    if cache and ArtifactStore.validate(cache.output_uri, cache.checksum):
        return cache
    if cache:
        session.delete(cache)
    return None


def renew_claim(session: Session, claim: Claim) -> bool:
    now = datetime.now(UTC)
    lease_until = now + timedelta(seconds=get_settings().lease_seconds)
    result = session.execute(
        update(TaskStep)
        .where(
            TaskStep.id == claim.step_id,
            TaskStep.lease_token == claim.lease_token,
            TaskStep.status == StepStatus.RUNNING,
        )
        .values(heartbeat_at=now, lease_until=lease_until)
    )
    if result.rowcount == 1:
        renew_gpu(session, claim.step_id, claim.lease_token, get_settings().lease_seconds)
        return True
    return False


def finish_success(
    session: Session,
    claim: Claim,
    output: dict,
    output_uri: str,
    checksum: str,
    *,
    cache_hit: bool = False,
) -> bool:
    step = session.scalar(select(TaskStep).where(TaskStep.id == claim.step_id).with_for_update())
    if step is None or step.lease_token != claim.lease_token or step.status != StepStatus.RUNNING:
        return False
    task = session.scalar(
        select(Task).where(Task.id == claim.task_id).options(selectinload(Task.steps))
    )
    attempt = session.get(StepAttempt, claim.attempt_id)
    assert task is not None and attempt is not None
    now = datetime.now(UTC)
    if task.status == TaskStatus.CANCEL_REQUESTED:
        step.status = StepStatus.CANCELED
        step.finished_at = now
        step.lease_until = None
        attempt.status = StepStatus.CANCELED
        attempt.finished_at = now
        attempt.duration_ms = max(0, int((now - attempt.started_at).total_seconds() * 1000))
        release_gpu(session, task, step, claim.lease_token)
        add_event(
            session,
            task,
            "STEP_CANCELED",
            step_id=step.id,
            attempt_id=attempt.id,
            old_status=StepStatus.RUNNING,
            new_status=step.status,
        )
        refresh_task(session, task)
        return True
    step.status = StepStatus.SUCCEEDED
    step.output_json = output
    step.finished_at = now
    step.lease_until = None
    step.heartbeat_at = now
    attempt.status = StepStatus.SUCCEEDED
    attempt.finished_at = now
    attempt.duration_ms = max(0, int((now - attempt.started_at).total_seconds() * 1000))
    attempt.output_uri = output_uri

    session.execute(
        pg_insert(StepResultCache)
        .values(
            input_hash=step.input_hash,
            status=StepStatus.SUCCEEDED,
            output_uri=output_uri,
            checksum=checksum,
        )
        .on_conflict_do_nothing(index_elements=[StepResultCache.input_hash])
    )
    release_gpu(session, task, step, claim.lease_token)
    add_event(
        session,
        task,
        "CACHE_HIT" if cache_hit else "STEP_SUCCEEDED",
        step_id=step.id,
        attempt_id=attempt.id,
        old_status=StepStatus.RUNNING,
        new_status=step.status,
    )
    refresh_task(session, task)
    return True


def finish_failure(
    session: Session, claim: Claim, code: str, message: str, retryable: bool
) -> bool:
    step = session.scalar(select(TaskStep).where(TaskStep.id == claim.step_id).with_for_update())
    if step is None or step.lease_token != claim.lease_token or step.status != StepStatus.RUNNING:
        return False
    task = session.scalar(
        select(Task).where(Task.id == claim.task_id).options(selectinload(Task.steps))
    )
    attempt = session.get(StepAttempt, claim.attempt_id)
    assert task is not None and attempt is not None
    now = datetime.now(UTC)
    attempt.status = StepStatus.FAILED
    attempt.finished_at = now
    attempt.duration_ms = max(0, int((now - attempt.started_at).total_seconds() * 1000))
    attempt.error_code = code
    attempt.error_message = message
    attempt.retryable = retryable
    old_status = step.status
    if retryable and step.attempt_count < step.max_attempts:
        delay = min(
            get_settings().retry_base_seconds * (2 ** (step.attempt_count - 1)),
            get_settings().retry_max_seconds,
        )
        step.status = StepStatus.RETRY_WAIT
        step.next_retry_at = now + timedelta(seconds=delay)
        event_type = "STEP_RETRY_SCHEDULED"
    else:
        step.status = StepStatus.FAILED
        step.finished_at = now
        event_type = "STEP_FAILED"
    step.lease_until = None
    release_gpu(session, task, step, claim.lease_token)
    add_event(
        session,
        task,
        event_type,
        step_id=step.id,
        attempt_id=attempt.id,
        old_status=old_status,
        new_status=step.status,
        details={"error_code": code, "retryable": retryable},
    )
    refresh_task(session, task)
    return True


def clear_resource_lease(session: Session, step_id: uuid.UUID) -> None:
    session.execute(delete(ResourceLease).where(ResourceLease.step_id == step_id))
