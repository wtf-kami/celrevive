import uuid
from types import SimpleNamespace
from unittest.mock import AsyncMock, Mock

import pytest

from app.services import image_intake_service
from app.services.storage_service import StoredImage


@pytest.fixture
def intake_dependencies(monkeypatch):
    session_result = Mock()
    session_result.scalar_one_or_none.return_value = object()
    db = Mock()
    db.execute = AsyncMock(return_value=session_result)
    db.commit = AsyncMock()
    db.rollback = AsyncMock()
    image = SimpleNamespace(image_id=uuid.uuid4())
    stored = StoredImage(
        storage_uri="supabase://skin-images/session/image.png",
        sha256="digest",
        file_size_bytes=5,
    )
    save_image = Mock(return_value=stored)
    create_image = AsyncMock(return_value=image)
    mark_received = AsyncMock()
    delete_image = Mock()

    monkeypatch.setattr(image_intake_service, "save_image_bytes", save_image)
    monkeypatch.setattr(image_intake_service, "create_skin_image", create_image)
    monkeypatch.setattr(
        image_intake_service,
        "mark_session_image_received",
        mark_received,
    )
    monkeypatch.setattr(image_intake_service, "delete_image", delete_image)

    return db, image, stored, save_image, create_image, mark_received, delete_image


@pytest.mark.asyncio
async def test_accept_validated_image_uploads_before_commit_and_queues_analysis(
    intake_dependencies,
):
    (
        db,
        image,
        stored,
        save_image,
        create_image,
        mark_received,
        _,
    ) = intake_dependencies
    background_tasks = Mock()
    session_id = uuid.uuid4()

    result = await image_intake_service.accept_validated_image(
        db,
        background_tasks,
        session_id=session_id,
        image_bytes=b"image",
        original_filename="skin.gif",
        mime_type="image/png",
        image_width=600,
        image_height=600,
    )

    assert result == image.image_id
    save_image.assert_called_once_with(b"image", session_id, ".png")
    create_image.assert_awaited_once()
    assert create_image.await_args.kwargs["storage_uri"] == stored.storage_uri
    mark_received.assert_awaited_once_with(db, session_id=session_id)
    db.commit.assert_awaited_once()
    background_tasks.add_task.assert_called_once_with(
        image_intake_service.run_visual_ai_analysis,
        image.image_id,
        session_id,
    )


@pytest.mark.asyncio
async def test_database_failure_rolls_back_and_deletes_uploaded_image(
    intake_dependencies,
):
    db, _, stored, _, _, _, delete_image = intake_dependencies
    db.commit.side_effect = RuntimeError("database unavailable")

    with pytest.raises(RuntimeError, match="database unavailable"):
        await image_intake_service.accept_validated_image(
            db,
            Mock(),
            session_id=uuid.uuid4(),
            image_bytes=b"image",
            original_filename="skin.jpg",
            mime_type="image/jpeg",
            image_width=600,
            image_height=600,
        )

    db.rollback.assert_awaited_once()
    delete_image.assert_called_once_with(stored.storage_uri)
