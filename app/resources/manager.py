import uuid
from datetime import UTC, datetime, timedelta

from sqlalchemy import delete, func, select, text
from sqlalchemy.orm import Session

from app.config import get_settings
from app.models import ResourceLease, Task, TaskStep
from app.services.state import add_event

RESOURCE_LOCK_ID = 72819431


def reserve_gpu(
    session: Session,
    task: Task,
    step: TaskStep,
    lease_token: uuid.UUID,
    lease_until: datetime,
) -> bool:
    required = int(step.resource_requirements.get("gpu_memory_mb", 0))
    if required <= 0:
        return True

    session.execute(text("SELECT pg_advisory_xact_lock(:lock_id)"), {"lock_id": RESOURCE_LOCK_ID})
    session.execute(delete(ResourceLease).where(ResourceLease.expires_at < datetime.now(UTC)))
    allocated = session.scalar(select(func.coalesce(func.sum(ResourceLease.gpu_memory_mb), 0)))
    if allocated + required > get_settings().gpu_total_memory_mb:
        add_event(
            session,
            task,
            "RESOURCE_WAIT",
            step_id=step.id,
            details={"resource": "MOCK_GPU", "required_memory_mb": required},
        )
        return False

    session.add(
        ResourceLease(
            step_id=step.id,
            lease_token=lease_token,
            gpu_memory_mb=required,
            expires_at=lease_until,
        )
    )
    add_event(
        session,
        task,
        "RESOURCE_ALLOCATED",
        step_id=step.id,
        details={"resource": "MOCK_GPU", "memory_mb": required},
    )
    return True


def renew_gpu(session: Session, step_id: uuid.UUID, lease_token: uuid.UUID, seconds: float) -> None:
    lease = session.scalar(
        select(ResourceLease).where(
            ResourceLease.step_id == step_id,
            ResourceLease.lease_token == lease_token,
        )
    )
    if lease:
        lease.expires_at = datetime.now(UTC) + timedelta(seconds=seconds)


def release_gpu(session: Session, task: Task, step: TaskStep, lease_token: uuid.UUID) -> None:
    lease = session.scalar(
        select(ResourceLease).where(
            ResourceLease.step_id == step.id,
            ResourceLease.lease_token == lease_token,
        )
    )
    if lease:
        memory_mb = lease.gpu_memory_mb
        session.delete(lease)
        add_event(
            session,
            task,
            "RESOURCE_RELEASED",
            step_id=step.id,
            details={"resource": "MOCK_GPU", "memory_mb": memory_mb},
        )


def reclaim_expired_resources(session: Session) -> int:
    result = session.execute(
        delete(ResourceLease).where(ResourceLease.expires_at < datetime.now(UTC))
    )
    return result.rowcount
