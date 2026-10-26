import json
import uuid

from sqlalchemy import text
from sqlalchemy.orm import Session

from app.config import get_settings


def notify_task_ready(session: Session, task_id: uuid.UUID, reason: str) -> None:
    """Queue a transactional PostgreSQL notification after the surrounding commit."""
    settings = get_settings()
    if not settings.task_notify_enabled:
        return
    payload = json.dumps(
        {"task_id": str(task_id), "reason": reason},
        separators=(",", ":"),
    )
    session.execute(
        text("SELECT pg_notify(:channel, :payload)"),
        {"channel": settings.task_notify_channel, "payload": payload},
    )
