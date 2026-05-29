from functools import lru_cache

import boto3

from .config import settings


def _client_kwargs(public: bool = False) -> dict:
    kwargs: dict = {"region_name": settings.aws_region}
    endpoint = settings.aws_endpoint_url
    if public and settings.aws_s3_public_endpoint:
        endpoint = settings.aws_s3_public_endpoint
    if endpoint:
        kwargs["endpoint_url"] = endpoint
        kwargs["aws_access_key_id"] = settings.aws_access_key_id or "test"
        kwargs["aws_secret_access_key"] = settings.aws_secret_access_key or "test"
    elif settings.aws_access_key_id:
        kwargs["aws_access_key_id"] = settings.aws_access_key_id
        kwargs["aws_secret_access_key"] = settings.aws_secret_access_key
    return kwargs


@lru_cache
def _s3_internal():
    return boto3.client("s3", **_client_kwargs(public=False))


@lru_cache
def _s3_public():
    if settings.aws_s3_public_endpoint:
        return boto3.client("s3", **_client_kwargs(public=True))
    return _s3_internal()


def upload_bytes(key: str, data: bytes, content_type: str) -> str:
    _s3_internal().put_object(
        Bucket=settings.s3_bucket_evidencias,
        Key=key,
        Body=data,
        ContentType=content_type,
    )
    return key


def presigned_url(key: str, expires: int = 3600) -> str:
    return _s3_public().generate_presigned_url(
        "get_object",
        Params={"Bucket": settings.s3_bucket_evidencias, "Key": key},
        ExpiresIn=expires,
    )
