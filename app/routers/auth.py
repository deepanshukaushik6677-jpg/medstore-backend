import jwt
from fastapi import APIRouter, Depends, HTTPException, status
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from ..database import get_db
from ..dependencies import CurrentPrincipal, get_current_principal
from ..models import PlatformAdmin, Store, StoreUser
from ..schemas.auth import (
    AdminLoginRequest,
    CurrentUserResponse,
    RefreshRequest,
    StoreUserLoginRequest,
    TokenResponse,
)
from ..security import create_access_token, create_refresh_token, decode_token, verify_password

router = APIRouter(prefix="/auth", tags=["auth"])


async def _build_store_user_response(user: StoreUser, db: AsyncSession) -> CurrentUserResponse:
    store = await db.get(Store, user.store_id)
    return CurrentUserResponse(
        id=str(user.id),
        role=user.role.value,
        store_id=str(user.store_id),
        name=user.name,
        tour_completed=user.tour_completed,
        default_bill_type=store.default_bill_type.value if store else None,
        gstin_set=bool(store.gstin) if store else False,
    )


@router.post("/login", response_model=TokenResponse)
async def login_store_user(payload: StoreUserLoginRequest, db: AsyncSession = Depends(get_db)):
    """Owner and Staff both log in here — role comes from the account, not the endpoint."""
    result = await db.execute(select(StoreUser).where(StoreUser.phone == payload.phone))
    user = result.scalar_one_or_none()
    if user is None or not user.is_active or not verify_password(payload.password, user.password_hash):
        raise HTTPException(status.HTTP_401_UNAUTHORIZED, "Incorrect phone or password")
    return TokenResponse(
        access_token=create_access_token(str(user.id), user.role.value, str(user.store_id)),
        refresh_token=create_refresh_token(str(user.id), user.role.value, str(user.store_id)),
    )


@router.post("/admin/login", response_model=TokenResponse)
async def login_admin(payload: AdminLoginRequest, db: AsyncSession = Depends(get_db)):
    result = await db.execute(select(PlatformAdmin).where(PlatformAdmin.email == payload.email))
    admin = result.scalar_one_or_none()
    if admin is None or not admin.is_active or not verify_password(payload.password, admin.password_hash):
        raise HTTPException(status.HTTP_401_UNAUTHORIZED, "Incorrect email or password")
    return TokenResponse(
        access_token=create_access_token(str(admin.id), "admin", None),
        refresh_token=create_refresh_token(str(admin.id), "admin", None),
    )


@router.post("/refresh", response_model=TokenResponse)
async def refresh_token(payload: RefreshRequest):
    try:
        data = decode_token(payload.refresh_token)
    except jwt.PyJWTError:
        raise HTTPException(status.HTTP_401_UNAUTHORIZED, "Invalid or expired refresh token")
    if data.get("type") != "refresh":
        raise HTTPException(status.HTTP_401_UNAUTHORIZED, "Not a refresh token")
    return TokenResponse(
        access_token=create_access_token(data["sub"], data["role"], data.get("store_id")),
        refresh_token=create_refresh_token(data["sub"], data["role"], data.get("store_id")),
    )


@router.get("/me", response_model=CurrentUserResponse)
async def read_current_user(
    principal: CurrentPrincipal = Depends(get_current_principal),
    db: AsyncSession = Depends(get_db),
):
    """Frontend calls this right after login to decide which shell to route
    into (Admin console vs Owner dashboard vs Staff counter mode)."""
    if principal.role == "admin":
        admin = await db.get(PlatformAdmin, principal.subject_id)
        if admin is None:
            raise HTTPException(status.HTTP_404_NOT_FOUND, "Admin not found")
        return CurrentUserResponse(id=str(admin.id), role="admin", store_id=None, name=admin.name)

    user = await db.get(StoreUser, principal.subject_id)
    if user is None:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "User not found")
    return await _build_store_user_response(user, db)


@router.patch("/me/tour-completed", response_model=CurrentUserResponse)
async def mark_tour_completed(
    principal: CurrentPrincipal = Depends(get_current_principal),
    db: AsyncSession = Depends(get_db),
):
    """Called once the first-login coach-mark tour finishes (or is
    dismissed) so it doesn't reappear on every future login. Admin has no
    tour to complete."""
    if principal.role == "admin":
        raise HTTPException(status.HTTP_400_BAD_REQUEST, "Admin accounts don't have an onboarding tour")
    user = await db.get(StoreUser, principal.subject_id)
    if user is None:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "User not found")
    user.tour_completed = True
    await db.commit()
    await db.refresh(user)
    return await _build_store_user_response(user, db)
