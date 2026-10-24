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

    @staticmethod
    def checksum_path(uri: str | Path) -> Path:
        return Path(f"{uri}.sha256")

    def publish(self, step_key: str, input_hash: str, content: dict[str, Any]) -> tuple[str, str]:
        target = self.path_for(step_key, input_hash)
        target.parent.mkdir(parents=True, exist_ok=True)
        payload = json.dumps(content, sort_keys=True, ensure_ascii=False).encode("utf-8")
        checksum = hashlib.sha256(payload).hexdigest()
        descriptor, temporary_name = tempfile.mkstemp(dir=target.parent, suffix=".tmp")
        checksum_descriptor, checksum_temporary_name = tempfile.mkstemp(
            dir=target.parent, suffix=".sha256.tmp"
        )
        try:
            with os.fdopen(descriptor, "wb") as temporary:
                temporary.write(payload)
                temporary.flush()
                os.fsync(temporary.fileno())
            with os.fdopen(checksum_descriptor, "w", encoding="ascii") as checksum_temporary:
                checksum_temporary.write(checksum)
                checksum_temporary.flush()
                os.fsync(checksum_temporary.fileno())
            os.replace(temporary_name, target)
            os.replace(checksum_temporary_name, self.checksum_path(target))
        finally:
            if os.path.exists(temporary_name):
                os.unlink(temporary_name)
            if os.path.exists(checksum_temporary_name):
                os.unlink(checksum_temporary_name)
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

    @classmethod
    def published_checksum(cls, uri: str | Path) -> str | None:
        checksum_path = cls.checksum_path(uri)
        if not Path(uri).is_file() or not checksum_path.is_file():
            return None
        expected = checksum_path.read_text(encoding="ascii").strip()
        if len(expected) != 64 or cls.checksum(str(uri)) != expected:
            return None
        return expected

    @staticmethod
    def read(uri: str) -> dict[str, Any]:
        return json.loads(Path(uri).read_text(encoding="utf-8"))
