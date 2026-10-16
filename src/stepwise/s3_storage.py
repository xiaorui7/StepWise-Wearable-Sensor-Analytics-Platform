"""Private immutable objects; PostgreSQL, not object listings, publishes results."""

from __future__ import annotations

import hashlib
from contextlib import suppress
from typing import Any
from uuid import UUID

import boto3
from botocore.config import Config
from botocore.exceptions import BotoCoreError, ClientError, HTTPClientError
from botocore.exceptions import ConnectionError as BotoConnectionError

from stepwise.settings import Settings
from stepwise.storage import ArtifactNotFoundError, ArtifactStore


class ObjectStoreUnavailable(ConnectionError):
    """Retryable, safe storage error (classified explicitly by the worker)."""


class ObjectIntegrityError(OSError):
    """Missing/corrupt metadata or mismatched content is terminal."""


class S3ArtifactStore:
    def __init__(self, settings: Settings) -> None:
        self.bucket = settings.s3_bucket
        self.prefix = settings.s3_prefix.strip("/")
        if not self.bucket or any(part in {".", ".."} for part in self.prefix.split("/")):
            raise ValueError("Invalid object store namespace")
        self.client = boto3.client(
            "s3",
            endpoint_url=settings.s3_endpoint_url,
            region_name=settings.s3_region,
            aws_access_key_id=settings.s3_access_key.get_secret_value() or None,
            aws_secret_access_key=settings.s3_secret_key.get_secret_value() or None,
            config=Config(
                connect_timeout=2, read_timeout=5,
                retries={"total_max_attempts": 1},
                s3={"addressing_style": "path"},
            ),
        )

    def _call(self, operation: str, **kwargs: Any) -> Any:
        try:
            return getattr(self.client, operation)(Bucket=self.bucket, **kwargs)
        except ClientError as exc:
            code = str(exc.response.get("Error", {}).get("Code", ""))
            status = exc.response.get("ResponseMetadata", {}).get("HTTPStatusCode", 0)
            if code in {"NoSuchKey", "NotFound", "404"}:
                raise ArtifactNotFoundError("Object content not found") from None
            if status in {408, 429, 500, 502, 503, 504} or code in {"SlowDown", "RequestTimeout"}:
                raise ObjectStoreUnavailable() from None
            if status in {409, 412}:
                raise FileExistsError("Immutable object already exists") from None
            raise OSError("Object storage request rejected") from None
        except (BotoConnectionError, HTTPClientError) as exc:
            raise ObjectStoreUnavailable() from exc
        except BotoCoreError:
            raise OSError("Object storage configuration or request failed") from None

    def _key(self, suffix: str) -> str:
        return f"{self.prefix}/{suffix}" if self.prefix else suffix

    def _reference(self, key: str) -> str:
        return f"s3://{self.bucket}/{key}"

    def _parse(self, reference: str) -> str:
        base = f"s3://{self.bucket}/"
        if not reference.startswith(base):
            raise ValueError("Storage key belongs to a different backend")
        key = reference[len(base):]
        if (
            not key or any(p in {"", ".", ".."} for p in key.split("/"))
            or any(c in key for c in "\\\x00")
            or (self.prefix and not key.startswith(self.prefix + "/"))
        ):
            raise ValueError("Storage key escapes configured namespace")
        return key

    def _put(self, key: str, content: bytes, media_type: str) -> str:
        digest = hashlib.sha256(content).hexdigest()
        with suppress(FileExistsError):
            self._call(
                "put_object", Key=key, Body=content, ContentType=media_type,
                Metadata={"sha256": digest}, IfNoneMatch="*",
            )
        reference = self._reference(key)
        if self.read(reference) != content:
            raise ObjectIntegrityError("Immutable object content does not match")
        return reference

    def put(self, analysis_id: UUID, name: str, content: bytes) -> str:
        name = ArtifactStore._safe_name(name)
        digest = hashlib.sha256(content).hexdigest()
        return self._put(self._key(f"{analysis_id}/inputs/{digest}/{name}"), content, "text/plain")

    def publish(
        self, analysis_id: UUID, token: str, artifacts: dict[str, tuple[str, bytes]]
    ) -> dict[str, tuple[str, str]]:
        token = str(UUID(token))
        for name, (_, content) in artifacts.items():
            ArtifactStore._safe_name(name)
            if not content:
                raise ObjectIntegrityError("Empty artifact")
        keys = {
            name: (media, self._put(self._key(f"{analysis_id}/{token}/{name}"), content, media))
            for name, (media, content) in artifacts.items()
        }
        # Recheck the whole set before DB finalization; there is no directory rename on S3.
        for name, (_, reference) in keys.items():
            if self.read(reference) != artifacts[name][1]:
                raise ObjectIntegrityError("Published content does not match")
        return keys

    def read(self, storage_key: str) -> bytes:
        response = self._call("get_object", Key=self._parse(storage_key))
        body = response["Body"]
        try:
            try:
                content: bytes = body.read()
            except (BotoConnectionError, HTTPClientError) as exc:
                raise ObjectStoreUnavailable() from exc
        finally:
            body.close()
        if (
            len(content) != response["ContentLength"]
            or hashlib.sha256(content).hexdigest() != response.get("Metadata", {}).get("sha256")
        ):
            raise ObjectIntegrityError("Object checksum verification failed")
        return content

    def check(self) -> None:
        self._call("head_bucket")
