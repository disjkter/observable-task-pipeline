from dataclasses import dataclass, field
from typing import Any


@dataclass(frozen=True)
class StepDefinition:
    key: str
    name: str
    depends_on: tuple[str, ...] = ()
    resources: dict[str, Any] = field(default_factory=lambda: {"cpu": 1})
    max_attempts: int = 3


ANIMATION_WORKFLOW = (
    StepDefinition("validate_asset", "Validate asset"),
    StepDefinition("transcode_video", "Transcode video", ("validate_asset",)),
    StepDefinition("extract_keyframes", "Extract keyframes", ("transcode_video",)),
    StepDefinition(
        "model_inference",
        "Model inference",
        ("extract_keyframes",),
        {"gpu": 1, "gpu_memory_mb": 4096},
    ),
    StepDefinition("generate_report", "Generate report", ("model_inference",)),
    StepDefinition(
        "package_result",
        "Package result",
        ("transcode_video", "generate_report"),
    ),
)

WORKFLOWS = {"animation": ANIMATION_WORKFLOW}


def get_workflow(task_type: str) -> tuple[StepDefinition, ...]:
    try:
        return WORKFLOWS[task_type]
    except KeyError as exc:
        raise ValueError(f"Unsupported task type: {task_type}") from exc
