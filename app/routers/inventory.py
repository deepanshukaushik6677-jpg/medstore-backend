import uuid

from fastapi import APIRouter, Depends, HTTPException, status
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.orm import selectinload

from ..database import get_db
from ..dependencies import CurrentPrincipal, get_current_principal, get_tenant_store_id, require_roles
from ..models import Medicine
from ..schemas.inventory import (
    BatchResponse,
    MedicineResponse,
    OwnerMedicineCreateRequest,
    OwnerMedicineUpdateRequest,
)

router = APIRouter(prefix="/medicines", tags=["inventory"], dependencies=[Depends(require_roles("owner", "staff"))])

# Fields Staff may touch on an existing medicine — matches the Locator brief:
# "Owner/Staff can edit a medicine's location from this same screen."
STAFF_EDITABLE_FIELDS = {"barcode", "zone", "rack", "shelf", "box"}


def _serialize(medicine: Medicine, role: str) -> MedicineResponse:
    total_stock = sum(b.quantity_on_hand for b in medicine.batches)
    batches = [
        BatchResponse(
            id=str(b.id),
            batch_number=b.batch_number,
            expiry_date=b.expiry_date,
            quantity_on_hand=b.quantity_on_hand,
            mrp=b.mrp,
            purchase_cost=b.purchase_cost if role == "owner" else None,
        )
        for b in sorted(medicine.batches, key=lambda b: b.expiry_date)
    ]
    return MedicineResponse(
        id=str(medicine.id),
        name=medicine.name,
        barcode=medicine.barcode,
        category=medicine.category,
        unit=medicine.unit,
        zone=medicine.zone,
        rack=medicine.rack,
        shelf=medicine.shelf,
        box=medicine.box,
        reorder_threshold=medicine.reorder_threshold,
        hsn_code=medicine.hsn_code,
        gst_rate=medicine.gst_rate,
        total_stock=total_stock,
        batches=batches,
    )


@router.post("", response_model=MedicineResponse, status_code=status.HTTP_201_CREATED)
async def create_medicine(
    payload: OwnerMedicineCreateRequest,
    principal: CurrentPrincipal = Depends(get_current_principal),
    store_id: uuid.UUID = Depends(get_tenant_store_id),
    db: AsyncSession = Depends(get_db),
):
    """Either role can add a medicine — Staff need this mid-receiving when a
    delivery includes something never stocked before. Tax fields are the one
    thing Staff can't set here."""
    if principal.role == "staff" and (payload.hsn_code is not None or payload.gst_rate is not None):
        raise HTTPException(status.HTTP_403_FORBIDDEN, "Staff cannot set HSN code or GST rate")
    medicine = Medicine(
        store_id=store_id,
        name=payload.name,
        barcode=payload.barcode,
        category=payload.category,
        unit=payload.unit,
        zone=payload.zone,
        rack=payload.rack,
        shelf=payload.shelf,
        box=payload.box,
        reorder_threshold=payload.reorder_threshold,
        hsn_code=payload.hsn_code,
        gst_rate=payload.gst_rate,
    )
    db.add(medicine)
    await db.commit()
    # A brand-new medicine has no batches yet — build the response directly
    # rather than touching the lazy `batches` relationship (would trigger an
    # implicit lazy-load, which AsyncSession doesn't allow outside a query).
    return MedicineResponse(
        id=str(medicine.id),
        name=medicine.name,
        barcode=medicine.barcode,
        category=medicine.category,
        unit=medicine.unit,
        zone=medicine.zone,
        rack=medicine.rack,
        shelf=medicine.shelf,
        box=medicine.box,
        reorder_threshold=medicine.reorder_threshold,
        hsn_code=medicine.hsn_code,
        gst_rate=medicine.gst_rate,
        total_stock=0,
        batches=[],
    )


@router.get("", response_model=list[MedicineResponse])
async def list_medicines(
    search: str | None = None,
    barcode: str | None = None,
    principal: CurrentPrincipal = Depends(get_current_principal),
    store_id: uuid.UUID = Depends(get_tenant_store_id),
    db: AsyncSession = Depends(get_db),
):
    """Powers both the stock-in dropdown and the Locator search — name or
    barcode, whichever the counter screen sends."""
    query = select(Medicine).where(Medicine.store_id == store_id).options(selectinload(Medicine.batches))
    if barcode:
        query = query.where(Medicine.barcode == barcode)
    elif search:
        query = query.where(Medicine.name.ilike(f"%{search}%"))
    result = await db.execute(query)
    medicines = result.scalars().unique().all()
    return [_serialize(m, principal.role) for m in medicines]


@router.get("/{medicine_id}", response_model=MedicineResponse)
async def get_medicine(
    medicine_id: uuid.UUID,
    principal: CurrentPrincipal = Depends(get_current_principal),
    store_id: uuid.UUID = Depends(get_tenant_store_id),
    db: AsyncSession = Depends(get_db),
):
    result = await db.execute(
        select(Medicine)
        .where(Medicine.id == medicine_id, Medicine.store_id == store_id)
        .options(selectinload(Medicine.batches))
    )
    medicine = result.scalar_one_or_none()
    if medicine is None:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "Medicine not found")
    return _serialize(medicine, principal.role)


@router.patch("/{medicine_id}", response_model=MedicineResponse)
async def update_medicine(
    medicine_id: uuid.UUID,
    payload: OwnerMedicineUpdateRequest,
    principal: CurrentPrincipal = Depends(get_current_principal),
    store_id: uuid.UUID = Depends(get_tenant_store_id),
    db: AsyncSession = Depends(get_db),
):
    result = await db.execute(
        select(Medicine)
        .where(Medicine.id == medicine_id, Medicine.store_id == store_id)
        .options(selectinload(Medicine.batches))
    )
    medicine = result.scalar_one_or_none()
    if medicine is None:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "Medicine not found")

    data = payload.model_dump(exclude_unset=True)
    if principal.role == "staff":
        disallowed = set(data) - STAFF_EDITABLE_FIELDS
        if disallowed:
            raise HTTPException(
                status.HTTP_403_FORBIDDEN, f"Staff cannot update: {', '.join(sorted(disallowed))}"
            )

    for field, value in data.items():
        setattr(medicine, field, value)
    await db.commit()
    result = await db.execute(
        select(Medicine).where(Medicine.id == medicine_id).options(selectinload(Medicine.batches))
    )
    medicine = result.scalar_one()
    return _serialize(medicine, principal.role)
