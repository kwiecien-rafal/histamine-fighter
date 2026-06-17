"""Admin login: credentials in, signed access token out."""

import structlog
from fastapi import APIRouter, Depends, HTTPException, Request, status

from app.core.ratelimit import auth_rate_limit, limiter
from app.core.security import create_access_token
from app.dependencies import get_admin_service
from app.schemas.admin import AdminLoginRequest, TokenResponse
from app.services.admin_service import AdminService

log = structlog.get_logger(__name__)

router = APIRouter(prefix="/admin/auth", tags=["admin"])


@router.post("/login", response_model=TokenResponse)
@limiter.limit(auth_rate_limit)
async def login(
    request: Request,
    payload: AdminLoginRequest,
    admin_service: AdminService = Depends(get_admin_service),
) -> TokenResponse:
    """Exchange admin credentials for a bearer token.

    A wrong email and a wrong password give the same 401, so the response never
    reveals which accounts exist.
    """
    client = request.client.host if request.client else None
    admin = await admin_service.authenticate(payload.email, payload.password)
    if admin is None:
        # Log the attempted email and source IP so brute force is visible; never
        # log the password.
        log.warning("admin.login.failed", email=payload.email, client=client)
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="Incorrect email or password.",
        )
    log.info("admin.login.success", email=admin.email, client=client)
    return TokenResponse(access_token=create_access_token(admin.email))
