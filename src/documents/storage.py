import shutil
import uuid
from abc import ABC, abstractmethod
from pathlib import Path
from urllib.parse import quote

import boto3
from fastapi import Request, UploadFile

from core.config import settings


class StoredFile:
    def __init__(
        self,
        storage_path: str,
        original_file_name: str,
        file_type: str | None,
        file_size_bytes: int,
    ) -> None:
        self.storage_path = storage_path
        self.original_file_name = original_file_name
        self.file_type = file_type
        self.file_size_bytes = file_size_bytes


class StorageService(ABC):
    @abstractmethod
    async def upload_file(self, file: UploadFile, course_id: int) -> StoredFile:
        raise NotImplementedError


class LocalStorageService(StorageService):
    def __init__(self, upload_dir: Path = settings.upload_dir) -> None:
        self.upload_dir = upload_dir

    async def upload_file(self, file: UploadFile, course_id: int) -> StoredFile:
        original_name = Path(file.filename or "upload.bin").name
        suffix = Path(original_name).suffix.lower()
        safe_name = f"{uuid.uuid4().hex}{suffix}"
        course_dir = self.upload_dir / "courses" / str(course_id)
        course_dir.mkdir(parents=True, exist_ok=True)
        target_path = course_dir / safe_name

        size = 0
        with target_path.open("wb") as output:
            while chunk := await file.read(1024 * 1024):
                size += len(chunk)
                output.write(chunk)

        return StoredFile(
            storage_path=target_path.as_posix(),
            original_file_name=original_name,
            file_type=suffix.lstrip(".") or None,
            file_size_bytes=size,
        )


class S3StorageService(StorageService):
    def __init__(self) -> None:
        if not settings.s3_bucket_name:
            raise RuntimeError("S3_BUCKET_NAME is not configured")
        self.bucket_name = settings.s3_bucket_name
        self.client = boto3.client(
            "s3",
            region_name=settings.aws_region,
            endpoint_url=f"https://s3.{settings.aws_region}.amazonaws.com",
            aws_access_key_id=settings.aws_access_key_id or None,
            aws_secret_access_key=settings.aws_secret_access_key or None,
        )

    async def upload_file(self, file: UploadFile, course_id: int) -> StoredFile:
        original_name = Path(file.filename or "upload.bin").name
        suffix = Path(original_name).suffix.lower()
        safe_name = f"{uuid.uuid4().hex}{suffix}"
        key = "/".join(
            part.strip("/")
            for part in [settings.s3_prefix, "courses", str(course_id), safe_name]
            if part.strip("/")
        )
        content = await file.read()
        self.client.put_object(
            Bucket=self.bucket_name,
            Key=key,
            Body=content,
            ContentType=file.content_type or "application/octet-stream",
            ContentDisposition=f"attachment; filename*=UTF-8''{quote(original_name)}",
        )
        return StoredFile(
            storage_path=f"s3://{self.bucket_name}/{key}",
            original_file_name=original_name,
            file_type=suffix.lstrip(".") or None,
            file_size_bytes=len(content),
        )

    def generate_download_url(self, storage_path: str) -> str:
        bucket_name, key = parse_s3_uri(storage_path)
        return self.client.generate_presigned_url(
            "get_object",
            Params={"Bucket": bucket_name, "Key": key},
            ExpiresIn=settings.s3_presigned_url_expires,
        )

    def delete_file(self, storage_path: str) -> None:
        bucket_name, key = parse_s3_uri(storage_path)
        self.client.delete_object(Bucket=bucket_name, Key=key)

    def read_file_bytes(self, storage_path: str) -> bytes:
        bucket_name, key = parse_s3_uri(storage_path)
        response = self.client.get_object(Bucket=bucket_name, Key=key)
        return response["Body"].read()


def parse_s3_uri(storage_path: str) -> tuple[str, str]:
    if not storage_path.startswith("s3://"):
        raise ValueError(f"Invalid S3 storage path: {storage_path}")
    without_scheme = storage_path.removeprefix("s3://")
    bucket_name, _, key = without_scheme.partition("/")
    if not bucket_name or not key:
        raise ValueError(f"Invalid S3 storage path: {storage_path}")
    return bucket_name, key


def get_storage_service() -> StorageService:
    if settings.storage_provider.lower() == "s3":
        return S3StorageService()
    return LocalStorageService()


def read_stored_file_bytes(storage_path: str | None) -> bytes:
    if not storage_path:
        raise ValueError("Document storage_path is empty")
    if storage_path.startswith("s3://"):
        return S3StorageService().read_file_bytes(storage_path)
    path = Path(storage_path)
    if not path.exists() or not path.is_file():
        raise FileNotFoundError(f"Document file does not exist: {storage_path}")
    return path.read_bytes()


def stored_file_url(request: Request, storage_path: str | None) -> str | None:
    if not storage_path:
        return None
    if storage_path.startswith("s3://"):
        return S3StorageService().generate_download_url(storage_path)
    normalized_path = storage_path.replace("\\", "/").lstrip("/")
    if normalized_path.startswith("static/"):
        return str(request.url_for("static", path=normalized_path.removeprefix("static/")))
    return str(request.base_url.replace(path=f"/{normalized_path}", query=""))


def remove_stored_file(storage_path: str | None) -> None:
    if not storage_path:
        return
    if storage_path.startswith("s3://"):
        S3StorageService().delete_file(storage_path)
        return
    path = Path(storage_path)
    if path.exists() and path.is_file():
        path.unlink()


def remove_local_file(storage_path: str | None) -> None:
    remove_stored_file(storage_path)


def remove_local_course_dir(course_id: int) -> None:
    course_dir = settings.upload_dir / "courses" / str(course_id)
    if course_dir.exists() and course_dir.is_dir():
        shutil.rmtree(course_dir)
