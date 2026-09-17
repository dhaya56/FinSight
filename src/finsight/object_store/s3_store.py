"""S3-compatible object store.

This adapter speaks the S3 API and nothing else. SeaweedFS is one endpoint that
answers it; AWS S3, Cloudflare R2 and others answer the same calls, so moving
between them is configuration rather than code
(PROJECT_BLUEPRINT.md §29.3, §29.4).

Nothing outside this module imports ``boto3``. That boundary is what keeps the
port swappable, and it is mechanically checkable.

Client behaviour is pinned rather than inherited: botocore defaults to sixty
second connect and read timeouts and to the legacy retry mode, which together
give a worst case of *attempts x (connect + read)*. ENV-002 recorded the same
multiplicative pattern on the database; the values here are configurable and are
unmeasured initial defaults.
"""

from collections.abc import Iterator
from contextlib import contextmanager
from functools import lru_cache
from typing import Any, BinaryIO

import boto3
from boto3.s3.transfer import TransferConfig
from botocore.client import Config
from botocore.exceptions import BotoCoreError, ClientError

from finsight.config.settings import Settings, get_settings
from finsight.object_store.port import (
    ObjectAlreadyExistsError,
    ObjectInfo,
    ObjectNotFoundError,
    ObjectStoreUnavailableError,
)

_NOT_FOUND_CODES = frozenset({"404", "NoSuchKey", "NoSuchBucket", "NotFound"})
_BUCKET_EXISTS_CODES = frozenset({"BucketAlreadyOwnedByYou", "BucketAlreadyExists"})


def _error_code(error: ClientError) -> str:
    code = error.response.get("Error", {}).get("Code", "")
    return str(code)


@lru_cache(maxsize=1)
def get_s3_client() -> Any:
    """Return the process-wide S3 client, creating it on first use.

    One shared client: construction loads the service model and builds its own
    connection pool, so per-request clients would leak sockets and add latency.
    ``boto3.client`` rather than ``boto3.resource`` — resources are not
    thread-safe, which matters once background workers run concurrently.

    Credentials left unset fall through to boto3's default chain, so instance
    roles and workload identity work in a cloud deployment without code change.
    """
    settings = get_settings()
    secret = settings.s3_secret_access_key
    return boto3.client(
        "s3",
        endpoint_url=settings.s3_endpoint_url,
        region_name=settings.s3_region,
        aws_access_key_id=settings.s3_access_key_id,
        aws_secret_access_key=secret.get_secret_value() if secret is not None else None,
        config=Config(
            s3={"addressing_style": settings.s3_addressing_style},
            connect_timeout=settings.s3_connect_timeout_seconds,
            read_timeout=settings.s3_read_timeout_seconds,
            retries={"mode": "standard", "max_attempts": settings.s3_max_attempts},
        ),
    )


def dispose_s3_client() -> None:
    """Release the shared client's sockets if one was ever created.

    Safe when no client exists: it returns without constructing one, so shutdown
    paths never force object-store settings to be read.
    """
    if get_s3_client.cache_info().currsize == 0:
        return
    client = get_s3_client()
    close = getattr(client, "close", None)
    if callable(close):
        close()
    get_s3_client.cache_clear()


class S3ObjectStore:
    """Store objects in any S3-compatible backend."""

    def __init__(
        self,
        client: Any,
        bucket: str,
        transfer_config: TransferConfig,
        *,
        send_checksum: bool = True,
    ) -> None:
        self._client = client
        self._bucket = bucket
        self._transfer_config = transfer_config
        self._send_checksum = send_checksum

    def ensure_bucket(self) -> None:
        """Create the bucket if it does not exist. Idempotent."""
        try:
            self._client.head_bucket(Bucket=self._bucket)
            return
        except ClientError as error:
            if _error_code(error) not in _NOT_FOUND_CODES:
                raise ObjectStoreUnavailableError(f"could not inspect bucket: {error}") from error
        except BotoCoreError as error:
            raise ObjectStoreUnavailableError(f"could not inspect bucket: {error}") from error

        try:
            self._client.create_bucket(Bucket=self._bucket)
        except ClientError as error:
            if _error_code(error) in _BUCKET_EXISTS_CODES:
                return
            raise ObjectStoreUnavailableError(f"could not create bucket: {error}") from error
        except BotoCoreError as error:
            raise ObjectStoreUnavailableError(f"could not create bucket: {error}") from error

    def put_if_absent(
        self,
        key: str,
        source: BinaryIO,
        *,
        size_bytes: int,
        sha256_hex: str,
    ) -> ObjectInfo:
        """Store the object unless identical content is already present."""
        del sha256_hex  # The key already encodes the digest; kept for port parity.

        stored = self._size_if_present(key)
        if stored is not None:
            if stored != size_bytes:
                raise ObjectAlreadyExistsError(
                    f"key already holds {stored} bytes, expected {size_bytes}"
                )
            return ObjectInfo(key=key, size_bytes=stored)

        extra_args = {"ChecksumAlgorithm": "SHA256"} if self._send_checksum else {}
        try:
            self._client.upload_fileobj(
                Fileobj=source,
                Bucket=self._bucket,
                Key=key,
                ExtraArgs=extra_args,
                Config=self._transfer_config,
            )
        except (ClientError, BotoCoreError) as error:
            raise ObjectStoreUnavailableError(f"could not store object: {error}") from error

        return ObjectInfo(key=key, size_bytes=size_bytes)

    @contextmanager
    def open_stream(self, key: str) -> Iterator[BinaryIO]:
        """Open the stored object for reading, releasing the connection on exit."""
        try:
            response = self._client.get_object(Bucket=self._bucket, Key=key)
        except ClientError as error:
            if _error_code(error) in _NOT_FOUND_CODES:
                raise ObjectNotFoundError(f"no object stored at {key}") from error
            raise ObjectStoreUnavailableError(f"could not read object: {error}") from error
        except BotoCoreError as error:
            raise ObjectStoreUnavailableError(f"could not read object: {error}") from error

        body: BinaryIO = response["Body"]
        try:
            yield body
        finally:
            body.close()

    def exists(self, key: str) -> bool:
        """Report whether an object is stored at the key."""
        return self._size_if_present(key) is not None

    def stat(self, key: str) -> ObjectInfo:
        """Return what is known about the stored object."""
        size = self._size_if_present(key)
        if size is None:
            raise ObjectNotFoundError(f"no object stored at {key}")
        return ObjectInfo(key=key, size_bytes=size)

    def _size_if_present(self, key: str) -> int | None:
        try:
            response = self._client.head_object(Bucket=self._bucket, Key=key)
        except ClientError as error:
            if _error_code(error) in _NOT_FOUND_CODES:
                return None
            raise ObjectStoreUnavailableError(f"could not inspect object: {error}") from error
        except BotoCoreError as error:
            raise ObjectStoreUnavailableError(f"could not inspect object: {error}") from error
        return int(response["ContentLength"])


def build_s3_object_store(settings: Settings | None = None) -> S3ObjectStore:
    """Build a store from settings, using the shared client."""
    resolved = settings if settings is not None else get_settings()
    transfer_config = TransferConfig(
        multipart_threshold=resolved.s3_multipart_threshold_bytes,
        multipart_chunksize=resolved.s3_multipart_threshold_bytes,
        max_concurrency=resolved.s3_multipart_concurrency,
        use_threads=resolved.s3_multipart_concurrency > 1,
    )
    return S3ObjectStore(
        client=get_s3_client(),
        bucket=resolved.s3_bucket,
        transfer_config=transfer_config,
        send_checksum=resolved.s3_send_checksum,
    )
