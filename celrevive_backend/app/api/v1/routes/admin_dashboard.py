import hashlib
import hmac
import os
import time
from pathlib import Path
import uuid
from typing import Optional
from urllib.parse import urlencode

from fastapi import APIRouter, Depends, HTTPException, Query, Request, Response, status
from fastapi.responses import HTMLResponse
from fastapi.templating import Jinja2Templates
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.database import get_db
from app.models.customer_session import CustomerSession
from app.models.skin_image import SkinImage
from app.repositories.admin_repository import (
    get_recent_sessions_summary,
    get_session_combined_concerns,
    get_session_detections_by_source,
    get_session_latest_image,
    get_session_questionnaire_answers,
    get_session_recommendation_detail,
)
from app.services.shopify_order_service import exchange_session_token_for_offline_token
from app.services.storage_service import read_image_bytes

router = APIRouter(tags=["admin-dashboard"])

TEMPLATES_DIR = Path(__file__).resolve().parents[3] / "templates"
templates = Jinja2Templates(directory=str(TEMPLATES_DIR))

ADMIN_COOKIE_NAME = "celrevive_admin_session"
ADMIN_COOKIE_TTL_SECONDS = 12 * 60 * 60  # 12 hours


def _get_signing_secret() -> str:
    return (
        os.getenv("SHOPIFY_API_SECRET", "").strip()
        or os.getenv("SHOPIFY_CLIENT_SECRET", "").strip()
    )


def _verify_shopify_admin_hmac(request: Request) -> bool:
    """
    Verifies the cryptographic `hmac` query parameter sent by Shopify Admin
    when opening an embedded app.
    """
    secret = _get_signing_secret()
    received_hmac = request.query_params.get("hmac")
    if not secret or not received_hmac:
        return False

    # Construct sorted query string excluding 'hmac' and 'signature'
    filtered_params = sorted(
        (k, v)
        for k, v in request.query_params.multi_items()
        if k not in ("hmac", "signature")
    )
    message = urlencode(filtered_params)
    computed_hmac = hmac.new(
        secret.encode("utf-8"),
        message.encode("utf-8"),
        hashlib.sha256,
    ).hexdigest()

    return hmac.compare_digest(computed_hmac, received_hmac)


def _create_signed_cookie_value(shop: str) -> str:
    secret = _get_signing_secret()
    expires_at = int(time.time()) + ADMIN_COOKIE_TTL_SECONDS
    payload = f"{shop}:{expires_at}"
    sig = hmac.new(
        secret.encode("utf-8"),
        payload.encode("utf-8"),
        hashlib.sha256,
    ).hexdigest()
    return f"{payload}:{sig}"


def _verify_signed_cookie(request: Request) -> bool:
    secret = _get_signing_secret()
    cookie_val = request.cookies.get(ADMIN_COOKIE_NAME)
    if not secret or not cookie_val:
        return False

    parts = cookie_val.split(":")
    if len(parts) != 3:
        return False

    shop, expires_str, received_sig = parts
    try:
        expires_at = int(expires_str)
    except ValueError:
        return False

    if time.time() > expires_at:
        return False

    payload = f"{shop}:{expires_str}"
    expected_sig = hmac.new(
        secret.encode("utf-8"),
        payload.encode("utf-8"),
        hashlib.sha256,
    ).hexdigest()

    return hmac.compare_digest(expected_sig, received_sig)


@router.get("/", response_class=HTMLResponse, include_in_schema=False)
@router.get("/admin", response_class=HTMLResponse)
async def render_admin_dashboard(
    request: Request,
    ref: Optional[str] = Query(
        default=None,
        description="Internal scan reference (defaults to latest upload).",
    ),
    min_confidence: float = Query(
        default=0.5,
        ge=0.0,
        le=1.0,
    ),
    db: AsyncSession = Depends(get_db),
) -> HTMLResponse:
    is_valid_shopify_hmac = _verify_shopify_admin_hmac(request)
    is_valid_cookie = _verify_signed_cookie(request)

    if not (is_valid_shopify_hmac or is_valid_cookie):
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail="Unauthorized: Please open this dashboard from Shopify Admin -> Apps.",
        )

    shop = request.query_params.get(
        "shop", os.getenv("SHOPIFY_STORE_DOMAIN", "46a312.myshopify.com")
    )
    id_token = request.query_params.get("id_token")
    if id_token and not os.getenv("SHOPIFY_ADMIN_ACCESS_TOKEN", "").strip():
        await exchange_session_token_for_offline_token(shop, id_token)

    recent_sessions = await get_recent_sessions_summary(db, limit=30)

    target_uuid: Optional[uuid.UUID] = None
    active_scan_label = "Latest Scan"
    active_customer_label = "Guest Tester"

    if ref and ref.strip():
        try:
            target_uuid = uuid.UUID(ref.strip())
        except ValueError:
            target_uuid = None

    if target_uuid is None and recent_sessions:
        target_uuid = uuid.UUID(recent_sessions[0]["_internal_id"])

    for item in recent_sessions:
        if target_uuid and item["_internal_id"] == str(target_uuid):
            active_scan_label = item["scan_label"]
            active_customer_label = item["customer_label"]
            break

    selected_session = None
    latest_image = None
    image_detections = []
    questionnaire_detections = []
    questionnaire_answers = []
    combined_concerns = []
    recommendation_detail = None

    if target_uuid:
        session_res = await db.execute(
            select(CustomerSession).where(CustomerSession.session_id == target_uuid)
        )
        selected_session = session_res.scalar_one_or_none()

        if selected_session is not None:
            latest_image = await get_session_latest_image(db, session_id=target_uuid)
            image_detections = await get_session_detections_by_source(
                db, session_id=target_uuid, detection_source="IMAGE"
            )
            questionnaire_detections = await get_session_detections_by_source(
                db, session_id=target_uuid, detection_source="QUESTIONNAIRE"
            )
            questionnaire_answers = await get_session_questionnaire_answers(
                db, session_id=target_uuid
            )
            combined_concerns = await get_session_combined_concerns(
                db, session_id=target_uuid
            )
            recommendation_detail = await get_session_recommendation_detail(
                db, session_id=target_uuid
            )

    response = templates.TemplateResponse(
        request=request,
        name="admin_dashboard.html",
        context={
            "recent_sessions": recent_sessions,
            "selected_session": selected_session,
            "selected_ref": str(target_uuid) if target_uuid else "",
            "active_scan_label": active_scan_label,
            "active_customer_label": active_customer_label,
            "latest_image": latest_image,
            "image_detections": image_detections,
            "questionnaire_detections": questionnaire_detections,
            "questionnaire_answers": questionnaire_answers,
            "combined_concerns": combined_concerns,
            "recommendation_detail": recommendation_detail,
            "default_min_confidence": min_confidence,
        },
    )

    # Issue/refresh the 12-hour HttpOnly cookie whenever verified via Shopify HMAC
    if is_valid_shopify_hmac:
        response.set_cookie(
            key=ADMIN_COOKIE_NAME,
            value=_create_signed_cookie_value(shop),
            max_age=ADMIN_COOKIE_TTL_SECONDS,
            httponly=True,
            secure=True,
            samesite="none",
        )

    return response


@router.get("/admin/image/{image_id}")
async def serve_session_image(
    image_id: uuid.UUID,
    request: Request,
    db: AsyncSession = Depends(get_db),
) -> Response:
    if not (_verify_signed_cookie(request) or _verify_shopify_admin_hmac(request)):
        raise HTTPException(status_code=status.HTTP_403_FORBIDDEN, detail="Unauthorized")

    res = await db.execute(select(SkinImage).where(SkinImage.image_id == image_id))
    skin_image = res.scalar_one_or_none()
    if skin_image is None or not skin_image.storage_uri:
        raise HTTPException(status_code=404, detail="Image not found")

    try:
        image_bytes = read_image_bytes(skin_image.storage_uri)
    except Exception as exc:
        raise HTTPException(status_code=404, detail="Image file missing from disk") from exc

    media_type = skin_image.mime_type or "image/jpeg"
    return Response(content=image_bytes, media_type=media_type)