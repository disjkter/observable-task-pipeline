from sqlalchemy import inspect, text

from app.database import create_database_engine

CORE_TABLES = {
    "tasks",
    "task_steps",
    "step_attempts",
    "task_events",
    "step_result_cache",
}


def test_database_is_at_expected_revision() -> None:
    engine = create_database_engine()
    with engine.connect() as connection:
        revision = connection.execute(text("SELECT version_num FROM alembic_version")).scalar_one()

    assert revision == "20261024_0002"


def test_migration_created_core_tables_and_constraints() -> None:
    engine = create_database_engine()
    inspector = inspect(engine)

    assert CORE_TABLES <= set(inspector.get_table_names())

    step_unique = {
        tuple(constraint["column_names"])
        for constraint in inspector.get_unique_constraints("task_steps")
    }
    attempt_unique = {
        tuple(constraint["column_names"])
        for constraint in inspector.get_unique_constraints("step_attempts")
    }
    cache_unique = {
        tuple(constraint["column_names"])
        for constraint in inspector.get_unique_constraints("step_result_cache")
    }

    assert ("task_id", "step_key") in step_unique
    assert ("step_id", "attempt_no") in attempt_unique
    assert ("input_hash",) in cache_unique


def test_claim_and_timeline_indexes_exist() -> None:
    engine = create_database_engine()
    inspector = inspect(engine)

    step_indexes = {index["name"] for index in inspector.get_indexes("task_steps")}
    event_indexes = {index["name"] for index in inspector.get_indexes("task_events")}

    assert "ix_task_steps_claimable" in step_indexes
    assert "ix_task_steps_task_status" in step_indexes
    assert "ix_task_events_task_created_at" in event_indexes
