import hashlib
import json
import os
import tempfile
from pathlib import Path
from typing import Any

from app.config import get_settings


class ArtifactStore:
    def __init__(self, root: Path | None = None) -> None:
        self.root = (root or get_settings().artifact_root).resolve()
        self.root.mkdir(parents=True, exist_ok=True)

    def path_for(self, step_key: str, input_hash: str) -> Path:
        return self.root / step_key / f"{input_hash}.json"

    def publish(self, step_key: str, input_hash: str, content: dict[str, Any]) -> tuple[str, str]:
        target = self.path_for(step_key, input_hash)
        target.parent.mkdir(parents=True, exist_ok=True)
        payload = json.dumps(content, sort_keys=True, ensure_ascii=False).encode("utf-8")
        checksum = hashlib.sha256(payload).hexdigest()
        descriptor, temporary_name = tempfile.mkstemp(dir=target.parent, suffix=".tmp")
        try:
            with os.fdopen(descriptor, "wb") as temporary:
                temporary.write(payload)
                temporary.flush()
                os.fsync(temporary.fileno())
            os.replace(temporary_name, target)
        finally:
            if os.path.exists(temporary_name):
                os.unlink(temporary_name)
        return str(target), checksum

    @staticmethod
    def validate(uri: str, checksum: str) -> bool:
        path = Path(uri)
        if not path.is_file():
            return False
        return hashlib.sha256(path.read_bytes()).hexdigest() == checksum

    @staticmethod
    def checksum(uri: str) -> str:
        return hashlib.sha256(Path(uri).read_bytes()).hexdigest()

    @staticmethod
    def read(uri: str) -> dict[str, Any]:
        return json.loads(Path(uri).read_text(encoding="utf-8"))
