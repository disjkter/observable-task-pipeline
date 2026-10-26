import hashlib
import json
import os
import tempfile
from pathlib import Path
from typing import Any, Protocol
from urllib.parse import quote, unquote, urlparse

from app.config import get_settings


class ArtifactBackend(Protocol):
    def uri_for(self, step_key: str, input_hash: str) -> str: ...

    def publish(
        self, step_key: str, input_hash: str, content: dict[str, Any]
    ) -> tuple[str, str]: ...

    def validate(self, uri: str, checksum: str) -> bool: ...

    def published_checksum(self, uri: str) -> str | None: ...

    def read(self, uri: str) -> dict[str, Any]: ...


def _payload(content: dict[str, Any]) -> bytes:
    return json.dumps(content, sort_keys=True, ensure_ascii=False).encode("utf-8")


class LocalArtifactStore:
    def __init__(self, root: Path) -> None:
        self.root = root.resolve()
        self.root.mkdir(parents=True, exist_ok=True)

    def path_for(self, step_key: str, input_hash: str) -> Path:
        return self.root / step_key / f"{input_hash}.json"

    def uri_for(self, step_key: str, input_hash: str) -> str:
        return str(self.path_for(step_key, input_hash))

    @staticmethod
    def checksum_path(uri: str | Path) -> Path:
        return Path(f"{uri}.sha256")

    def publish(self, step_key: str, input_hash: str, content: dict[str, Any]) -> tuple[str, str]:
        target = self.path_for(step_key, input_hash)
        target.parent.mkdir(parents=True, exist_ok=True)
        payload = _payload(content)
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

    def validate(self, uri: str, checksum: str) -> bool:
        path = Path(uri)
        if not path.is_file():
            return False
        return hashlib.sha256(path.read_bytes()).hexdigest() == checksum

    def published_checksum(self, uri: str) -> str | None:
        checksum_path = self.checksum_path(uri)
        if not Path(uri).is_file() or not checksum_path.is_file():
            return None
        expected = checksum_path.read_text(encoding="ascii").strip()
        if len(expected) != 64 or not self.validate(uri, expected):
            return None
        return expected

    def read(self, uri: str) -> dict[str, Any]:
        return json.loads(Path(uri).read_text(encoding="utf-8"))


class S3ArtifactStore:
    def __init__(self, *, bucket: str, prefix: str = "", client=None) -> None:
        if not bucket:
            raise ValueError("ARTIFACT_S3_BUCKET is required when ARTIFACT_BACKEND=s3")
        self.bucket = bucket
        self.prefix = prefix.strip("/")
        self.client = client or self._create_client()

    @staticmethod
    def _create_client():
        try:
            import boto3
        except ImportError as exc:
            raise RuntimeError('S3 artifact support requires `pip install -e ".[s3]"`') from exc

        settings = get_settings()
        kwargs: dict[str, Any] = {}
        if settings.artifact_s3_endpoint_url:
            kwargs["endpoint_url"] = settings.artifact_s3_endpoint_url
        if settings.artifact_s3_region:
            kwargs["region_name"] = settings.artifact_s3_region
        if settings.artifact_s3_access_key_id:
            kwargs["aws_access_key_id"] = settings.artifact_s3_access_key_id
        if settings.artifact_s3_secret_access_key:
            kwargs["aws_secret_access_key"] = (
                settings.artifact_s3_secret_access_key.get_secret_value()
            )
        return boto3.client("s3", **kwargs)

    def _key_for(self, step_key: str, input_hash: str) -> str:
        key = f"{quote(step_key, safe='')}/{input_hash}.json"
        return f"{self.prefix}/{key}" if self.prefix else key

    def uri_for(self, step_key: str, input_hash: str) -> str:
        return f"s3://{self.bucket}/{self._key_for(step_key, input_hash)}"

    def _parse_uri(self, uri: str) -> tuple[str, str]:
        parsed = urlparse(uri)
        if parsed.scheme != "s3" or not parsed.netloc or not parsed.path:
            raise ValueError(f"Invalid S3 artifact URI: {uri}")
        if parsed.netloc != self.bucket:
            raise ValueError(f"Artifact URI belongs to unexpected bucket: {parsed.netloc}")
        return parsed.netloc, unquote(parsed.path.lstrip("/"))

    def _read_bytes(self, uri: str) -> bytes:
        bucket, key = self._parse_uri(uri)
        response = self.client.get_object(Bucket=bucket, Key=key)
        return response["Body"].read()

    def publish(self, step_key: str, input_hash: str, content: dict[str, Any]) -> tuple[str, str]:
        payload = _payload(content)
        checksum = hashlib.sha256(payload).hexdigest()
        uri = self.uri_for(step_key, input_hash)
        bucket, key = self._parse_uri(uri)
        self.client.put_object(
            Bucket=bucket,
            Key=key,
            Body=payload,
            ContentType="application/json",
            Metadata={"sha256": checksum},
        )
        self.client.put_object(
            Bucket=bucket,
            Key=f"{key}.sha256",
            Body=checksum.encode("ascii"),
            ContentType="text/plain",
        )
        return uri, checksum

    def validate(self, uri: str, checksum: str) -> bool:
        try:
            return hashlib.sha256(self._read_bytes(uri)).hexdigest() == checksum
        except Exception:
            return False

    def published_checksum(self, uri: str) -> str | None:
        try:
            expected = self._read_bytes(f"{uri}.sha256").decode("ascii").strip()
        except Exception:
            return None
        if len(expected) != 64 or not self.validate(uri, expected):
            return None
        return expected

    def read(self, uri: str) -> dict[str, Any]:
        return json.loads(self._read_bytes(uri).decode("utf-8"))


class ArtifactStore:
    """Configuration-backed facade preserving the storage API used by the pipeline."""

    def __init__(self, root: Path | None = None, *, backend: ArtifactBackend | None = None) -> None:
        settings = get_settings()
        if backend is not None:
            self.backend = backend
        elif root is not None or settings.artifact_backend == "local":
            self.backend = LocalArtifactStore(root or settings.artifact_root)
        else:
            self.backend = S3ArtifactStore(
                bucket=settings.artifact_s3_bucket or "",
                prefix=settings.artifact_s3_prefix,
            )

    def path_for(self, step_key: str, input_hash: str) -> Path:
        if not isinstance(self.backend, LocalArtifactStore):
            raise TypeError("path_for() is only available for the local artifact backend")
        return self.backend.path_for(step_key, input_hash)

    def uri_for(self, step_key: str, input_hash: str) -> str:
        return self.backend.uri_for(step_key, input_hash)

    def publish(self, step_key: str, input_hash: str, content: dict[str, Any]) -> tuple[str, str]:
        return self.backend.publish(step_key, input_hash, content)

    def validate(self, uri: str, checksum: str) -> bool:
        return self.backend.validate(uri, checksum)

    def published_checksum(self, uri: str) -> str | None:
        return self.backend.published_checksum(uri)

    def read(self, uri: str) -> dict[str, Any]:
        return self.backend.read(uri)
