import hashlib
import uuid
from types import SimpleNamespace
from urllib.parse import urlparse

import pytest

from app.services import storage_service


class FakeBucket:
    def __init__(self):
        self.objects = {}
        self.upload_options = None

    def upload(self, path, file, file_options):
        self.objects[path] = file
        self.upload_options = file_options

    def download(self, path):
        return self.objects[path]

    def remove(self, paths):
        for path in paths:
            del self.objects[path]

    def create_signed_url(self, path, expires_in):
        return {"signedURL": f"https://storage.example/{path}?expires={expires_in}"}


class FakeStorage:
    def __init__(self):
        self.bucket = FakeBucket()
        self.requested_bucket = None

    def from_(self, bucket):
        self.requested_bucket = bucket
        return self.bucket


class FakeClient:
    def __init__(self):
        self.storage = FakeStorage()


@pytest.fixture
def fake_storage(monkeypatch, tmp_path):
    client = FakeClient()
    monkeypatch.setattr(storage_service, "_client", client)
    monkeypatch.setattr(
        storage_service,
        "get_settings",
        lambda: SimpleNamespace(
            SUPABASE_STORAGE_BUCKET="skin-images",
            SKIN_IMAGE_STORAGE_ROOT=str(tmp_path),
        ),
    )
    return client


def test_upload_download_delete_and_signed_url(fake_storage):
    image_bytes = b"validated image"
    session_id = uuid.uuid4()

    stored = storage_service.save_image_bytes(image_bytes, session_id, ".jpg")
    parsed_uri = urlparse(stored.storage_uri)
    object_path = parsed_uri.path.lstrip("/")

    assert parsed_uri.scheme == "supabase"
    assert parsed_uri.netloc == "skin-images"
    assert object_path.startswith(f"{session_id}/")
    assert object_path.endswith(".jpg")
    assert stored.sha256 == hashlib.sha256(image_bytes).hexdigest()
    assert stored.file_size_bytes == len(image_bytes)
    assert fake_storage.storage.bucket.upload_options == {
        "content-type": "image/jpeg",
        "upsert": "false",
    }

    assert storage_service.read_image_bytes(stored.storage_uri) == image_bytes
    assert storage_service.generate_signed_url(stored.storage_uri, 60).endswith(
        "?expires=60"
    )

    storage_service.delete_image(stored.storage_uri)
    assert fake_storage.storage.bucket.objects == {}


def test_upload_rejects_empty_or_unsupported_images():
    with pytest.raises(ValueError, match="empty image"):
        storage_service.save_image_bytes(b"", uuid.uuid4(), ".jpg")

    with pytest.raises(ValueError, match="Unsupported image extension"):
        storage_service.save_image_bytes(b"image", uuid.uuid4(), ".gif")


def test_storage_uri_validation_and_signed_url_expiry(fake_storage):
    with pytest.raises(ValueError, match="Invalid Supabase storage URI"):
        storage_service.read_image_bytes("supabase://skin-images/")

    with pytest.raises(ValueError, match="Unsupported storage URI scheme"):
        storage_service.delete_image("file:///tmp/image.jpg")

    with pytest.raises(ValueError, match="expiry"):
        storage_service.generate_signed_url(
            "supabase://skin-images/session/image.jpg",
            3601,
        )


def test_read_legacy_file_uri_only_within_configured_storage_root(
    fake_storage,
    monkeypatch,
    tmp_path,
):
    storage_root = tmp_path / "storage"
    storage_root.mkdir()
    monkeypatch.setattr(
        storage_service,
        "get_settings",
        lambda: SimpleNamespace(
            SUPABASE_STORAGE_BUCKET="skin-images",
            SKIN_IMAGE_STORAGE_ROOT=str(storage_root),
        ),
    )
    image_path = storage_root / "old-image.jpg"
    image_path.write_bytes(b"legacy image")
    outside_path = tmp_path / "outside-image.jpg"
    outside_path.write_bytes(b"not allowed")

    assert storage_service.read_image_bytes(image_path.as_uri()) == b"legacy image"

    with pytest.raises(ValueError, match="outside the configured storage root"):
        storage_service.read_image_bytes(outside_path.as_uri())
