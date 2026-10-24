import os
import subprocess
import sys
import time
from concurrent.futures import ThreadPoolExecutor
from datetime import UTC, datetime, timedelta
from pathlib import Path

from fastapi.testclient import TestClient
from sqlalchemy import func, select
from sqlalchemy.orm import selectinload

from app.config import get_settings
from app.database import SessionLocal
from app.engine.status import StepStatus, TaskStatus
from app.main import app
from app.models import ResourceLease, StepAttempt, StepResultCache, Task, TaskEvent, TaskStep
from app.recovery import recover_expired_steps
from app.services.execution import claim_step, finish_failure, finish_success, prepare_execution
from app.services.tasks import create_task, request_cancel
from app.storage.artifacts import ArtifactStore
from app.workers import Worker, run_until_idle


def make_task(input_json: dict | None = None) -> Task:
    with SessionLocal.begin() as session:
        task, _ = create_task(
            session,
            task_type="animation",
            input_json=input_json or {"source": "mock://asset"},
            idempotency_key=None,
        )
        task_id = task.id
    with SessionLocal() as session:
        return session.get(Task, task_id)


def test_api_and_worker_complete_normal_workflow() -> None:
    client = TestClient(app)
    response = client.post(
        "/api/tasks",
        json={"input": {"source": "mock://normal"}, "idempotency_key": "normal-1"},
    )
    assert response.status_code == 201
    task_id = response.json()["id"]

    assert run_until_idle() == 6

    task_response = client.get(f"/api/tasks/{task_id}")
    assert task_response.status_code == 200
    assert task_response.json()["status"] == TaskStatus.SUCCEEDED
    assert task_response.json()["progress"] == 100
    assert len(client.get(f"/api/tasks/{task_id}/steps").json()) == 6
    assert client.get("/health").json()["database"] == "reachable"


def test_retry_history_is_preserved() -> None:
    task = make_task(
        {
            "source": "mock://retry",
            "mock": {"fail_times": {"transcode_video": 1}},
        }
    )
    run_until_idle(max_iterations=30)

    with SessionLocal() as session:
        persisted = session.get(Task, task.id)
        step = session.scalar(
            select(TaskStep).where(
                TaskStep.task_id == task.id,
                TaskStep.step_key == "transcode_video",
            )
        )
        attempts = list(
            session.scalars(
                select(StepAttempt)
                .where(StepAttempt.step_id == step.id)
                .order_by(StepAttempt.attempt_no)
            )
        )
        assert persisted.status == TaskStatus.SUCCEEDED
        assert [attempt.status for attempt in attempts] == [
            StepStatus.FAILED,
            StepStatus.SUCCEEDED,
        ]


def test_idempotent_submission_and_cross_task_cache_hits() -> None:
    client = TestClient(app)
    body = {"input": {"source": "mock://cached"}, "idempotency_key": "same-request"}
    first = client.post("/api/tasks", json=body)
    second = client.post("/api/tasks", json=body)
    assert first.status_code == 201
    assert second.status_code == 200
    assert first.json()["id"] == second.json()["id"]
    run_until_idle()

    third = client.post("/api/tasks", json={"input": body["input"]})
    assert third.status_code == 201
    run_until_idle()
    with SessionLocal() as session:
        cache_hits = session.scalar(
            select(func.count())
            .select_from(TaskEvent)
            .where(TaskEvent.task_id == third.json()["id"], TaskEvent.event_type == "CACHE_HIT")
        )
        assert cache_hits == 6


def test_concurrent_workers_only_claim_step_once() -> None:
    task = make_task({"source": "mock://concurrent"})

    def claim(worker_id: str):
        with SessionLocal.begin() as session:
            return claim_step(session, worker_id)

    with ThreadPoolExecutor(max_workers=2) as pool:
        claims = list(pool.map(claim, ["worker-a", "worker-b"]))

    valid_claims = [claim for claim in claims if claim is not None]
    assert len(valid_claims) == 1
    with SessionLocal() as session:
        attempts = session.scalar(
            select(func.count())
            .select_from(StepAttempt)
            .join(TaskStep)
            .where(TaskStep.task_id == task.id)
        )
        assert attempts == 1


def test_expired_lease_recovery_rejects_stale_worker_update() -> None:
    task = make_task({"source": "mock://recovery"})
    with SessionLocal.begin() as session:
        claim = claim_step(session, "old-worker")
    assert claim is not None
    with SessionLocal.begin() as session:
        _, _, input_hash = prepare_execution(session, claim)
        ArtifactStore().path_for(claim.step_key, input_hash).unlink(missing_ok=True)
        step = session.get(TaskStep, claim.step_id)
        step.lease_until = datetime.now(UTC) - timedelta(seconds=1)

    with SessionLocal.begin() as session:
        assert recover_expired_steps(session) == 1

    with SessionLocal.begin() as session:
        accepted = finish_success(session, claim, {"stale": True}, "missing", "bad")
    assert not accepted
    run_until_idle(max_iterations=30)
    with SessionLocal() as session:
        persisted = session.scalar(
            select(Task).where(Task.id == task.id).options(selectinload(Task.steps))
        )
        assert persisted.status == TaskStatus.SUCCEEDED
        validate_step = next(step for step in persisted.steps if step.step_key == "validate_asset")
        assert validate_step.attempt_count == 2


def test_non_retryable_failure_blocks_downstream() -> None:
    task = make_task(
        {
            "source": "mock://invalid",
            "mock": {
                "fail_times": {"validate_asset": 3},
                "error_types": {"validate_asset": "INVALID_INPUT"},
            },
        }
    )
    run_until_idle()
    with SessionLocal() as session:
        persisted = session.scalar(
            select(Task).where(Task.id == task.id).options(selectinload(Task.steps))
        )
        assert persisted.status == TaskStatus.FAILED
        assert persisted.progress == 0
        assert next(
            step for step in persisted.steps if step.step_key == "validate_asset"
        ).status == (StepStatus.FAILED)
        assert all(
            step.status == StepStatus.BLOCKED
            for step in persisted.steps
            if step.step_key != "validate_asset"
        )


def test_artifact_is_registered_after_worker_crash() -> None:
    make_task({"source": "mock://artifact-recovery"})
    with SessionLocal.begin() as session:
        claim = claim_step(session, "crashed-worker")
    assert claim is not None
    with SessionLocal.begin() as session:
        _, _, input_hash = prepare_execution(session, claim)

    output = {"step_key": claim.step_key, "recovered": True}
    ArtifactStore().publish(claim.step_key, input_hash, output)
    with SessionLocal.begin() as session:
        step = session.get(TaskStep, claim.step_id)
        step.lease_until = datetime.now(UTC) - timedelta(seconds=1)
        assert recover_expired_steps(session) == 1

    with SessionLocal() as session:
        step = session.get(TaskStep, claim.step_id)
        cache = session.scalar(
            select(StepResultCache).where(StepResultCache.input_hash == input_hash)
        )
        assert step.status == StepStatus.SUCCEEDED
        assert step.output_json == output
        assert cache is not None


def test_corrupt_orphan_artifact_is_not_registered() -> None:
    make_task({"source": "mock://corrupt-orphan"})
    with SessionLocal.begin() as session:
        claim = claim_step(session, "crashed-worker")
    assert claim is not None
    with SessionLocal.begin() as session:
        _, _, input_hash = prepare_execution(session, claim)

    uri, _ = ArtifactStore().publish(claim.step_key, input_hash, {"valid": True})
    with open(uri, "wb") as artifact:
        artifact.write(b'{"tampered": true}')
    with SessionLocal.begin() as session:
        step = session.get(TaskStep, claim.step_id)
        step.lease_until = datetime.now(UTC) - timedelta(seconds=1)
        assert recover_expired_steps(session) == 1

    with SessionLocal() as session:
        step = session.get(TaskStep, claim.step_id)
        cache = session.scalar(
            select(StepResultCache).where(StepResultCache.input_hash == input_hash)
        )
        assert step.status == StepStatus.RETRY_WAIT
        assert cache is None


def test_corrupt_cache_artifact_is_recomputed() -> None:
    source = {"source": "mock://corrupt-cache"}
    first = make_task(source)
    run_until_idle()
    with SessionLocal() as session:
        step = session.scalar(
            select(TaskStep).where(
                TaskStep.task_id == first.id,
                TaskStep.step_key == "validate_asset",
            )
        )
        cache = session.scalar(
            select(StepResultCache).where(StepResultCache.input_hash == step.input_hash)
        )
        with open(cache.output_uri, "wb") as artifact:
            artifact.write(b"corrupt")

    second = make_task(source)
    run_until_idle()
    with SessionLocal() as session:
        step = session.scalar(
            select(TaskStep).where(
                TaskStep.task_id == second.id,
                TaskStep.step_key == "validate_asset",
            )
        )
        events = list(
            session.scalars(
                select(TaskEvent).where(
                    TaskEvent.task_id == second.id,
                    TaskEvent.step_id == step.id,
                )
            )
        )
        assert any(event.event_type == "STEP_SUCCEEDED" for event in events)
        assert not any(event.event_type == "CACHE_HIT" for event in events)


def test_mock_gpu_capacity_wait_oom_and_release() -> None:
    settings = get_settings()
    original_capacity = settings.gpu_total_memory_mb
    settings.gpu_total_memory_mb = 1024
    try:
        waiting_task = make_task({"source": "mock://gpu-wait"})
        run_until_idle(max_iterations=20)
        with SessionLocal() as session:
            model_step = session.scalar(
                select(TaskStep).where(
                    TaskStep.task_id == waiting_task.id,
                    TaskStep.step_key == "model_inference",
                )
            )
            assert model_step.status == StepStatus.RETRY_WAIT
            assert session.scalar(select(func.count()).select_from(ResourceLease)) == 0
    finally:
        settings.gpu_total_memory_mb = original_capacity

    run_until_idle(max_iterations=30)
    oom_task = make_task({"source": "mock://gpu-oom", "mock": {"gpu_oom_step": "model_inference"}})
    run_until_idle(max_iterations=40)
    with SessionLocal() as session:
        assert session.get(Task, waiting_task.id).status == TaskStatus.SUCCEEDED
        assert session.get(Task, oom_task.id).status == TaskStatus.FAILED
        oom_step = session.scalar(
            select(TaskStep).where(
                TaskStep.task_id == oom_task.id,
                TaskStep.step_key == "model_inference",
            )
        )
        assert oom_step.attempt_count == oom_step.max_attempts == 3
        assert session.scalar(select(func.count()).select_from(ResourceLease)) == 0


def test_cancel_queued_task() -> None:
    client = TestClient(app)
    created = client.post("/api/tasks", json={"input": {"source": "mock://cancel"}})
    canceled = client.post(f"/api/tasks/{created.json()['id']}/cancel")
    assert canceled.status_code == 200
    assert canceled.json()["status"] == TaskStatus.CANCELED
    steps = client.get(f"/api/tasks/{created.json()['id']}/steps").json()
    assert all(step["status"] == StepStatus.CANCELED for step in steps)


def test_running_step_cooperates_with_cancel_request() -> None:
    task = make_task({"source": "mock://cancel-running"})
    with SessionLocal.begin() as session:
        claim = claim_step(session, "cancel-worker")
    assert claim is not None
    with SessionLocal.begin() as session:
        _, _, input_hash = prepare_execution(session, claim)
        persisted = session.scalar(
            select(Task).where(Task.id == task.id).options(selectinload(Task.steps))
        )
        request_cancel(session, persisted)
    uri, checksum = ArtifactStore().publish(claim.step_key, input_hash, {"unused": True})
    with SessionLocal.begin() as session:
        assert finish_success(session, claim, {"unused": True}, uri, checksum)
    with SessionLocal() as session:
        persisted = session.get(Task, task.id)
        step = session.get(TaskStep, claim.step_id)
        assert persisted.status == TaskStatus.CANCELED
        assert step.status == StepStatus.CANCELED


def test_failed_handler_after_cancel_is_recorded_as_canceled() -> None:
    task = make_task({"source": "mock://cancel-failure"})
    with SessionLocal.begin() as session:
        claim = claim_step(session, "cancel-worker")
    assert claim is not None
    with SessionLocal.begin() as session:
        persisted = session.scalar(
            select(Task).where(Task.id == task.id).options(selectinload(Task.steps))
        )
        request_cancel(session, persisted)
    with SessionLocal.begin() as session:
        assert finish_failure(session, claim, "TEMPORARY", "late failure", True)
    with SessionLocal() as session:
        persisted = session.get(Task, task.id)
        step = session.get(TaskStep, claim.step_id)
        attempt = session.get(StepAttempt, claim.attempt_id)
        assert persisted.status == TaskStatus.CANCELED
        assert step.status == StepStatus.CANCELED
        assert attempt.status == StepStatus.CANCELED


def test_expired_running_step_is_canceled_when_task_was_canceled() -> None:
    task = make_task({"source": "mock://cancel-recovery"})
    with SessionLocal.begin() as session:
        claim = claim_step(session, "lost-worker")
    assert claim is not None
    with SessionLocal.begin() as session:
        persisted = session.scalar(
            select(Task).where(Task.id == task.id).options(selectinload(Task.steps))
        )
        request_cancel(session, persisted)
        step = session.get(TaskStep, claim.step_id)
        step.lease_until = datetime.now(UTC) - timedelta(seconds=1)
    with SessionLocal.begin() as session:
        assert recover_expired_steps(session) == 1
    with SessionLocal() as session:
        assert session.get(Task, task.id).status == TaskStatus.CANCELED
        assert session.get(TaskStep, claim.step_id).status == StepStatus.CANCELED


def test_manual_retry_keeps_attempt_numbers_monotonic() -> None:
    client = TestClient(app)
    created = client.post(
        "/api/tasks",
        json={
            "input": {
                "source": "mock://manual-retry",
                "mock": {
                    "fail_times": {"validate_asset": 1},
                    "error_types": {"validate_asset": "INVALID_INPUT"},
                },
            }
        },
    )
    task_id = created.json()["id"]
    run_until_idle()
    assert client.get(f"/api/tasks/{task_id}").json()["status"] == TaskStatus.FAILED

    retried = client.post(f"/api/tasks/{task_id}/retry")
    assert retried.status_code == 200
    run_until_idle()
    attempts = client.get(f"/api/tasks/{task_id}/steps/validate_asset/attempts").json()
    assert [attempt["attempt_no"] for attempt in attempts] == [1, 2]
    assert client.get(f"/api/tasks/{task_id}").json()["status"] == TaskStatus.SUCCEEDED


def test_heartbeat_renews_long_running_step_lease() -> None:
    settings = get_settings()
    original_lease_seconds = settings.lease_seconds
    settings.lease_seconds = 0.3
    try:
        task = make_task(
            {
                "source": "mock://heartbeat",
                "mock": {"sleep_seconds": {"validate_asset": 0.8}},
            }
        )
        with ThreadPoolExecutor(max_workers=1) as pool:
            future = pool.submit(Worker("heartbeat-worker", allow_process_exit=False).run_once)
            time.sleep(0.5)
            with SessionLocal() as session:
                step = session.scalar(
                    select(TaskStep).where(
                        TaskStep.task_id == task.id,
                        TaskStep.step_key == "validate_asset",
                    )
                )
                assert step.status == StepStatus.RUNNING
                assert step.heartbeat_at is not None
                assert step.lease_until > datetime.now(UTC)
            assert future.result(timeout=3)
    finally:
        settings.lease_seconds = original_lease_seconds


def test_expired_mock_gpu_lease_is_reclaimed() -> None:
    make_task({"source": "mock://gpu-crash"})
    worker = Worker("setup-worker", allow_process_exit=False)
    assert worker.run_once()
    assert worker.run_once()
    assert worker.run_once()
    with SessionLocal.begin() as session:
        claim = claim_step(session, "lost-gpu-worker")
    assert claim is not None and claim.step_key == "model_inference"
    with SessionLocal.begin() as session:
        step = session.get(TaskStep, claim.step_id)
        step.lease_until = datetime.now(UTC) - timedelta(seconds=1)
        lease = session.scalar(select(ResourceLease).where(ResourceLease.step_id == claim.step_id))
        lease.expires_at = datetime.now(UTC) - timedelta(seconds=1)
    with SessionLocal.begin() as session:
        assert recover_expired_steps(session) == 1
    with SessionLocal() as session:
        assert session.scalar(select(func.count()).select_from(ResourceLease)) == 0


def test_terminated_worker_process_is_recovered() -> None:
    task = make_task(
        {
            "source": "mock://terminated-process",
            "mock": {"sleep_seconds": {"validate_asset": 10}},
        }
    )
    environment = os.environ.copy()
    environment.update({"LEASE_SECONDS": "0.5", "POLL_INTERVAL_SECONDS": "0.05"})
    process = subprocess.Popen(
        [sys.executable, "-m", "app.worker"],
        cwd=Path(__file__).parents[2],
        env=environment,
        stdout=subprocess.DEVNULL,
        stderr=subprocess.DEVNULL,
    )
    try:
        deadline = time.monotonic() + 5
        while time.monotonic() < deadline:
            with SessionLocal() as session:
                step = session.scalar(
                    select(TaskStep).where(
                        TaskStep.task_id == task.id,
                        TaskStep.step_key == "validate_asset",
                    )
                )
                if step.status == StepStatus.RUNNING:
                    break
            time.sleep(0.05)
        else:
            raise AssertionError("Worker did not claim the step")
    finally:
        process.terminate()
        process.wait(timeout=5)

    with SessionLocal.begin() as session:
        persisted = session.get(Task, task.id)
        persisted.input_json = {"source": "mock://terminated-process"}
    time.sleep(0.6)
    run_until_idle(max_iterations=40, worker_id="replacement-worker")

    with SessionLocal() as session:
        persisted = session.get(Task, task.id)
        step = session.scalar(
            select(TaskStep).where(
                TaskStep.task_id == task.id,
                TaskStep.step_key == "validate_asset",
            )
        )
        assert persisted.status == TaskStatus.SUCCEEDED
        assert step.attempt_count == 2
