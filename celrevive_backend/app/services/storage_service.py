import hashlib
import uuid
from pathlib import Path
from urllib.parse import ParseResult, quote, unquote, urlparse

from supabase import Client, create_client

from app.core.config import get_settings

_client: Client | None = None


class StoredImage:
    def __init__(
        self,
        storage_uri: str,
        sha256: str,
        file_size_bytes: int,
    ):
        self.storage_uri = storage_uri
        self.sha256 = sha256
        self.file_size_bytes = file_size_bytes


def get_storage_client() -> Client:
    """Return a lazily initialized Supabase client."""
    global _client

    if _client is None:
        settings = get_settings()

        if not settings.SUPABASE_URL:
            raise RuntimeError("SUPABASE_URL is not configured")

        if not settings.SUPABASE_SERVICE_ROLE_KEY:
            raise RuntimeError(
                "SUPABASE_SERVICE_ROLE_KEY is not configured"
            )

        _client = create_client(
            settings.SUPABASE_URL,
            settings.SUPABASE_SERVICE_ROLE_KEY,
        )

    return _client


def save_image_bytes(
    image_bytes: bytes,
    session_id: uuid.UUID,
    extension: str,
) -> StoredImage:
    """Upload validated image bytes to private Supabase Storage."""

    if not image_bytes:
        raise ValueError("Cannot upload an empty image")

    extension = extension.lower()

    allowed_extensions = {
        ".jpg": "image/jpeg",
        ".jpeg": "image/jpeg",
        ".png": "image/png",
        ".webp": "image/webp",
    }

    if extension not in allowed_extensions:
        raise ValueError(f"Unsupported image extension: {extension}")

    sha256 = hashlib.sha256(image_bytes).hexdigest()

    filename = f"{uuid.uuid4().hex}{extension}"
    object_path = f"{session_id}/{filename}"

    settings = get_settings()
    bucket = settings.SUPABASE_STORAGE_BUCKET

    client = get_storage_client()

    client.storage.from_(bucket).upload(
        path=object_path,
        file=image_bytes,
        file_options={
            "content-type": allowed_extensions[extension],
            "upsert": "false",
        },
    )

    # Store a provider-independent locator rather than a local file URI.
    storage_uri = (
        f"supabase://{bucket}/{quote(object_path, safe='/')}"
    )

    return StoredImage(
        storage_uri=storage_uri,
        sha256=sha256,
        file_size_bytes=len(image_bytes),
    )


def read_image_bytes(storage_uri: str) -> bytes:
    """Download an image from Supabase or a still-present legacy local path."""

    parsed = urlparse(storage_uri)

    if parsed.scheme == "file":
        settings = get_settings()
        legacy_path = unquote(parsed.path or parsed.netloc)
        if (
            len(legacy_path) > 2
            and legacy_path[0] == "/"
            and legacy_path[2] == ":"
        ):
            legacy_path = legacy_path[1:]
        path = Path(legacy_path).resolve()
        storage_root = Path(settings.SKIN_IMAGE_STORAGE_ROOT).resolve()
        if path != storage_root and storage_root not in path.parents:
            raise ValueError(
                "Legacy image path is outside the configured storage root"
            )
        return path.read_bytes()

    if parsed.scheme != "supabase":
        raise ValueError(
            f"Unsupported storage URI scheme: {parsed.scheme}"
        )

    bucket, object_path = _parse_supabase_uri(parsed)
    client = get_storage_client()

    return client.storage.from_(bucket).download(object_path)


def delete_image(storage_uri: str) -> None:
    """Delete an image from Supabase Storage."""

    parsed = urlparse(storage_uri)

    if parsed.scheme != "supabase":
        raise ValueError(
            f"Unsupported storage URI scheme: {parsed.scheme}"
        )

    bucket, object_path = _parse_supabase_uri(parsed)
    client = get_storage_client()

    client.storage.from_(bucket).remove([object_path])


def generate_signed_url(
    storage_uri: str,
    expires_in: int = 300,
) -> str:
    """Generate a temporary URL for accessing a private image."""

    parsed = urlparse(storage_uri)

    if parsed.scheme != "supabase":
        raise ValueError(
            f"Unsupported storage URI scheme: {parsed.scheme}"
        )

    if not 1 <= expires_in <= 3600:
        raise ValueError("Signed URL expiry must be between 1 and 3600 seconds")

    bucket, object_path = _parse_supabase_uri(parsed)
    client = get_storage_client()

    result = client.storage.from_(bucket).create_signed_url(
        object_path,
        expires_in,
    )

    signed_url = result.get("signedURL") or result.get("signedUrl")

    if not signed_url:
        raise RuntimeError("Supabase did not return a signed URL")

    return signed_url


def _parse_supabase_uri(parsed: ParseResult) -> tuple[str, str]:
    bucket = parsed.netloc
    object_path = unquote(parsed.path.lstrip("/"))

    if not bucket or not object_path:
        raise ValueError("Invalid Supabase storage URI")

    return bucket, object_path
