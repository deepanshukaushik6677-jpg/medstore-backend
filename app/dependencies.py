import uuid
from datetime import datetime, timezone
from typing import Annotated

import jwt
from fastapi import Depends, HTTPException, status
from fastapi.security import OAuth2PasswordBearer
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from .database import get_db
from .models import AdminAccessLog
from .security import decode_token

oauth2_scheme = OAuth2PasswordBearer(tokenUrl="/auth/login", auto_error=True)


class CurrentPrincipal:
    """Resolved identity for the current request: which account, which role,
    and — for store users — which store they're scoped to."""

    def __init__(self, subject_id: uuid.UUID, role: str, store_id: uuid.UUID | None):
        self.subject_id = subject_id
        self.role = role  # "admin" | "owner" | "staff"
        self.store_id = store_id


async def get_current_principal(token: Annotated[str, Depends(oauth2_scheme)]) -> CurrentPrincipal:
    try:
        payload = decode_token(token)
    except jwt.PyJWTError:
        raise HTTPException(status.HTTP_401_UNAUTHORIZED, "Invalid or expired token")
    if payload.get("type") != "access":
        raise HTTPException(status.HTTP_401_UNAUTHORIZED, "Not an access token")
    try:
        subject_id = uuid.UUID(payload["sub"])
    except (KeyError, ValueError, TypeError):
        raise HTTPException(status.HTTP_401_UNAUTHORIZED, "Malformed token")
    store_id = uuid.UUID(payload["store_id"]) if payload.get("store_id") else None
    return CurrentPrincipal(subject_id=subject_id, role=payload.get("role", ""), store_id=store_id)


def require_roles(*roles: str):
    """Dependency factory: 403s unless the caller's role is one of `roles`.
    This is what keeps Staff off analytics/purchase-financials endpoints and
    Owner off other stores' data, at the API layer — not just hidden in the UI."""

    async def _check(
        principal: Annotated[CurrentPrincipal, Depends(get_current_principal)],
    ) -> CurrentPrincipal:
        if principal.role not in roles:
            raise HTTPException(status.HTTP_403_FORBIDDEN, "Not permitted for this role")
        return principal

    return _check


async def get_tenant_store_id(
    principal: Annotated[CurrentPrincipal, Depends(get_current_principal)],
) -> uuid.UUID:
    """The tenant filter every store-scoped query goes through. Owner/Staff
    get their store_id straight from their own token — never from a request
    parameter, so one store's staff can never point a request at another
    store's id. Admin is refused here entirely; see get_admin_scoped_store_id
    for the one legitimate, logged path into store data."""
    if principal.role in ("owner", "staff"):
        if principal.store_id is None:
            raise HTTPException(status.HTTP_403_FORBIDDEN, "Account is not attached to a store")
        return principal.store_id
    raise HTTPException(
        status.HTTP_403_FORBIDDEN,
        "This account type has no ambient store access",
    )


async def get_admin_scoped_store_id(
    store_id: uuid.UUID,
    principal: Annotated[CurrentPrincipal, Depends(require_roles("admin"))],
    db: Annotated[AsyncSession, Depends(get_db)],
) -> uuid.UUID:
    """Use only on support-only endpoints where Admin legitimately needs to
    read one store's data. Requires a live, unexpired, unrevoked
    AdminAccessLog grant for this exact (admin, store) pair."""
    now = datetime.now(timezone.utc)
    result = await db.execute(
        select(AdminAccessLog).where(
            AdminAccessLog.admin_id == principal.subject_id,
            AdminAccessLog.store_id == store_id,
            AdminAccessLog.revoked_at.is_(None),
            AdminAccessLog.expires_at > now,
        )
    )
    if result.scalar_one_or_none() is None:
        raise HTTPException(
            status.HTTP_403_FORBIDDEN,
            "No active access grant for this store — create one first",
        )
    return store_id
