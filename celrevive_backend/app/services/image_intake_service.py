import asyncio
import logging
import uuid
from pathlib import Path

from fastapi import BackgroundTasks
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.models.customer_session import CustomerSession
from app.repositories.skin_image_repository import create_skin_image, mark_session_image_received
from app.services.storage_service import delete_image, save_image_bytes
from app.workers.visual_ai_task import run_visual_ai_analysis

logger = logging.getLogger(__name__)

_EXTENSION_BY_MIME_TYPE = {
	"image/jpeg": ".jpg",
	"image/png": ".png",
	"image/webp": ".webp",
}


async def accept_validated_image(
	db: AsyncSession,
	background_tasks: BackgroundTasks,
	*,
	session_id: uuid.UUID,
	image_bytes: bytes,
	original_filename: str | None,
	mime_type: str | None,
	image_width: int | None,
	image_height: int | None,
) -> uuid.UUID:
	session_result = await db.execute(
		select(CustomerSession).where(CustomerSession.session_id == session_id)
	)
	if session_result.scalar_one_or_none() is None:
		raise ValueError("Customer session does not exist")

	extension = _EXTENSION_BY_MIME_TYPE.get(
		mime_type or "",
		Path(original_filename or "").suffix.lower() or ".bin",
	)
	stored = await asyncio.to_thread(
		save_image_bytes,
		image_bytes,
		session_id,
		extension,
	)

	try:
		image = await create_skin_image(
			db,
			session_id=session_id,
			storage_uri=stored.storage_uri,
			original_filename=original_filename,
			mime_type=mime_type,
			file_size_bytes=stored.file_size_bytes,
			image_width=image_width,
			image_height=image_height,
			sha256=stored.sha256,
		)
		await mark_session_image_received(db, session_id=session_id)
		await db.commit()
	except Exception:
		await db.rollback()
		try:
			await asyncio.to_thread(delete_image, stored.storage_uri)
		except Exception:
			logger.exception(
				"Failed to clean up uploaded image after database failure"
			)
		raise

	background_tasks.add_task(run_visual_ai_analysis, image.image_id, session_id)
	return image.image_id
