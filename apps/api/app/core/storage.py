import asyncio
from dataclasses import dataclass
from time import monotonic
from typing import Protocol
from uuid import uuid4


class ObjectStorage(Protocol):
    async def put(self, key: str, data: bytes, *, content_type: str) -> None: ...

    async def get(self, key: str) -> bytes: ...

    async def delete(self, key: str) -> None: ...

    async def create_upload_url(self, key: str, *, content_type: str, expires_in: int = 900) -> str: ...

    async def create_download_url(self, key: str, *, expires_in: int = 300) -> str: ...


class InMemoryObjectStorage:
    def __init__(self) -> None:
        self._objects: dict[str, tuple[bytes, str]] = {}
        self._download_tokens: dict[str, tuple[str, float]] = {}

    async def put(self, key: str, data: bytes, *, content_type: str) -> None:
        self._objects[key] = (bytes(data), content_type)

    async def get(self, key: str) -> bytes:
        return self._objects[key][0]

    async def delete(self, key: str) -> None:
        self._objects.pop(key, None)

    async def create_upload_url(self, key: str, *, content_type: str, expires_in: int = 900) -> str:
        del content_type, expires_in
        return f"memory://upload/{key}"

    async def create_download_url(self, key: str, *, expires_in: int = 300) -> str:
        if key not in self._objects:
            raise KeyError(key)
        token = str(uuid4())
        self._download_tokens[token] = (key, monotonic() + expires_in)
        return f"/api/v1/assets/downloads/{token}"

    async def resolve_download_token(self, token: str) -> tuple[bytes, str]:
        record = self._download_tokens.get(token)
        if record is None:
            raise KeyError(token)
        key, expires_at = record
        if monotonic() >= expires_at:
            self._download_tokens.pop(token, None)
            raise KeyError(token)
        try:
            return self._objects[key]
        except KeyError:
            self._download_tokens.pop(token, None)
            raise


@dataclass(frozen=True, slots=True)
class S3StorageOptions:
    bucket: str
    region: str
    endpoint_url: str | None = None
    access_key: str | None = None
    secret_key: str | None = None
    use_path_style: bool = False


class S3CompatibleObjectStorage:
    """AWS S3/MinIO adapter. Database records store object keys, never provider URLs."""

    def __init__(self, options: S3StorageOptions) -> None:
        import boto3
        from botocore.config import Config

        addressing_style = "path" if options.use_path_style else "auto"
        self._bucket = options.bucket
        self._client = boto3.client(
            "s3",
            region_name=options.region,
            endpoint_url=options.endpoint_url,
            aws_access_key_id=options.access_key,
            aws_secret_access_key=options.secret_key,
            config=Config(s3={"addressing_style": addressing_style}),
        )

    async def put(self, key: str, data: bytes, *, content_type: str) -> None:
        await asyncio.to_thread(
            self._client.put_object,
            Bucket=self._bucket,
            Key=key,
            Body=data,
            ContentType=content_type,
        )

    async def get(self, key: str) -> bytes:
        response = await asyncio.to_thread(self._client.get_object, Bucket=self._bucket, Key=key)
        return await asyncio.to_thread(response["Body"].read)

    async def delete(self, key: str) -> None:
        await asyncio.to_thread(self._client.delete_object, Bucket=self._bucket, Key=key)

    async def create_upload_url(self, key: str, *, content_type: str, expires_in: int = 900) -> str:
        return await asyncio.to_thread(
            self._client.generate_presigned_url,
            "put_object",
            Params={"Bucket": self._bucket, "Key": key, "ContentType": content_type},
            ExpiresIn=expires_in,
        )

    async def create_download_url(self, key: str, *, expires_in: int = 300) -> str:
        return await asyncio.to_thread(
            self._client.generate_presigned_url,
            "get_object",
            Params={"Bucket": self._bucket, "Key": key},
            ExpiresIn=expires_in,
        )
