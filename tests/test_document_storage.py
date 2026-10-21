import os
from dataclasses import replace
from datetime import UTC, datetime, timedelta
from hashlib import sha256
from io import BytesIO
from pathlib import Path
from uuid import uuid4

import pytest

from contract_app.documents.storage import (
    LocalObjectStore,
    ObjectConflict,
    ObjectIntegrityError,
    ObjectRef,
    S3ObjectStore,
    create_object_store,
)


class FakeS3Error(Exception):
    def __init__(self, status: int, code: str) -> None:
        self.response = {
            "ResponseMetadata": {"HTTPStatusCode": status},
            "Error": {"Code": code},
        }


class FakeS3Client:
    def __init__(self) -> None:
        self.objects: dict[tuple[str, str], bytes] = {}
        self.modified_at: dict[tuple[str, str], datetime] = {}
        self.put_calls = 0
        self.conflicts = 0

    def put_object(self, **request: object) -> None:
        self.put_calls += 1
        assert request["IfNoneMatch"] == "*"
        key = (str(request["Bucket"]), str(request["Key"]))
        if self.conflicts:
            self.conflicts -= 1
            raise FakeS3Error(409, "ConditionalRequestConflict")
        if key in self.objects:
            raise FakeS3Error(412, "PreconditionFailed")
        body = request["Body"]
        assert isinstance(body, bytes)
        self.objects[key] = body
        self.modified_at[key] = datetime.now(UTC)

    def get_object(self, **request: object) -> dict[str, BytesIO]:
        key = (str(request["Bucket"]), str(request["Key"]))
        try:
            return {"Body": BytesIO(self.objects[key])}
        except KeyError as exc:
            raise FakeS3Error(404, "NoSuchKey") from exc

    def delete_object(self, **request: object) -> None:
        key = (str(request["Bucket"]), str(request["Key"]))
        self.objects.pop(key, None)
        self.modified_at.pop(key, None)

    def head_object(self, **request: object) -> dict[str, object]:
        key = (str(request["Bucket"]), str(request["Key"]))
        try:
            data = self.objects[key]
        except KeyError as exc:
            raise FakeS3Error(404, "NoSuchKey") from exc
        return {"ContentLength": len(data), "Metadata": {"sha256": sha256(data).hexdigest()}}

    def list_objects_v2(self, **request: object) -> dict[str, object]:
        bucket = str(request["Bucket"])
        prefix = str(request["Prefix"])
        entries = [
            {
                "Key": key,
                "Size": len(data),
                "LastModified": self.modified_at[(stored_bucket, key)],
            }
            for (stored_bucket, key), data in self.objects.items()
            if stored_bucket == bucket and key.startswith(prefix)
        ]
        return {"Contents": entries, "IsTruncated": False}


def test_local_object_store_is_content_addressed_and_tenant_scoped(tmp_path: Path) -> None:
    store = LocalObjectStore(tmp_path / "objects")
    tenant, case, other_tenant = uuid4(), uuid4(), uuid4()
    data = b"synthetic contract bytes"

    first = store.put(tenant, case, data)
    repeated = store.put(tenant, case, data)
    isolated = store.put(other_tenant, case, data)

    assert first == repeated
    assert store.get(first) == data
    assert first.sha256 == repeated.sha256 == isolated.sha256
    assert first.size_bytes == len(data)
    assert LocalObjectStore(tmp_path / "objects").get(isolated) == data
    assert len(list((tmp_path / "objects").rglob(first.sha256))) == 2
    assert not list((tmp_path / "objects").rglob("contract.pdf"))
    assert store.delete(first)
    assert not store.delete(first)
    assert store.get(isolated) == data


def test_local_object_store_rejects_tampering_and_empty_bytes(tmp_path: Path) -> None:
    store = LocalObjectStore(tmp_path / "objects")
    reference = store.put(uuid4(), uuid4(), b"original")
    path = (
        tmp_path / "objects" / str(reference.tenant_id) / str(reference.case_id) / reference.sha256
    )
    path.write_bytes(b"changed")

    with pytest.raises(ObjectIntegrityError, match="object_hash_mismatch"):
        store.get(reference)
    path.write_bytes(b"original")
    with pytest.raises(ObjectIntegrityError, match="object_size_mismatch"):
        store.get(replace(reference, size_bytes=reference.size_bytes + 1))
    with pytest.raises(ValueError, match="object_empty"):
        store.put(uuid4(), uuid4(), b"")


def test_local_object_store_rejects_symlinked_tenant_paths(tmp_path: Path) -> None:
    root = tmp_path / "objects"
    outside = tmp_path / "outside"
    outside.mkdir()
    tenant = uuid4()
    store = LocalObjectStore(root)
    (root / str(tenant)).symlink_to(outside, target_is_directory=True)

    with pytest.raises(ValueError, match="object_path_invalid"):
        store.put(tenant, uuid4(), b"secret")
    assert list(outside.iterdir()) == []


def test_local_object_store_lists_only_old_tenant_objects(tmp_path: Path) -> None:
    store = LocalObjectStore(tmp_path / "objects")
    tenant, case, other_tenant = uuid4(), uuid4(), uuid4()
    stale = store.put(tenant, case, b"stale object")
    fresh = store.put(tenant, case, b"fresh object")
    other = store.put(other_tenant, case, b"other tenant object")
    old_time = (datetime.now(UTC) - timedelta(days=2)).timestamp()
    stale_path = store.root / str(tenant) / str(case) / stale.sha256
    os.utime(stale_path, (old_time, old_time))

    candidates = list(store.iter_before(tenant, datetime.now(UTC) - timedelta(days=1)))

    assert [candidate.reference for candidate in candidates] == [stale]
    assert fresh not in [candidate.reference for candidate in candidates]
    assert other.tenant_id not in {candidate.reference.tenant_id for candidate in candidates}


def test_local_object_store_cleans_only_expired_upload_temporaries(tmp_path: Path) -> None:
    store = LocalObjectStore(tmp_path / "objects")
    tenant, case = uuid4(), uuid4()
    store.put(tenant, case, b"create tenant and case directories")
    digest = sha256(b"interrupted upload").hexdigest()
    stale_path = store.root / str(tenant) / str(case) / f".upload-{digest}-stale"
    stale_path.write_bytes(b"partial")
    fresh_path = store.root / str(tenant) / str(case) / f".upload-{digest}-fresh"
    fresh_path.write_bytes(b"partial")
    old_time = (datetime.now(UTC) - timedelta(days=2)).timestamp()
    os.utime(stale_path, (old_time, old_time))
    cutoff = datetime.now(UTC) - timedelta(days=1)

    stale = list(store.iter_temporary_before(tenant, cutoff))

    assert len(stale) == 1
    assert stale[0].name == stale_path.name
    assert store.delete_temporary(stale[0], cutoff)
    assert not store.delete_temporary(stale[0], cutoff)
    assert fresh_path.exists()


def test_s3_object_store_uses_conditional_put_and_validates_downloads() -> None:
    client = FakeS3Client()
    store = S3ObjectStore(client, "contract-test-bucket", prefix="private/contracts")
    tenant, case = uuid4(), uuid4()
    data = b"s3 synthetic contract"

    reference = store.put(tenant, case, data)
    assert store.put(tenant, case, data) == reference
    assert store.get(reference) == data
    assert client.put_calls == 2  # The second conditional put observed 412 and verified bytes.
    assert len(client.objects) == 1
    assert next(iter(client.objects))[1].startswith(f"private/contracts/{tenant}/{case}/")
    assert store.delete(reference)
    assert not store.delete(reference)
    assert len(client.objects) == 0


def test_s3_object_store_lists_old_objects_for_one_tenant() -> None:
    client = FakeS3Client()
    store = S3ObjectStore(client, "contract-test-bucket", prefix="private/contracts")
    tenant, case, other_tenant = uuid4(), uuid4(), uuid4()
    stale = store.put(tenant, case, b"old s3 object")
    store.put(other_tenant, case, b"other tenant object")
    old_time = datetime.now(UTC) - timedelta(days=2)
    stale_key = (store.bucket, store._key(tenant, case, stale.sha256))
    client.modified_at[stale_key] = old_time

    candidates = list(store.iter_before(tenant, datetime.now(UTC) - timedelta(days=1)))

    assert len(candidates) == 1
    assert candidates[0].reference == stale
    assert candidates[0].created_at == old_time


def test_s3_object_store_retries_conflicts_and_rejects_corrupt_existing_object() -> None:
    client = FakeS3Client()
    store = S3ObjectStore(client, "contract-test-bucket")
    tenant, case = uuid4(), uuid4()
    client.conflicts = 2
    reference = store.put(tenant, case, b"first write")
    assert client.put_calls == 3
    key = (store.bucket, store._key(tenant, case, reference.sha256))
    client.objects[key] = b"corrupt"
    with pytest.raises(ObjectIntegrityError, match="object_hash_mismatch"):
        store.put(tenant, case, b"first write")
    with pytest.raises(FileNotFoundError, match="object_not_found"):
        store.get(ObjectRef(tenant, case, "0" * 64, 4))
    client.conflicts = 3
    with pytest.raises(ObjectConflict, match="object_write_conflict"):
        store.put(uuid4(), uuid4(), b"conflicted write")


def test_object_store_factory_selects_local_or_injected_s3_without_fallback(
    tmp_path: Path,
) -> None:
    local_path = tmp_path / "local objects"
    local = create_object_store({"OBJECT_STORE_DIR": str(local_path)})
    assert isinstance(local, LocalObjectStore)
    assert local.root == local_path.resolve()

    client = FakeS3Client()
    factory_calls: list[tuple[str | None, str | None]] = []

    def make_client(endpoint: str | None, region: str | None) -> FakeS3Client:
        factory_calls.append((endpoint, region))
        return client

    s3 = create_object_store(
        {
            "OBJECT_STORE_BACKEND": "s3",
            "S3_BUCKET": "contract-bucket",
            "S3_ENDPOINT_URL": "http://127.0.0.1:9000",
            "AWS_REGION": "ap-east-1",
            "S3_PREFIX": "private",
        },
        s3_client_factory=make_client,
    )
    assert isinstance(s3, S3ObjectStore)
    assert factory_calls == [("http://127.0.0.1:9000", "ap-east-1")]
    assert s3.put(uuid4(), uuid4(), b"configured")

    with pytest.raises(ValueError, match="s3_bucket_required"):
        create_object_store({"OBJECT_STORE_BACKEND": "s3", "OBJECT_STORE_DIR": str(local_path)})
    with pytest.raises(ValueError, match="object_store_backend_unsupported"):
        create_object_store({"OBJECT_STORE_BACKEND": "unknown"})
