import logging
import os
import socket
import threading
import time
import uuid

from app.config import get_settings
from app.database import SessionLocal
from app.handlers import HandlerError, SimulatedCrash, run_mock_handler
from app.recovery import recover_expired_steps
from app.services.execution import (
    Claim,
    claim_step,
    find_cache,
    finish_failure,
    finish_success,
    prepare_execution,
    renew_claim,
)
from app.storage.artifacts import ArtifactStore
from app.workers.wakeup import IdleBackoff, PostgresWakeup

logger = logging.getLogger(__name__)


class Worker:
    def __init__(self, worker_id: str | None = None, *, allow_process_exit: bool = True) -> None:
        self.worker_id = worker_id or f"{socket.gethostname()}-{os.getpid()}-{uuid.uuid4().hex[:6]}"
        self.allow_process_exit = allow_process_exit
        self.store = ArtifactStore()

    def _heartbeat(self, claim: Claim, stopped: threading.Event) -> None:
        interval = max(0.1, get_settings().lease_seconds / 3)
        while not stopped.wait(interval):
            with SessionLocal.begin() as session:
                if not renew_claim(session, claim):
                    return

    def run_once(self) -> bool:
        with SessionLocal.begin() as session:
            recover_expired_steps(session)
            claim = claim_step(session, self.worker_id)
        if claim is None:
            return False

        stopped = threading.Event()
        heartbeat = threading.Thread(target=self._heartbeat, args=(claim, stopped), daemon=True)
        heartbeat.start()
        try:
            with SessionLocal.begin() as session:
                task_input, upstream, input_hash = prepare_execution(session, claim)
                cache = find_cache(session, input_hash, self.store)
                if cache:
                    output = self.store.read(cache.output_uri)
                    return finish_success(
                        session,
                        claim,
                        output,
                        cache.output_uri,
                        cache.checksum,
                        cache_hit=True,
                    )

            output = run_mock_handler(claim.step_key, task_input, upstream, claim.attempt_no)
            output_uri, checksum = self.store.publish(claim.step_key, input_hash, output)
            if task_input.get("mock", {}).get("crash_after_artifact_step") == claim.step_key:
                if self.allow_process_exit:
                    os._exit(70)
                raise SimulatedCrash("Injected crash after artifact publication")
            with SessionLocal.begin() as session:
                return finish_success(session, claim, output, output_uri, checksum)
        except SimulatedCrash:
            logger.warning("simulated_worker_crash", extra={"step_key": claim.step_key})
            return False
        except HandlerError as exc:
            with SessionLocal.begin() as session:
                return finish_failure(session, claim, exc.code, exc.message, exc.retryable)
        except Exception as exc:
            logger.exception("unexpected_step_failure", extra={"step_key": claim.step_key})
            with SessionLocal.begin() as session:
                return finish_failure(session, claim, "INTERNAL_ERROR", str(exc), True)
        finally:
            stopped.set()
            heartbeat.join(timeout=1)

    def run_forever(self) -> None:
        logger.info("worker_started", extra={"worker_id": self.worker_id})
        settings = get_settings()
        backoff = IdleBackoff(
            initial_seconds=settings.poll_interval_seconds,
            max_seconds=settings.worker_idle_backoff_max_seconds,
            multiplier=settings.worker_idle_backoff_multiplier,
        )
        wakeup = PostgresWakeup()
        try:
            while True:
                if self.run_once():
                    backoff.reset()
                    continue
                if wakeup.wait(backoff.next_delay()):
                    backoff.reset()
        finally:
            wakeup.close()


def run_until_idle(max_iterations: int = 100, worker_id: str = "test-worker") -> int:
    worker = Worker(worker_id=worker_id, allow_process_exit=False)
    processed = 0
    idle_rounds = 0
    for _ in range(max_iterations):
        if worker.run_once():
            processed += 1
            idle_rounds = 0
        else:
            idle_rounds += 1
            if idle_rounds >= 3:
                break
            time.sleep(get_settings().poll_interval_seconds)
    return processed
