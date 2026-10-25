import uuid
from datetime import datetime
from typing import Any

from pydantic import BaseModel, ConfigDict, Field


class TaskCreate(BaseModel):
    task_type: str = "animation"
    input: dict[str, Any]
    idempotency_key: str | None = Field(default=None, max_length=255)


class TaskRead(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: uuid.UUID
    task_no: str
    task_type: str
    status: str
    progress: int
    input_json: dict[str, Any]
    result_json: dict[str, Any] | None
    error_code: str | None
    error_message: str | None
    created_at: datetime
    started_at: datetime | None
    finished_at: datetime | None


class TaskListRead(BaseModel):
    items: list[TaskRead]
    total: int
    limit: int
    offset: int
    status_counts: dict[str, int]


class StepRead(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: uuid.UUID
    step_key: str
    step_name: str
    status: str
    depends_on: list[str]
    resource_requirements: dict[str, Any]
    output_json: dict[str, Any] | None
    attempt_count: int
    max_attempts: int
    worker_id: str | None
    lease_until: datetime | None
    started_at: datetime | None
    finished_at: datetime | None


class AttemptRead(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: uuid.UUID
    attempt_no: int
    worker_id: str
    status: str
    started_at: datetime
    finished_at: datetime | None
    duration_ms: int | None
    error_code: str | None
    error_message: str | None
    retryable: bool | None
    output_uri: str | None


class EventRead(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: int
    step_id: uuid.UUID | None
    attempt_id: uuid.UUID | None
    event_type: str
    old_status: str | None
    new_status: str | None
    details_json: dict[str, Any]
    created_at: datetime
