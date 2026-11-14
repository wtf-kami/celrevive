import logging
import uuid

from fastapi import APIRouter, BackgroundTasks, Depends, HTTPException, status
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.database import get_db
from app.models.customer_session import CustomerSession
from app.models.questionnaire_response import QuestionnaireResponse
from app.repositories.recommendation_repository import (
    get_combined_skin_concerns,
    get_latest_recommendation_output,
)
from app.repositories.skin_concern_detection_repository import (
    get_detected_image_concerns_by_session,
)
from app.schemas.customer_session import (
    CustomerSessionCreate,
    CustomerSessionResponse,
    ImageAnalysisStatusResponse,
)
from app.schemas.recommendation import RecommendationOutputResponse
from app.schemas.questionnaire import (
    QuestionnaireSubmission,
    QuestionnaireSubmissionResponse,
)
from app.services.questionnaire_ai.client import (
    QUESTIONNAIRE_VERSION,
    validate_questionnaire_payload,
)
from app.workers.recommendation_task import run_questionnaire_analysis
from app.services.shopify_order_service import create_and_complete_zero_cost_order

router = APIRouter()
logger = logging.getLogger("uvicorn.error")


@router.post(
    "/create-session",
    response_model=CustomerSessionResponse,
    status_code=status.HTTP_201_CREATED,
)
async def create_customer_session(
    session_data: CustomerSessionCreate,
    db: AsyncSession = Depends(get_db),
) -> CustomerSessionResponse:
    if not session_data.consent_given:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="Consent is required to create a customer session.",
        )

    session = CustomerSession(
        session_id=uuid.uuid4(),
        shopify_customer_id=session_data.shopify_customer_id,
        shopify_customer_name=session_data.shopify_customer_name,
        shopify_customer_email=session_data.shopify_customer_email,
        shopify_cart_id=session_data.shopify_cart_id,
        shopify_checkout_id=session_data.shopify_checkout_id,
        consent_given=session_data.consent_given,
        session_status="STARTED",
        notes=session_data.notes,
    )
    db.add(session)
    await db.commit()

    return CustomerSessionResponse(
        session_id=session.session_id,
        session_status=session.session_status,
    )


@router.get(
    "/sessions/{session_id}/analysis",
    response_model=ImageAnalysisStatusResponse,
    summary="Get image and combined concern status",
    description=(
        "Poll this endpoint to check image analysis. Image analysis starts "
        "automatically after an accepted image upload; questionnaire analysis "
        "starts after questionnaire submission. `combined_skin_concerns` remains "
        "null until both detection results have been combined. This endpoint "
        "reports status and does not start analysis."
    ),
)
async def get_image_analysis_status(
    session_id: uuid.UUID,
    db: AsyncSession = Depends(get_db),
) -> ImageAnalysisStatusResponse:
    result = await db.execute(
        select(CustomerSession).where(CustomerSession.session_id == session_id)
    )
    session = result.scalar_one_or_none()
    if session is None:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail="Customer session not found.",
        )

    if session.session_status == "VALIDATION_FAILED":
        return ImageAnalysisStatusResponse(
            session_id=session_id,
            analysis_status="validation_failed",
            message="Image validation failed. Please upload a valid image.",
        )
    if session.session_status == "ANALYSIS_FAILED":
        return ImageAnalysisStatusResponse(
            session_id=session_id,
            analysis_status="analysis_failed",
            message="Image analysis failed. Please upload the image again.",
        )
    if session.session_status == "ERROR":
        image_concerns = await get_detected_image_concerns_by_session(
            db,
            session_id=session_id,
        )
        combined_concerns = await get_combined_skin_concerns(
            db,
            session_id=session_id,
        )
        return ImageAnalysisStatusResponse(
            session_id=session_id,
            analysis_status="analysis_failed",
            message="Questionnaire or recommendation analysis failed.",
            primary_concerns=image_concerns,
            combined_skin_concerns=combined_concerns,
        )
    if session.session_status in {
        "RECOMMENDATION_GENERATED",
        "CONCERNS_COMBINED",
        "ORDER_PLACED",
    }:
        concerns = await get_detected_image_concerns_by_session(
            db,
            session_id=session_id,
        )
        combined_concerns = await get_combined_skin_concerns(
            db,
            session_id=session_id,
        )
        return ImageAnalysisStatusResponse(
            session_id=session_id,
            analysis_status="completed",
            message="Image analysis completed.",
            primary_concerns=concerns,
            combined_skin_concerns=combined_concerns,
        )
    if session.session_status == "IMAGE_RECEIVED":
        return ImageAnalysisStatusResponse(
            session_id=session_id,
            analysis_status="pending",
            message="Image analysis is in progress.",
        )
    if session.session_status == "PROCESSING":
        return ImageAnalysisStatusResponse(
            session_id=session_id,
            analysis_status="processing",
            message="Image analysis is in progress.",
        )
    raise HTTPException(
        status_code=status.HTTP_409_CONFLICT,
        detail="Image analysis has not been submitted for this session.",
    )


@router.post(
    "/sessions/{session_id}/questionnaire",
    response_model=QuestionnaireSubmissionResponse,
    status_code=status.HTTP_202_ACCEPTED,
)
async def submit_questionnaire(
    session_id: uuid.UUID,
    submission: QuestionnaireSubmission,
    background_tasks: BackgroundTasks,
    db: AsyncSession = Depends(get_db),
) -> QuestionnaireSubmissionResponse:
    if submission.questionnaire_version != QUESTIONNAIRE_VERSION:
        raise HTTPException(
            status_code=status.HTTP_422_UNPROCESSABLE_ENTITY,
            detail=f"Unsupported questionnaire_version. Expected {QUESTIONNAIRE_VERSION}.",
        )
    try:
        validated_response_json = validate_questionnaire_payload(submission.response_json)
    except ValueError as exc:
        raise HTTPException(
            status_code=status.HTTP_422_UNPROCESSABLE_ENTITY,
            detail=str(exc),
        ) from exc

    session_result = await db.execute(
        select(CustomerSession)
        .where(CustomerSession.session_id == session_id)
        .with_for_update()
    )
    session = session_result.scalar_one_or_none()
    if session is None:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail="Customer session not found.",
        )
    if session.session_status not in {
        "IMAGE_RECEIVED",
        "PROCESSING",
        "RECOMMENDATION_GENERATED",
    }:
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT,
            detail="Questionnaire can only be submitted after an image is accepted.",
        )

    existing_result = await db.execute(
        select(QuestionnaireResponse.questionnaire_response_id)
        .where(QuestionnaireResponse.session_id == session_id)
        .limit(1)
    )
    if existing_result.scalar_one_or_none() is not None:
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT,
            detail="A questionnaire response has already been submitted for this session.",
        )

    response = QuestionnaireResponse(
        session_id=session_id,
        questionnaire_version=submission.questionnaire_version,
        response_json=validated_response_json,
    )
    db.add(response)
    await db.commit()
    await db.refresh(response)
    background_tasks.add_task(
        run_questionnaire_analysis,
        response.questionnaire_response_id,
        session_id,
    )
    return QuestionnaireSubmissionResponse(
        session_id=session_id,
        questionnaire_status="processing",
        message="Questionnaire received. Recommendation generation is in progress.",
    )


@router.get(
    "/sessions/{session_id}/recommendation",
    response_model=RecommendationOutputResponse,
    summary="Get the final recommendation for the Shopify widget",
    description=(
        "Returns the persisted final recommendation for a session. Returns "
        "409 while recommendation generation is still in progress."
    ),
)
async def get_session_recommendation(
    session_id: uuid.UUID,
    db: AsyncSession = Depends(get_db),
) -> RecommendationOutputResponse:
    session_result = await db.execute(
        select(CustomerSession).where(CustomerSession.session_id == session_id)
    )
    session = session_result.scalar_one_or_none()
    if session is None:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail="Customer session not found.",
        )

    recommendation = await get_latest_recommendation_output(
        db,
        session_id=session_id,
    )
    if recommendation is not None:
        return RecommendationOutputResponse.model_validate(recommendation)
    if session.session_status == "ERROR":
        raise HTTPException(
            status_code=status.HTTP_502_BAD_GATEWAY,
            detail="Questionnaire or recommendation analysis failed.",
        )
    raise HTTPException(
        status_code=status.HTTP_409_CONFLICT,
        detail="Recommendation is not available yet.",
    )


@router.post(
    "/sessions/{session_id}/place-order",
    status_code=status.HTTP_200_OK,
)
async def place_session_order(
    session_id: uuid.UUID,
    db: AsyncSession = Depends(get_db),
) -> dict:
    try:
        # Lock the session row so concurrent requests cannot place duplicate orders
        session_result = await db.execute(
            select(CustomerSession)
            .where(CustomerSession.session_id == session_id)
            .with_for_update()
        )
        session = session_result.scalar_one_or_none()
        if session is None:
            raise HTTPException(
                status_code=status.HTTP_404_NOT_FOUND,
                detail="Customer session not found.",
            )

        if session.session_status == "ORDER_PLACED" or session.shopify_checkout_id:
            raise HTTPException(
                status_code=status.HTTP_409_CONFLICT,
                detail="An order has already been placed for this session.",
            )

        rec_response = await get_session_recommendation(
            session_id=session_id,
            db=db,
        )
        rec_data = (
            rec_response.model_dump()
            if hasattr(rec_response, "model_dump")
            else dict(rec_response)
        )

        order_result = await create_and_complete_zero_cost_order(
            session_id=str(session_id),
            shopify_customer_id=getattr(session, "shopify_customer_id", None),
            shopify_customer_email=getattr(session, "shopify_customer_email", None),
            shopify_customer_name=getattr(session, "shopify_customer_name", None),
            skin_type=rec_data.get("skin_type"),
            skin_concerns=rec_data.get("skin_concerns") or [],
            recommended_base=rec_data.get("recommended_base") or {},
            formulation_weight_g=rec_data.get("formulation_weight_g"),
            recommended_actives=rec_data.get("recommended_actives") or [],
        )

        # Persist order placement in the database to permanently block duplicates
        session.session_status = "ORDER_PLACED"
        session.shopify_checkout_id = str(
            order_result.get("order_id") or order_result.get("draft_order_id") or "PLACED"
        )
        await db.commit()

        return order_result
    except HTTPException:
        raise
    except Exception as exc:
        logger.exception("place_session_order failed")
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail=f"place_session_order error: {type(exc).__name__}: {str(exc)}",
        )