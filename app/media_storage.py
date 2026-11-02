"""Private MinIO/S3 photo storage for Telegram source messages.

Only Telegram native photos (not arbitrary files) are ingested. The source is
trusted for transport, but byte type and size are validated before upload.
Database stores an object key, never a public unauthenticated URL.
"""
import hashlib
import io
import logging
from datetime import datetime, timezone

from sqlalchemy import select

from app.config import settings
from app.models import SourceMedia

log = logging.getLogger(__name__)


def image_type(data: bytes) -> tuple[str, str]:
    if data.startswith(b'\xff\xd8\xff'):
        return 'image/jpeg', 'jpg'
    if data.startswith(b'\x89PNG\r\n\x1a\n'):
        return 'image/png', 'png'
    if data[:4] == b'RIFF' and data[8:12] == b'WEBP':
        return 'image/webp', 'webp'
    raise ValueError('Неизвестный формат изображения Telegram')


def client():
    """MinIO S3 client; no public buckets and no local credential logging."""
    import boto3
    if not settings.minio_root_user or not settings.minio_root_password:
        raise RuntimeError('MINIO_ROOT_USER/MINIO_ROOT_PASSWORD не заданы')
    return boto3.client('s3', endpoint_url=(('https' if settings.minio_secure else 'http') +
                        '://' + settings.minio_endpoint),
                        aws_access_key_id=settings.minio_root_user,
                        aws_secret_access_key=settings.minio_root_password,
                        region_name='us-east-1')


def ensure_bucket(s3):
    from botocore.exceptions import ClientError
    try:
        s3.head_bucket(Bucket=settings.minio_bucket)
    except ClientError as exc:
        if exc.response.get('ResponseMetadata', {}).get('HTTPStatusCode') != 404:
            raise
        s3.create_bucket(Bucket=settings.minio_bucket)


def store_photo(db, source_message, data: bytes, *, telegram_photo_id=None, s3=None) -> SourceMedia:
    if not isinstance(data, bytes) or not data or len(data) > settings.telegram_media_max_bytes:
        raise ValueError('Фото Telegram превышает допустимый размер или пустое')
    mime, ext = image_type(data)
    digest = hashlib.sha256(data).hexdigest()
    current = db.scalar(select(SourceMedia).where(SourceMedia.source_message_id == source_message.id)
                        .with_for_update())
    if current and current.sha256 == digest:
        current.telegram_photo_id = telegram_photo_id
        return current
    key = f'telegram/{source_message.source_id}/{source_message.message_id}/{digest}.{ext}'
    s3 = s3 or client()
    ensure_bucket(s3)
    s3.put_object(Bucket=settings.minio_bucket, Key=key, Body=io.BytesIO(data),
                  ContentType=mime)
    if current is None:
        current = SourceMedia(source_message_id=source_message.id, object_key=key,
                              content_type=mime, size_bytes=len(data), sha256=digest,
                              telegram_photo_id=telegram_photo_id)
        db.add(current)
    else:
        current.object_key = key
        current.content_type = mime
        current.size_bytes = len(data)
        current.sha256 = digest
        current.telegram_photo_id = telegram_photo_id
        current.stored_at = datetime.now(timezone.utc)
    db.flush()
    return current


def signed_media_url(source_message_id, db, *, s3=None, ttl_seconds=300):
    if not 30 <= ttl_seconds <= 900:
        raise ValueError('Недопустимый срок жизни ссылки')
    media = db.scalar(select(SourceMedia).where(SourceMedia.source_message_id == source_message_id))
    if not media:
        return None
    s3 = s3 or client()
    return {
        'content_type': media.content_type, 'size_bytes': media.size_bytes,
        'url': s3.generate_presigned_url('get_object', Params={
            'Bucket': settings.minio_bucket, 'Key': media.object_key,
        }, ExpiresIn=ttl_seconds),
        'expires_in': ttl_seconds,
    }
