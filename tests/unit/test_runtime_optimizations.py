import io

from app.storage.artifacts import ArtifactStore, S3ArtifactStore
from app.workers.wakeup import IdleBackoff


class FakeS3Client:
    def __init__(self) -> None:
        self.objects: dict[tuple[str, str], bytes] = {}

    def put_object(self, *, Bucket, Key, Body, **_kwargs):
        self.objects[(Bucket, Key)] = bytes(Body)

    def get_object(self, *, Bucket, Key):
        return {"Body": io.BytesIO(self.objects[(Bucket, Key)])}


def test_idle_backoff_grows_to_cap_and_resets() -> None:
    backoff = IdleBackoff(initial_seconds=0.2, max_seconds=1.0, multiplier=2.0)

    assert [backoff.next_delay() for _ in range(5)] == [0.2, 0.4, 0.8, 1.0, 1.0]
    backoff.reset()
    assert backoff.next_delay() == 0.2


def test_s3_artifact_backend_publishes_validates_and_reads() -> None:
    client = FakeS3Client()
    backend = S3ArtifactStore(bucket="pipeline", prefix="artifacts", client=client)
    store = ArtifactStore(backend=backend)

    uri, checksum = store.publish("model_inference", "a" * 64, {"result": "ok"})

    assert uri == f"s3://pipeline/artifacts/model_inference/{'a' * 64}.json"
    assert store.validate(uri, checksum)
    assert store.published_checksum(uri) == checksum
    assert store.read(uri) == {"result": "ok"}

    client.objects[("pipeline", f"artifacts/model_inference/{'a' * 64}.json")] = b"corrupt"
    assert not store.validate(uri, checksum)
    assert store.published_checksum(uri) is None
