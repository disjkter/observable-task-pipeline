import logging
import time
from dataclasses import dataclass

import psycopg
from psycopg import sql
from sqlalchemy.engine import make_url

from app.config import get_settings

logger = logging.getLogger(__name__)


@dataclass
class IdleBackoff:
    initial_seconds: float
    max_seconds: float
    multiplier: float

    def __post_init__(self) -> None:
        self.initial_seconds = max(0.01, self.initial_seconds)
        self.max_seconds = max(self.initial_seconds, self.max_seconds)
        self.multiplier = max(1.0, self.multiplier)
        self.current_seconds = self.initial_seconds

    def next_delay(self) -> float:
        delay = self.current_seconds
        self.current_seconds = min(self.current_seconds * self.multiplier, self.max_seconds)
        return delay

    def reset(self) -> None:
        self.current_seconds = self.initial_seconds


class PostgresWakeup:
    """Best-effort LISTEN/NOTIFY wakeup with timed polling as the correctness fallback."""

    def __init__(self) -> None:
        settings = get_settings()
        self.enabled = settings.task_notify_enabled
        self.channel = settings.task_notify_channel
        self.database_url = str(
            make_url(settings.database_url)
            .set(drivername="postgresql")
            .render_as_string(hide_password=False)
        )
        self.connection: psycopg.Connection | None = None

    def open(self) -> None:
        if not self.enabled or self.connection is not None:
            return
        connection = psycopg.connect(self.database_url, autocommit=True)
        connection.execute(sql.SQL("LISTEN {}").format(sql.Identifier(self.channel)))
        self.connection = connection

    def wait(self, timeout_seconds: float) -> bool:
        if not self.enabled:
            time.sleep(timeout_seconds)
            return False
        try:
            self.open()
            assert self.connection is not None
            notification = next(
                self.connection.notifies(timeout=timeout_seconds, stop_after=1),
                None,
            )
            return notification is not None
        except Exception:
            logger.warning("task_notification_wait_failed", exc_info=True)
            self.close()
            time.sleep(timeout_seconds)
            return False

    def close(self) -> None:
        if self.connection is not None:
            self.connection.close()
            self.connection = None
