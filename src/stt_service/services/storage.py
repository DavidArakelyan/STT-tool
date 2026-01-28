"""S3 Storage service for file operations."""

import io
import json
from contextlib import asynccontextmanager
from typing import Any, AsyncGenerator, BinaryIO

import aioboto3
from botocore.config import Config as BotoConfig
from botocore.exceptions import ClientError

from stt_service.config import get_settings
from stt_service.utils.exceptions import StorageError

settings = get_settings()


class StorageService:
    """Service for S3/MinIO storage operations."""

    def __init__(self) -> None:
        self._session = aioboto3.Session()
        self._config = BotoConfig(
            signature_version="s3v4",
            retries={"max_attempts": 3, "mode": "adaptive"},
        )

    @asynccontextmanager
    async def _get_client(self) -> AsyncGenerator[Any, None]:
        """Get an S3 client."""
        async with self._session.client(
            "s3",
            endpoint_url=settings.s3.endpoint_url,
            aws_access_key_id=settings.s3.access_key_id,
            aws_secret_access_key=settings.s3.secret_access_key,
            region_name=settings.s3.region,
            config=self._config,
        ) as client:
            yield client

    async def ensure_bucket_exists(self) -> None:
        """Ensure the bucket exists, create if not."""
        async with self._get_client() as client:
            try:
                await client.head_bucket(Bucket=settings.s3.bucket_name)
            except ClientError as e:
                error_code = e.response.get("Error", {}).get("Code", "")
                if error_code == "404":
                    try:
                        await client.create_bucket(
                            Bucket=settings.s3.bucket_name,
                            CreateBucketConfiguration={
                                "LocationConstraint": settings.s3.region
                            }
                            if settings.s3.region != "us-east-1"
                            else {},
                        )
                    except ClientError as create_error:
                        raise StorageError(
                            f"Failed to create bucket: {create_error}"
                        ) from create_error
                else:
                    raise StorageError(f"Failed to check bucket: {e}") from e

    async def upload_file(
        self,
        key: str,
        data: bytes | BinaryIO,
        content_type: str = "application/octet-stream",
        metadata: dict[str, str] | None = None,
    ) -> str:
        """Upload a file to S3.

        Args:
            key: S3 object key
            data: File data (bytes or file-like object)
            content_type: MIME type
            metadata: Optional metadata dict

        Returns:
            The S3 key of the uploaded file
        """
        async with self._get_client() as client:
            try:
                extra_args: dict[str, Any] = {"ContentType": content_type}
                if metadata:
                    extra_args["Metadata"] = metadata

                if isinstance(data, bytes):
                    data = io.BytesIO(data)

                await client.upload_fileobj(
                    data,
                    settings.s3.bucket_name,
                    key,
                    ExtraArgs=extra_args,
                )
                return key
            except ClientError as e:
                raise StorageError(f"Failed to upload file: {e}") from e

    async def download_file(self, key: str) -> bytes:
        """Download a file from S3.

        Args:
            key: S3 object key

        Returns:
            File content as bytes
        """
        async with self._get_client() as client:
            try:
                buffer = io.BytesIO()
                await client.download_fileobj(
                    settings.s3.bucket_name,
                    key,
                    buffer,
                )
                buffer.seek(0)
                return buffer.read()
            except ClientError as e:
                raise StorageError(f"Failed to download file: {e}") from e

    async def download_file_to_path(self, key: str, local_path: str) -> str:
        """Download a file from S3 to a local path.

        Args:
            key: S3 object key
            local_path: Local file path

        Returns:
            Local file path
        """
        async with self._get_client() as client:
            try:
                await client.download_file(
                    settings.s3.bucket_name,
                    key,
                    local_path,
                )
                return local_path
            except ClientError as e:
                raise StorageError(f"Failed to download file: {e}") from e

    async def delete_file(self, key: str) -> None:
        """Delete a file from S3."""
        async with self._get_client() as client:
            try:
                await client.delete_object(
                    Bucket=settings.s3.bucket_name,
                    Key=key,
                )
            except ClientError as e:
                raise StorageError(f"Failed to delete file: {e}") from e

    async def delete_files(self, keys: list[str]) -> None:
        """Delete multiple files from S3."""
        if not keys:
            return

        async with self._get_client() as client:
            try:
                await client.delete_objects(
                    Bucket=settings.s3.bucket_name,
                    Delete={"Objects": [{"Key": key} for key in keys]},
                )
            except ClientError as e:
                raise StorageError(f"Failed to delete files: {e}") from e

    async def file_exists(self, key: str) -> bool:
        """Check if a file exists in S3."""
        async with self._get_client() as client:
            try:
                await client.head_object(
                    Bucket=settings.s3.bucket_name,
                    Key=key,
                )
                return True
            except ClientError:
                return False

    async def get_file_size(self, key: str) -> int:
        """Get the size of a file in S3."""
        async with self._get_client() as client:
            try:
                response = await client.head_object(
                    Bucket=settings.s3.bucket_name,
                    Key=key,
                )
                return response["ContentLength"]
            except ClientError as e:
                raise StorageError(f"Failed to get file size: {e}") from e

    async def generate_presigned_url(
        self,
        key: str,
        expiration: int | None = None,
        method: str = "get_object",
    ) -> str:
        """Generate a presigned URL for file access.

        Args:
            key: S3 object key
            expiration: URL expiration in seconds
            method: S3 operation ('get_object' or 'put_object')

        Returns:
            Presigned URL string
        """
        if expiration is None:
            expiration = settings.s3.presigned_url_expiration

        async with self._get_client() as client:
            try:
                url = await client.generate_presigned_url(
                    method,
                    Params={
                        "Bucket": settings.s3.bucket_name,
                        "Key": key,
                    },
                    ExpiresIn=expiration,
                )
                return url
            except ClientError as e:
                raise StorageError(f"Failed to generate presigned URL: {e}") from e

    async def list_files(
        self,
        prefix: str,
        max_keys: int = 1000,
    ) -> list[dict[str, Any]]:
        """List files with a given prefix.

        Args:
            prefix: S3 key prefix
            max_keys: Maximum number of keys to return

        Returns:
            List of file metadata dicts
        """
        async with self._get_client() as client:
            try:
                response = await client.list_objects_v2(
                    Bucket=settings.s3.bucket_name,
                    Prefix=prefix,
                    MaxKeys=max_keys,
                )
                return [
                    {
                        "key": obj["Key"],
                        "size": obj["Size"],
                        "last_modified": obj["LastModified"],
                    }
                    for obj in response.get("Contents", [])
                ]
            except ClientError as e:
                raise StorageError(f"Failed to list files: {e}") from e

    async def upload_json(self, key: str, data: dict[str, Any]) -> str:
        """Upload JSON data to S3.

        Args:
            key: S3 object key
            data: Dict to serialize as JSON

        Returns:
            S3 key
        """
        json_bytes = json.dumps(data, ensure_ascii=False, indent=2).encode("utf-8")
        return await self.upload_file(
            key,
            json_bytes,
            content_type="application/json",
        )

    async def download_json(self, key: str) -> dict[str, Any]:
        """Download and parse JSON from S3.

        Args:
            key: S3 object key

        Returns:
            Parsed JSON dict
        """
        data = await self.download_file(key)
        return json.loads(data.decode("utf-8"))

    @staticmethod
    def generate_job_key(job_id: str, filename: str) -> str:
        """Generate S3 key for a job's original file."""
        return f"jobs/{job_id}/original/{filename}"

    @staticmethod
    def generate_chunk_key(job_id: str, chunk_index: int) -> str:
        """Generate S3 key for a job chunk."""
        return f"jobs/{job_id}/chunks/chunk_{chunk_index:04d}.wav"

    @staticmethod
    def generate_result_key(job_id: str) -> str:
        """Generate S3 key for a job's result."""
        return f"jobs/{job_id}/result/transcript.json"


# Singleton instance
storage_service = StorageService()
