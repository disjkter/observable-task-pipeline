from sqlalchemy import UniqueConstraint
from sqlalchemy.dialects import postgresql
from sqlalchemy.schema import CreateTable

from app.database import Base
from app.models import StepAttempt, StepResultCache, Task, TaskEvent, TaskStep


def test_core_tables_are_registered() -> None:
    assert {
        "tasks",
        "task_steps",
        "step_attempts",
        "task_events",
        "step_result_cache",
    } <= set(Base.metadata.tables)


def test_required_unique_constraints_exist() -> None:
    step_unique_columns = {
        tuple(constraint.columns.keys())
        for constraint in TaskStep.__table__.constraints
        if isinstance(constraint, UniqueConstraint)
    }
    attempt_unique_columns = {
        tuple(constraint.columns.keys())
        for constraint in StepAttempt.__table__.constraints
        if isinstance(constraint, UniqueConstraint)
    }
    assert ("task_id", "step_key") in step_unique_columns
    assert ("step_id", "attempt_no") in attempt_unique_columns
    assert Task.__table__.c.task_no.unique
    assert Task.__table__.c.idempotency_key.unique
    assert StepResultCache.__table__.c.input_hash.unique


def test_foreign_keys_and_delete_policies() -> None:
    assert next(iter(TaskStep.__table__.c.task_id.foreign_keys)).ondelete == "CASCADE"
    assert next(iter(StepAttempt.__table__.c.step_id.foreign_keys)).ondelete == "CASCADE"
    assert next(iter(TaskEvent.__table__.c.step_id.foreign_keys)).ondelete == "SET NULL"
    assert next(iter(TaskEvent.__table__.c.attempt_id.foreign_keys)).ondelete == "SET NULL"


def test_all_tables_compile_for_postgresql() -> None:
    dialect = postgresql.dialect()
    for table in Base.metadata.sorted_tables:
        ddl = str(CreateTable(table).compile(dialect=dialect))
        assert f"CREATE TABLE {table.name}" in ddl
