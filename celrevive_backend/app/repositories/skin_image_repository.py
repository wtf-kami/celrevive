import asyncio
import uuid

from sqlalchemy import select, text, update
from sqlalchemy.ext.asyncio import AsyncSession

from app.models.customer_session import CustomerSession
from app.models.skin_image import SkinImage
from app.services.storage_service import read_image_bytes


async def create_skin_image(
    db: AsyncSession,
    *,
    session_id: uuid.UUID,
    storage_uri: str,
    original_filename: str | None,
    mime_type: str | None,
    file_size_bytes: int | None,
    image_width: int | None,
    image_height: int | None,
    sha256: str | None,
) -> SkinImage:
    skin_image = SkinImage(
        session_id=session_id,
        storage_uri=storage_uri,
        original_filename=original_filename,
        mime_type=mime_type,
        file_size_bytes=file_size_bytes,
        image_width=image_width,
        image_height=image_height,
        sha256=sha256,
    )
    db.add(skin_image)
    await db.flush()
    return skin_image


async def mark_session_image_received(db: AsyncSession, *, session_id: uuid.UUID) -> None:
    await db.execute(
        update(CustomerSession)
        .where(CustomerSession.session_id == session_id)
        .values(session_status="IMAGE_RECEIVED", updated_at=text("CURRENT_TIMESTAMP"))
    )


async def mark_session_validation_failed(
    db: AsyncSession,
    *,
    session_id: uuid.UUID,
) -> None:
    result = await db.execute(
        update(CustomerSession)
        .where(CustomerSession.session_id == session_id)
        .values(session_status="VALIDATION_FAILED", updated_at=text("CURRENT_TIMESTAMP"))
        .returning(CustomerSession.session_id)
    )
    if result.scalar_one_or_none() is None:
        raise ValueError("Customer session does not exist")
    await db.commit()


async def get_image_bytes_and_mime(
    db: AsyncSession,
    *,
    image_id: uuid.UUID,
) -> tuple[bytes, str]:
    result = await db.execute(select(SkinImage).where(SkinImage.image_id == image_id))
    image = result.scalar_one()
    image_bytes = await asyncio.to_thread(read_image_bytes, image.storage_uri)
    return image_bytes, image.mime_type or "application/octet-stream"
