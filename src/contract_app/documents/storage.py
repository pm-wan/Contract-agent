"""Immutable local development object store with tenant scoped content keys."""

import hashlib
import importlib
import os
import tempfile
from collections.abc import Callable, Iterator, Mapping
from dataclasses import dataclass
from datetime import UTC, datetime
from pathlib import Path
from typing import Any, Literal, Protocol, cast
from uuid import UUID


class ObjectIntegrityError(ValueError):
    """Stored bytes do not match their declared content digest."""


class ObjectConflict(ValueError):
    """An immutable object key could not be created after concurrent writes."""


class ObjectStore(Protocol):
    """Storage interface shared by local development and S3-compatible backends."""

    @property
    def backend(self) -> str: ...

    def put(self, tenant_id: UUID, case_id: UUID, data: bytes) -> "ObjectRef": ...

    def get(self, reference: "ObjectRef") -> bytes: ...

    def delete(self, reference: "ObjectRef") -> bool: ...

    def iter_before(self, tenant_id: UUID, cutoff: datetime) -> Iterator["StoredObject"]: ...

    def iter_temporary_before(
        self, tenant_id: UUID, cutoff: datetime
    ) -> Iterator["TemporaryObject"]: ...

    def delete_temporary(self, temporary: "TemporaryObject", cutoff: datetime) -> bool: ...


class S3Client(Protocol):
    def put_object(self, **kwargs: Any) -> Any: ...

    def get_object(self, **kwargs: Any) -> Any: ...

    def delete_object(self, **kwargs: Any) -> Any: ...

    def head_object(self, **kwargs: Any) -> Any: ...

    def list_objects_v2(self, **kwargs: Any) -> Any: ...


S3ClientFactory = Callable[[str | None, str | None], S3Client]


@dataclass(frozen=True, slots=True)
class ObjectRef:
    tenant_id: UUID
    case_id: UUID
    sha256: str
    size_bytes: int


@dataclass(frozen=True, slots=True)
class StoredObject:
    reference: ObjectRef
    created_at: datetime


@dataclass(frozen=True, slots=True)
class TemporaryObject:
    tenant_id: UUID
    case_id: UUID
    sha256: str
    name: str
    created_at: datetime


class LocalObjectStore:
    """Store immutable bytes under tenant/case/hash; never persist client filenames."""

    backend: Literal["local"] = "local"

    def __init__(self, root: str | Path) -> None:
        self.root = Path(root).resolve()
        self.root.mkdir(parents=True, exist_ok=True, mode=0o700)
        if not self.root.is_dir():
            raise ValueError("object_store_root_invalid")

    @staticmethod
    def _digest(data: bytes) -> str:
        return hashlib.sha256(data).hexdigest()

    def _path(self, tenant_id: UUID, case_id: UUID, digest: str) -> Path:
        if len(digest) != 64 or any(char not in "0123456789abcdef" for char in digest):
            raise ValueError("object_digest_invalid")
        tenant_directory = self.root / str(tenant_id)
        tenant_directory.mkdir(exist_ok=True, mode=0o700)
        resolved_tenant = tenant_directory.resolve()
        if not resolved_tenant.is_relative_to(self.root):
            raise ValueError("object_path_invalid")
        directory = tenant_directory / str(case_id)
        directory.mkdir(exist_ok=True, mode=0o700)
        resolved_directory = directory.resolve()
        if not resolved_directory.is_relative_to(resolved_tenant):
            raise ValueError("object_path_invalid")
        return resolved_directory / digest

    def put(self, tenant_id: UUID, case_id: UUID, data: bytes) -> ObjectRef:
        if not data:
            raise ValueError("object_empty")
        digest = self._digest(data)
        target = self._path(tenant_id, case_id, digest)
        descriptor, temporary_name = tempfile.mkstemp(
            prefix=f".upload-{digest}-", dir=target.parent
        )
        try:
            with os.fdopen(descriptor, "wb") as temporary:
                temporary.write(data)
                temporary.flush()
                os.fsync(temporary.fileno())
            try:
                os.link(temporary_name, target, follow_symlinks=False)
            except FileExistsError:
                if self._read_verified(target, digest) != data:
                    raise ObjectIntegrityError("object_hash_collision") from None
        finally:
            Path(temporary_name).unlink(missing_ok=True)
        return ObjectRef(tenant_id, case_id, digest, len(data))

    def get(self, reference: ObjectRef) -> bytes:
        path = self._path(reference.tenant_id, reference.case_id, reference.sha256)
        data = self._read_verified(path, reference.sha256)
        if len(data) != reference.size_bytes:
            raise ObjectIntegrityError("object_size_mismatch")
        return data

    def delete(self, reference: ObjectRef) -> bool:
        path = self._path(reference.tenant_id, reference.case_id, reference.sha256)
        try:
            data = self._read_verified(path, reference.sha256)
        except FileNotFoundError:
            return False
        if len(data) != reference.size_bytes:
            raise ObjectIntegrityError("object_size_mismatch")
        path.unlink()
        return True

    def iter_before(self, tenant_id: UUID, cutoff: datetime) -> Iterator[StoredObject]:
        if cutoff.tzinfo is None:
            raise ValueError("object_cutoff_must_be_timezone_aware")
        tenant_directory = self.root / str(tenant_id)
        if tenant_directory.is_symlink():
            raise ValueError("object_path_invalid")
        try:
            resolved_tenant = tenant_directory.resolve(strict=True)
        except FileNotFoundError:
            return
        if not resolved_tenant.is_relative_to(self.root):
            raise ValueError("object_path_invalid")
        cutoff_utc = cutoff.astimezone(UTC)
        for case_directory in resolved_tenant.iterdir():
            if case_directory.is_symlink() or not case_directory.is_dir():
                continue
            try:
                case_id = UUID(case_directory.name)
            except ValueError:
                continue
            for path in case_directory.iterdir():
                if (
                    path.is_symlink()
                    or not path.is_file()
                    or len(path.name) != 64
                    or any(character not in "0123456789abcdef" for character in path.name)
                ):
                    continue
                metadata = path.stat()
                modified_at = datetime.fromtimestamp(metadata.st_mtime, UTC)
                if modified_at < cutoff_utc:
                    yield StoredObject(
                        ObjectRef(tenant_id, case_id, path.name, metadata.st_size),
                        modified_at,
                    )

    def iter_temporary_before(self, tenant_id: UUID, cutoff: datetime) -> Iterator[TemporaryObject]:
        if cutoff.tzinfo is None:
            raise ValueError("object_cutoff_must_be_timezone_aware")
        tenant_directory = self.root / str(tenant_id)
        if tenant_directory.is_symlink():
            raise ValueError("object_path_invalid")
        try:
            resolved_tenant = tenant_directory.resolve(strict=True)
        except FileNotFoundError:
            return
        if not resolved_tenant.is_relative_to(self.root):
            raise ValueError("object_path_invalid")
        cutoff_utc = cutoff.astimezone(UTC)
        for case_directory in resolved_tenant.iterdir():
            if case_directory.is_symlink() or not case_directory.is_dir():
                continue
            try:
                case_id = UUID(case_directory.name)
            except ValueError:
                continue
            for path in case_directory.iterdir():
                if path.is_symlink() or not path.is_file() or not path.name.startswith(".upload-"):
                    continue
                digest, separator, _suffix = path.name.removeprefix(".upload-").partition("-")
                if (
                    not separator
                    or len(digest) != 64
                    or any(character not in "0123456789abcdef" for character in digest)
                ):
                    continue
                modified_at = datetime.fromtimestamp(path.stat().st_mtime, UTC)
                if modified_at < cutoff_utc:
                    yield TemporaryObject(
                        tenant_id,
                        case_id,
                        digest,
                        path.name,
                        modified_at,
                    )

    def delete_temporary(self, temporary: TemporaryObject, cutoff: datetime) -> bool:
        if cutoff.tzinfo is None:
            raise ValueError("object_cutoff_must_be_timezone_aware")
        tenant_directory = self.root / str(temporary.tenant_id)
        case_directory = tenant_directory / str(temporary.case_id)
        if tenant_directory.is_symlink() or case_directory.is_symlink():
            raise ValueError("object_path_invalid")
        try:
            resolved_case = case_directory.resolve(strict=True)
        except FileNotFoundError:
            return False
        if not resolved_case.is_relative_to(self.root):
            raise ValueError("object_path_invalid")
        prefix = f".upload-{temporary.sha256}-"
        if not temporary.name.startswith(prefix) or Path(temporary.name).name != temporary.name:
            raise ValueError("object_temporary_name_invalid")
        path = resolved_case / temporary.name
        if path.is_symlink() or not path.is_file():
            return False
        modified_at = datetime.fromtimestamp(path.stat().st_mtime, UTC)
        if modified_at >= cutoff.astimezone(UTC):
            return False
        path.unlink()
        return True

    @staticmethod
    def _read_verified(path: Path, expected_digest: str) -> bytes:
        flags = os.O_RDONLY | getattr(os, "O_NOFOLLOW", 0)
        try:
            descriptor = os.open(path, flags)
        except FileNotFoundError as exc:
            raise FileNotFoundError("object_not_found") from exc
        with os.fdopen(descriptor, "rb") as stored:
            data = stored.read()
        if hashlib.sha256(data).hexdigest() != expected_digest:
            raise ObjectIntegrityError("object_hash_mismatch")
        return data


class S3ObjectStore:
    """S3-compatible content store using conditional writes to prevent replacement."""

    backend: Literal["s3"] = "s3"

    def __init__(self, client: S3Client, bucket: str, *, prefix: str = "contract-audit") -> None:
        normalized_prefix = prefix.strip("/")
        if not bucket or not normalized_prefix or ".." in normalized_prefix.split("/"):
            raise ValueError("s3_object_store_configuration_invalid")
        self.client = client
        self.bucket = bucket
        self.prefix = normalized_prefix

    def _key(self, tenant_id: UUID, case_id: UUID, digest: str) -> str:
        if len(digest) != 64 or any(char not in "0123456789abcdef" for char in digest):
            raise ValueError("object_digest_invalid")
        return f"{self.prefix}/{tenant_id}/{case_id}/{digest}"

    @staticmethod
    def _error_status(error: Exception) -> tuple[int | None, str | None]:
        response = getattr(error, "response", None)
        if not isinstance(response, dict):
            return None, None
        metadata = response.get("ResponseMetadata", {})
        details = response.get("Error", {})
        status = metadata.get("HTTPStatusCode") if isinstance(metadata, dict) else None
        code = details.get("Code") if isinstance(details, dict) else None
        return (status if isinstance(status, int) else None, str(code) if code else None)

    def put(self, tenant_id: UUID, case_id: UUID, data: bytes) -> ObjectRef:
        if not data:
            raise ValueError("object_empty")
        digest = hashlib.sha256(data).hexdigest()
        reference = ObjectRef(tenant_id, case_id, digest, len(data))
        key = self._key(tenant_id, case_id, digest)
        for attempt in range(3):
            try:
                self.client.put_object(
                    Bucket=self.bucket,
                    Key=key,
                    Body=data,
                    ContentLength=len(data),
                    ContentType="application/octet-stream",
                    Metadata={"sha256": digest},
                    IfNoneMatch="*",
                )
                return reference
            except Exception as exc:
                status, code = self._error_status(exc)
                if status == 412 or code in {"PreconditionFailed", "412"}:
                    existing = self.get(reference)
                    if existing != data:
                        raise ObjectIntegrityError("object_hash_collision") from None
                    return reference
                if status == 409 or code in {"ConditionalRequestConflict", "409"}:
                    if attempt < 2:
                        continue
                    raise ObjectConflict("object_write_conflict") from None
                raise
        raise ObjectConflict("object_write_conflict")

    def get(self, reference: ObjectRef) -> bytes:
        key = self._key(reference.tenant_id, reference.case_id, reference.sha256)
        try:
            response = self.client.get_object(Bucket=self.bucket, Key=key)
        except Exception as exc:
            status, code = self._error_status(exc)
            if status == 404 or code in {"NoSuchKey", "NotFound", "404"}:
                raise FileNotFoundError("object_not_found") from None
            raise
        body = response["Body"]
        try:
            raw_data = body.read()
        finally:
            close = getattr(body, "close", None)
            if callable(close):
                close()
        if not isinstance(raw_data, bytes):
            raise ObjectIntegrityError("object_payload_invalid")
        data = raw_data
        if hashlib.sha256(data).hexdigest() != reference.sha256:
            raise ObjectIntegrityError("object_hash_mismatch")
        if len(data) != reference.size_bytes:
            raise ObjectIntegrityError("object_size_mismatch")
        return data

    def delete(self, reference: ObjectRef) -> bool:
        key = self._key(reference.tenant_id, reference.case_id, reference.sha256)
        try:
            response = self.client.head_object(Bucket=self.bucket, Key=key)
        except Exception as exc:
            status, code = self._error_status(exc)
            if status == 404 or code in {"NoSuchKey", "NotFound", "404"}:
                return False
            raise
        metadata = response.get("Metadata", {})
        if not isinstance(metadata, dict) or metadata.get("sha256") != reference.sha256:
            raise ObjectIntegrityError("object_hash_mismatch")
        if response.get("ContentLength") != reference.size_bytes:
            raise ObjectIntegrityError("object_size_mismatch")
        try:
            self.client.delete_object(Bucket=self.bucket, Key=key)
        except Exception as exc:
            status, code = self._error_status(exc)
            if status == 404 or code in {"NoSuchKey", "NotFound", "404"}:
                return False
            raise
        return True

    def iter_before(self, tenant_id: UUID, cutoff: datetime) -> Iterator[StoredObject]:
        if cutoff.tzinfo is None:
            raise ValueError("object_cutoff_must_be_timezone_aware")
        cutoff_utc = cutoff.astimezone(UTC)
        prefix = f"{self.prefix}/{tenant_id}/"
        continuation_token: str | None = None
        while True:
            request: dict[str, Any] = {"Bucket": self.bucket, "Prefix": prefix}
            if continuation_token is not None:
                request["ContinuationToken"] = continuation_token
            response = self.client.list_objects_v2(**request)
            entries = response.get("Contents", [])
            if not isinstance(entries, list):
                raise ObjectIntegrityError("object_listing_invalid")
            for entry in entries:
                if not isinstance(entry, dict):
                    continue
                key = entry.get("Key")
                size_bytes = entry.get("Size")
                modified_at = entry.get("LastModified")
                if (
                    not isinstance(key, str)
                    or not isinstance(size_bytes, int)
                    or not isinstance(modified_at, datetime)
                    or modified_at.tzinfo is None
                    or modified_at.astimezone(UTC) >= cutoff_utc
                ):
                    continue
                if not key.startswith(prefix):
                    continue
                remainder = key.removeprefix(prefix).split("/")
                if len(remainder) != 2:
                    continue
                try:
                    case_id = UUID(remainder[0])
                except ValueError:
                    continue
                digest = remainder[1]
                if len(digest) != 64 or any(c not in "0123456789abcdef" for c in digest):
                    continue
                yield StoredObject(
                    ObjectRef(tenant_id, case_id, digest, size_bytes),
                    modified_at.astimezone(UTC),
                )
            if response.get("IsTruncated") is not True:
                break
            next_token = response.get("NextContinuationToken")
            if not isinstance(next_token, str) or not next_token:
                raise ObjectIntegrityError("object_listing_cursor_invalid")
            continuation_token = next_token

    def iter_temporary_before(self, tenant_id: UUID, cutoff: datetime) -> Iterator[TemporaryObject]:
        del tenant_id, cutoff
        return iter(())

    def delete_temporary(self, temporary: TemporaryObject, cutoff: datetime) -> bool:
        del temporary, cutoff
        return False


def _boto3_client(endpoint_url: str | None, region_name: str | None) -> S3Client:
    try:
        boto3 = importlib.import_module("boto3")
        botocore_config = importlib.import_module("botocore.config")
    except ImportError as exc:
        raise RuntimeError("s3_backend_requires_storage_extra") from exc
    config = botocore_config.Config(s3={"addressing_style": "path" if endpoint_url else "auto"})
    return cast(
        S3Client,
        boto3.client("s3", endpoint_url=endpoint_url, region_name=region_name, config=config),
    )


def create_object_store(
    environment: Mapping[str, str] | None = None,
    *,
    s3_client_factory: S3ClientFactory | None = None,
) -> ObjectStore:
    """Build the configured backend; invalid S3 config never falls back to local disk."""
    settings = os.environ if environment is None else environment
    backend = settings.get("OBJECT_STORE_BACKEND", "local").strip().lower()
    if backend == "local":
        root = settings.get("OBJECT_STORE_DIR", "./var/objects")
        return LocalObjectStore(root)
    if backend == "s3":
        bucket = settings.get("S3_BUCKET", "").strip()
        if not bucket:
            raise ValueError("s3_bucket_required")
        endpoint_url = settings.get("S3_ENDPOINT_URL") or None
        region_name = settings.get("AWS_REGION") or settings.get("AWS_DEFAULT_REGION") or None
        client = (s3_client_factory or _boto3_client)(endpoint_url, region_name)
        return S3ObjectStore(
            client,
            bucket,
            prefix=settings.get("S3_PREFIX", "contract-audit"),
        )
    raise ValueError("object_store_backend_unsupported")
