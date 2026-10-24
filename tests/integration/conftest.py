import pytest
from sqlalchemy import delete

from app.database import SessionLocal
from app.models import ResourceLease, StepResultCache, Task


@pytest.fixture(autouse=True)
def clean_pipeline_data():
    with SessionLocal.begin() as session:
        session.execute(delete(ResourceLease))
        session.execute(delete(Task))
        session.execute(delete(StepResultCache))
    yield
    with SessionLocal.begin() as session:
        session.execute(delete(ResourceLease))
        session.execute(delete(Task))
        session.execute(delete(StepResultCache))
