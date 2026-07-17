import uuid

from fastapi import APIRouter, Depends, HTTPException, status
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.orm import selectinload

from ..database import get_db
from ..dependencies import CurrentPrincipal, get_current_principal, get_tenant_store_id, require_roles
from ..models import (
    Batch,
    Medicine,
    PurchaseEntry,
    PurchaseFinancialsStatus,
    PurchaseLineItem,
    PurchasePaymentStatus,
    Supplier,
)
from ..schemas.purchasing import (
    CompletePurchaseFinancialsRequest,
    PurchaseEntryCreateRequest,
    PurchaseEntryResponse,
    PurchaseLineItemResponse,
    SupplierCreateRequest,
    SupplierResponse,
    SupplierUpdateRequest,
)

supplier_router = APIRouter(
    prefix="/suppliers", tags=["purchasing"], dependencies=[Depends(require_roles("owner", "staff"))]
)
purchase_router = APIRouter(
    prefix="/purchase-entries", tags=["purchasing"], dependencies=[Depends(require_roles("owner", "staff"))]
)

_ENTRY_LOAD_OPTIONS = (
    selectinload(PurchaseEntry.line_items).selectinload(PurchaseLineItem.medicine),
    selectinload(PurchaseEntry.supplier),
)


def _serialize_supplier(supplier: Supplier) -> SupplierResponse:
    return SupplierResponse(
        id=str(supplier.id),
        name=supplier.name,
        contact_phone=supplier.contact_phone,
        return_window_days=supplier.return_window_days,
    )


def _serialize_entry(entry: PurchaseEntry, role: str) -> PurchaseEntryResponse:
    is_owner = role == "owner"
    line_items = [
        PurchaseLineItemResponse(
            id=str(li.id),
            medicine_id=str(li.medicine_id),
            medicine_name=li.medicine.name if li.medicine else "",
            batch_id=str(li.batch_id),
            quantity=li.quantity,
            unit_cost=li.unit_cost if is_owner else None,
        )
        for li in entry.line_items
    ]
    return PurchaseEntryResponse(
        id=str(entry.id),
        supplier_id=str(entry.supplier_id),
        supplier_name=entry.supplier.name if entry.supplier else "",
        financials_status=entry.financials_status.value,
        created_at=entry.created_at.isoformat(),
        line_items=line_items,
        invoice_no=entry.invoice_no if is_owner else None,
        invoice_date=entry.invoice_date.isoformat() if (is_owner and entry.invoice_date) else None,
        due_date=entry.due_date.isoformat() if (is_owner and entry.due_date) else None,
        amount=entry.amount if is_owner else None,
        amount_paid=entry.amount_paid if is_owner else None,
        payment_status=entry.payment_status.value if (is_owner and entry.payment_status) else None,
    )


# ---- Suppliers -------------------------------------------------------------
# Owner manages suppliers; Staff needs read access for the stock-in dropdown.


@supplier_router.post(
    "", response_model=SupplierResponse, status_code=status.HTTP_201_CREATED,
    dependencies=[Depends(require_roles("owner"))],
)
async def create_supplier(
    payload: SupplierCreateRequest,
    store_id: uuid.UUID = Depends(get_tenant_store_id),
    db: AsyncSession = Depends(get_db),
):
    supplier = Supplier(
        store_id=store_id, name=payload.name, contact_phone=payload.contact_phone,
        return_window_days=payload.return_window_days,
    )
    db.add(supplier)
    await db.commit()
    await db.refresh(supplier)
    return _serialize_supplier(supplier)


@supplier_router.get("", response_model=list[SupplierResponse])
async def list_suppliers(store_id: uuid.UUID = Depends(get_tenant_store_id), db: AsyncSession = Depends(get_db)):
    result = await db.execute(select(Supplier).where(Supplier.store_id == store_id))
    return [_serialize_supplier(s) for s in result.scalars().all()]


@supplier_router.patch(
    "/{supplier_id}", response_model=SupplierResponse, dependencies=[Depends(require_roles("owner"))]
)
async def update_supplier(
    supplier_id: uuid.UUID,
    payload: SupplierUpdateRequest,
    store_id: uuid.UUID = Depends(get_tenant_store_id),
    db: AsyncSession = Depends(get_db),
):
    supplier = await db.get(Supplier, supplier_id)
    if supplier is None or supplier.store_id != store_id:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "Supplier not found")
    for field, value in payload.model_dump(exclude_unset=True).items():
        setattr(supplier, field, value)
    await db.commit()
    await db.refresh(supplier)
    return _serialize_supplier(supplier)


# ---- Purchase entries -------------------------------------------------------
# Create = Staff's stock-in receiving (batch fields only, live stock
# immediately). Complete-financials = Owner's follow-up on the same record.


@purchase_router.post("", response_model=PurchaseEntryResponse, status_code=status.HTTP_201_CREATED)
async def create_purchase_entry(
    payload: PurchaseEntryCreateRequest,
    principal: CurrentPrincipal = Depends(get_current_principal),
    store_id: uuid.UUID = Depends(get_tenant_store_id),
    db: AsyncSession = Depends(get_db),
):
    supplier = await db.get(Supplier, payload.supplier_id)
    if supplier is None or supplier.store_id != store_id:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "Supplier not found")

    entry = PurchaseEntry(
        store_id=store_id,
        supplier_id=supplier.id,
        financials_status=PurchaseFinancialsStatus.pending_review,
        created_by_id=principal.subject_id,
    )
    db.add(entry)
    await db.flush()  # assigns entry.id for the line items below

    for line in payload.line_items:
        medicine = await db.get(Medicine, line.medicine_id)
        if medicine is None or medicine.store_id != store_id:
            raise HTTPException(status.HTTP_404_NOT_FOUND, f"Medicine {line.medicine_id} not found")
        # Batch goes live immediately — stock accuracy never waits on the
        # Owner completing invoice details.
        batch = Batch(
            medicine_id=medicine.id,
            store_id=store_id,
            batch_number=line.batch_number,
            expiry_date=line.expiry_date,
            quantity_on_hand=line.quantity,
            mrp=line.mrp,
            purchase_cost=None,
        )
        db.add(batch)
        await db.flush()
        db.add(
            PurchaseLineItem(
                purchase_entry_id=entry.id, medicine_id=medicine.id, batch_id=batch.id,
                quantity=line.quantity, unit_cost=None,
            )
        )

    await db.commit()
    result = await db.execute(select(PurchaseEntry).where(PurchaseEntry.id == entry.id).options(*_ENTRY_LOAD_OPTIONS))
    entry = result.scalar_one()
    return _serialize_entry(entry, principal.role)


@purchase_router.get("", response_model=list[PurchaseEntryResponse])
async def list_purchase_entries(
    financials_status: str | None = None,
    principal: CurrentPrincipal = Depends(get_current_principal),
    store_id: uuid.UUID = Depends(get_tenant_store_id),
    db: AsyncSession = Depends(get_db),
):
    """?financials_status=pending_review powers an Owner's "needs review"
    queue — this is what will surface on the Today dashboard in Phase 6."""
    query = select(PurchaseEntry).where(PurchaseEntry.store_id == store_id).options(*_ENTRY_LOAD_OPTIONS)
    if financials_status:
        try:
            query = query.where(PurchaseEntry.financials_status == PurchaseFinancialsStatus(financials_status))
        except ValueError:
            raise HTTPException(status.HTTP_400_BAD_REQUEST, "Invalid financials_status filter")
    result = await db.execute(query)
    entries = result.scalars().unique().all()
    return [_serialize_entry(e, principal.role) for e in entries]


@purchase_router.get("/{entry_id}", response_model=PurchaseEntryResponse)
async def get_purchase_entry(
    entry_id: uuid.UUID,
    principal: CurrentPrincipal = Depends(get_current_principal),
    store_id: uuid.UUID = Depends(get_tenant_store_id),
    db: AsyncSession = Depends(get_db),
):
    result = await db.execute(
        select(PurchaseEntry)
        .where(PurchaseEntry.id == entry_id, PurchaseEntry.store_id == store_id)
        .options(*_ENTRY_LOAD_OPTIONS)
    )
    entry = result.scalar_one_or_none()
    if entry is None:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "Purchase entry not found")
    return _serialize_entry(entry, principal.role)


@purchase_router.patch(
    "/{entry_id}/complete-financials", response_model=PurchaseEntryResponse,
    dependencies=[Depends(require_roles("owner"))],
)
async def complete_purchase_financials(
    entry_id: uuid.UUID,
    payload: CompletePurchaseFinancialsRequest,
    principal: CurrentPrincipal = Depends(get_current_principal),
    store_id: uuid.UUID = Depends(get_tenant_store_id),
    db: AsyncSession = Depends(get_db),
):
    result = await db.execute(
        select(PurchaseEntry)
        .where(PurchaseEntry.id == entry_id, PurchaseEntry.store_id == store_id)
        .options(*_ENTRY_LOAD_OPTIONS)
    )
    entry = result.scalar_one_or_none()
    if entry is None:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "Purchase entry not found")

    try:
        payment_status = PurchasePaymentStatus(payload.payment_status)
    except ValueError:
        raise HTTPException(status.HTTP_400_BAD_REQUEST, "Invalid payment_status")

    line_items_by_id = {str(li.id): li for li in entry.line_items}
    cost_by_line_item = {str(c.line_item_id): c.unit_cost for c in payload.line_item_costs}
    missing = set(line_items_by_id) - set(cost_by_line_item)
    if missing:
        raise HTTPException(status.HTTP_400_BAD_REQUEST, f"Missing cost for line item(s): {', '.join(missing)}")

    for line_item_id, unit_cost in cost_by_line_item.items():
        line_item = line_items_by_id.get(line_item_id)
        if line_item is None:
            raise HTTPException(status.HTTP_400_BAD_REQUEST, f"Unknown line item: {line_item_id}")
        line_item.unit_cost = unit_cost
        batch = await db.get(Batch, line_item.batch_id)
        # Margin queries join live batch cost rather than a frozen snapshot,
        # so this retroactively corrects any sale already made from this batch.
        batch.purchase_cost = unit_cost

    entry.invoice_no = payload.invoice_no
    entry.invoice_date = payload.invoice_date
    entry.due_date = payload.due_date
    entry.amount = payload.amount
    entry.amount_paid = payload.amount_paid
    entry.payment_status = payment_status
    entry.financials_status = PurchaseFinancialsStatus.completed
    entry.completed_by_id = principal.subject_id

    await db.commit()
    result = await db.execute(select(PurchaseEntry).where(PurchaseEntry.id == entry.id).options(*_ENTRY_LOAD_OPTIONS))
    entry = result.scalar_one()
    return _serialize_entry(entry, principal.role)
