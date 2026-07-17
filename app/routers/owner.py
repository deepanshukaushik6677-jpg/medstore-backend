import uuid

from fastapi import APIRouter, Depends, HTTPException, status
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from ..database import get_db
from ..dependencies import get_tenant_store_id, require_roles
from ..models import StoreUser, StoreUserRole
from ..schemas.store_user import CreateStaffRequest, StaffResponse, UpdateStaffRequest
from ..security import hash_password

router = APIRouter(prefix="/owner/staff", tags=["owner"], dependencies=[Depends(require_roles("owner"))])


@router.post("", response_model=StaffResponse, status_code=status.HTTP_201_CREATED)
async def create_staff(
    payload: CreateStaffRequest,
    store_id: uuid.UUID = Depends(get_tenant_store_id),
    db: AsyncSession = Depends(get_db),
):
    existing = await db.execute(select(StoreUser).where(StoreUser.phone == payload.phone))
    if existing.scalar_one_or_none() is not None:
        raise HTTPException(status.HTTP_409_CONFLICT, "That phone number is already registered")
    staff = StoreUser(
        store_id=store_id,
        role=StoreUserRole.staff,
        name=payload.name,
        phone=payload.phone,
        password_hash=hash_password(payload.password),
    )
    db.add(staff)
    await db.commit()
    await db.refresh(staff)
    return StaffResponse(
        id=str(staff.id), name=staff.name, phone=staff.phone, is_active=staff.is_active,
        tour_completed=staff.tour_completed,
    )


@router.get("", response_model=list[StaffResponse])
async def list_staff(store_id: uuid.UUID = Depends(get_tenant_store_id), db: AsyncSession = Depends(get_db)):
    result = await db.execute(
        select(StoreUser).where(StoreUser.store_id == store_id, StoreUser.role == StoreUserRole.staff)
    )
    staff = result.scalars().all()
    return [
        StaffResponse(id=str(s.id), name=s.name, phone=s.phone, is_active=s.is_active, tour_completed=s.tour_completed)
        for s in staff
    ]


@router.patch("/{staff_id}", response_model=StaffResponse)
async def update_staff(
    staff_id: uuid.UUID,
    payload: UpdateStaffRequest,
    store_id: uuid.UUID = Depends(get_tenant_store_id),
    db: AsyncSession = Depends(get_db),
):
    staff = await db.get(StoreUser, staff_id)
    # Tenant check: never trust the path param alone — confirm it actually belongs to this store.
    if staff is None or staff.store_id != store_id or staff.role != StoreUserRole.staff:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "Staff member not found")
    if payload.is_active is not None:
        staff.is_active = payload.is_active
    if payload.new_password is not None:
        staff.password_hash = hash_password(payload.new_password)
    await db.commit()
    await db.refresh(staff)
    return StaffResponse(
        id=str(staff.id), name=staff.name, phone=staff.phone, is_active=staff.is_active,
        tour_completed=staff.tour_completed,
    )
