import time
from dataclasses import dataclass
from typing import Any


@dataclass
class HandlerError(Exception):
    code: str
    message: str
    retryable: bool

    def __str__(self) -> str:
        return self.message


class SimulatedCrash(Exception):
    pass


def run_mock_handler(
    step_key: str,
    task_input: dict[str, Any],
    upstream_outputs: dict[str, Any],
    attempt_no: int,
) -> dict[str, Any]:
    mock = task_input.get("mock", {})
    sleep_seconds = float(mock.get("sleep_seconds", {}).get(step_key, 0))
    if sleep_seconds > 0:
        time.sleep(sleep_seconds)

    fail_times = int(mock.get("fail_times", {}).get(step_key, 0))
    if attempt_no <= fail_times:
        error_kind = mock.get("error_types", {}).get(step_key, "TEMPORARY")
        retryable = error_kind not in {"INVALID_INPUT", "MISSING_INPUT"}
        raise HandlerError(error_kind, f"Injected {error_kind} failure", retryable)

    if mock.get("gpu_oom_step") == step_key:
        raise HandlerError("GPU_OOM", "Injected MOCK GPU out-of-memory", True)

    return {
        "step_key": step_key,
        "source": task_input.get("source", "mock://asset"),
        "upstream_steps": sorted(upstream_outputs),
        "attempt_no": attempt_no,
        "content": mock.get("output_content", {}).get(step_key, f"mock output for {step_key}"),
    }
