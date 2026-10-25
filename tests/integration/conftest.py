import pytest
from sqlalchemy import delete

from app.config import get_settings
from app.database import SessionLocal
from app.models import ResourceLease, StepResultCache, Task


@pytest.fixture(autouse=True)
def clean_pipeline_data(tmp_path, monkeypatch):
    settings = get_settings()
    original_artifact_root = settings.artifact_root
    test_artifact_root = tmp_path / "artifacts"
    settings.artifact_root = test_artifact_root
    monkeypatch.setenv("ARTIFACT_ROOT", str(test_artifact_root))
    with SessionLocal.begin() as session:
        session.execute(delete(ResourceLease))
        session.execute(delete(Task))
        session.execute(delete(StepResultCache))
    yield
    with SessionLocal.begin() as session:
        session.execute(delete(ResourceLease))
        session.execute(delete(Task))
        session.execute(delete(StepResultCache))
    settings.artifact_root = original_artifact_root
