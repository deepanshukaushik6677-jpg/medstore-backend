import uuid
from datetime import datetime, timedelta, timezone

from fastapi import APIRouter, Depends, HTTPException, status
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from ..database import get_db
from ..dependencies import CurrentPrincipal, require_roles
from ..models import AdminAccessLog, Store, StoreStatus, StoreUser, StoreUserRole
from ..schemas.admin import (
    AccessGrantRequest,
    AccessGrantResponse,
    CreateStoreRequest,
    StoreSummaryResponse,
    UpdateStoreStatusRequest,
)
from ..security import hash_password

router = APIRouter(prefix="/admin", tags=["admin"], dependencies=[Depends(require_roles("admin"))])


@router.post("/stores", response_model=StoreSummaryResponse, status_code=status.HTTP_201_CREATED)
async def create_store(payload: CreateStoreRequest, db: AsyncSession = Depends(get_db)):
    """Admin onboards a new store and its initial Owner account in one step.
    No routine access to the store's own data is granted by this — see
    AdminAccessLog for that."""
    existing = await db.execute(select(StoreUser).where(StoreUser.phone == payload.owner_phone))
    if existing.scalar_one_or_none() is not None:
        raise HTTPException(status.HTTP_409_CONFLICT, "That phone number is already registered")

    store = Store(name=payload.store_name)
    db.add(store)
    await db.flush()  # assigns store.id before we create the owner row

    owner = StoreUser(
        store_id=store.id,
        role=StoreUserRole.owner,
        name=payload.owner_name,
        phone=payload.owner_phone,
        password_hash=hash_password(payload.owner_password),
    )
    db.add(owner)
    await db.commit()
    await db.refresh(store)
    return StoreSummaryResponse(
        id=str(store.id), name=store.name, status=store.status.value, created_at=store.created_at.isoformat()
    )


def _serialize_store(store: Store) -> StoreSummaryResponse:
    return StoreSummaryResponse(
        id=str(store.id), name=store.name, status=store.status.value, created_at=store.created_at.isoformat()
    )


@router.get("/stores", response_model=list[StoreSummaryResponse])
async def list_stores(db: AsyncSession = Depends(get_db)):
    """Platform-level view only: existence + status. Deliberately no
    inventory/sales/customer fields here — that would be ambient access."""
    result = await db.execute(select(Store))
    stores = result.scalars().all()
    return [_serialize_store(s) for s in stores]


@router.get("/stores/{store_id}", response_model=StoreSummaryResponse)
async def get_store(store_id: uuid.UUID, db: AsyncSession = Depends(get_db)):
    store = await db.get(Store, store_id)
    if store is None:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "Store not found")
    return _serialize_store(store)


@router.patch("/stores/{store_id}", response_model=StoreSummaryResponse)
async def update_store_status(
    store_id: uuid.UUID, payload: UpdateStoreStatusRequest, db: AsyncSession = Depends(get_db)
):
    """The one lifecycle action Admin has over a store — active/suspended.
    Still no access to the store's own inventory/sales/customer data."""
    store = await db.get(Store, store_id)
    if store is None:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "Store not found")
    try:
        store.status = StoreStatus(payload.status)
    except ValueError:
        raise HTTPException(status.HTTP_400_BAD_REQUEST, "status must be 'active' or 'suspended'")
    await db.commit()
    await db.refresh(store)
    return _serialize_store(store)


@router.post("/stores/{store_id}/access-grants", response_model=AccessGrantResponse, status_code=status.HTTP_201_CREATED)
async def create_access_grant(
    store_id: uuid.UUID,
    payload: AccessGrantRequest,
    principal: CurrentPrincipal = Depends(require_roles("admin")),
    db: AsyncSession = Depends(get_db),
):
    """The only way an admin ever gets a scoped read into a store's data:
    explicit reason, time-boxed, permanently on record."""
    store = await db.get(Store, store_id)
    if store is None:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "Store not found")
    grant = AdminAccessLog(
        admin_id=principal.subject_id,
        store_id=store_id,
        reason=payload.reason,
        expires_at=datetime.now(timezone.utc) + timedelta(hours=payload.duration_hours),
    )
    db.add(grant)
    await db.commit()
    await db.refresh(grant)
    return AccessGrantResponse(
        id=str(grant.id), reason=grant.reason, granted_at=grant.granted_at.isoformat(),
        expires_at=grant.expires_at.isoformat(), revoked_at=grant.revoked_at.isoformat() if grant.revoked_at else None,
    )


@router.get("/stores/{store_id}/access-grants", response_model=list[AccessGrantResponse])
async def list_access_grants(store_id: uuid.UUID, db: AsyncSession = Depends(get_db)):
    """Audit trail of every time an admin has (or currently has) a scoped
    read into this store — visible so the history is never just implicit."""
    store = await db.get(Store, store_id)
    if store is None:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "Store not found")
    result = await db.execute(
        select(AdminAccessLog).where(AdminAccessLog.store_id == store_id).order_by(AdminAccessLog.granted_at.desc())
    )
    grants = result.scalars().all()
    return [
        AccessGrantResponse(
            id=str(g.id), reason=g.reason, granted_at=g.granted_at.isoformat(),
            expires_at=g.expires_at.isoformat(), revoked_at=g.revoked_at.isoformat() if g.revoked_at else None,
        )
        for g in grants
    ]
