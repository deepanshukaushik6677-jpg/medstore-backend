import uuid
from dataclasses import dataclass
from datetime import date, datetime, timedelta, timezone
from decimal import Decimal

from fastapi import APIRouter, Depends, HTTPException, status
from sqlalchemy import select, update
from sqlalchemy.dialects.postgresql import insert as pg_insert
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.orm import selectinload

from ..database import get_db
from ..dependencies import CurrentPrincipal, get_current_principal, get_tenant_store_id, require_roles
from ..models import (
    Batch,
    Bill,
    BillLineItem,
    BillStatus,
    InvoiceCounter,
    InvoiceSeries,
    Medicine,
    PaymentMode,
    Store,
)
from ..schemas.billing import (
    BillCartItemRequest,
    BillLineItemResponse,
    BillPreviewResponse,
    BillResponse,
    CreateBillRequest,
    ResolvedLineItem,
)
from ..utils import round2 as _round2

router = APIRouter(prefix="/bills", tags=["billing"], dependencies=[Depends(require_roles("owner", "staff"))])

# "A few minutes' grace" from the brief, made concrete.
VOID_GRACE_MINUTES = 5

_BILL_LOAD_OPTIONS = (selectinload(Bill.line_items).selectinload(BillLineItem.medicine),)


def _current_fy_label(d: date) -> str:
    """Indian financial year: Apr 1 - Mar 31, written like "2026-27"."""
    start_year = d.year if d.month >= 4 else d.year - 1
    return f"{start_year}-{(start_year + 1) % 100:02d}"


@dataclass
class _ResolvedLine:
    medicine_id: uuid.UUID
    medicine_name: str
    hsn_code: str | None
    batch_id: uuid.UUID
    quantity: int
    unit_price: Decimal
    gst_rate_applied: Decimal
    gst_amount: Decimal
    line_total: Decimal


async def _resolve_cart(
    db: AsyncSession,
    store_id: uuid.UUID,
    bill_type: str,
    items: list[BillCartItemRequest],
    *,
    mutate: bool,
) -> tuple[list[_ResolvedLine], Decimal, Decimal]:
    """Walks each cart line's batches in FEFO order, splitting across
    batches if the nearest-expiry one doesn't have enough on hand. When
    mutate=True (real checkout) this also decrements quantity_on_hand and
    locks the rows it touches; a preview leaves stock untouched."""
    resolved: list[_ResolvedLine] = []
    subtotal = Decimal("0")
    gst_total = Decimal("0")

    for item in items:
        medicine = await db.get(Medicine, item.medicine_id)
        if medicine is None or medicine.store_id != store_id:
            raise HTTPException(status.HTTP_404_NOT_FOUND, f"Medicine {item.medicine_id} not found")

        if bill_type == "gst":
            missing = [
                label
                for label, val in (("GST rate", medicine.gst_rate), ("HSN code", medicine.hsn_code))
                if val is None
            ]
            if missing:
                raise HTTPException(
                    status.HTTP_400_BAD_REQUEST,
                    f"'{medicine.name}' needs {' and '.join(missing)} set before it can go on a GST invoice",
                )

        # Expired stock is never sellable, regardless of FEFO order — a
        # pharmacy can't legally sell it. This gap surfaced once Expiry
        # Protection existed to show it: without this filter, FEFO would
        # happily pick the nearest-expiry batch even after it had expired.
        batch_query = (
            select(Batch)
            .where(Batch.medicine_id == medicine.id, Batch.quantity_on_hand > 0, Batch.expiry_date >= date.today())
            .order_by(Batch.expiry_date.asc())
        )
        if mutate:
            batch_query = batch_query.with_for_update()
        batches = (await db.execute(batch_query)).scalars().all()

        remaining = item.quantity
        for batch in batches:
            if remaining <= 0:
                break
            draw = min(batch.quantity_on_hand, remaining)
            if draw <= 0:
                continue
            remaining -= draw
            if mutate:
                batch.quantity_on_hand -= draw

            unit_price = batch.mrp
            line_subtotal = _round2(unit_price * draw)
            gst_rate = medicine.gst_rate if bill_type == "gst" else Decimal("0")
            gst_amount = _round2(line_subtotal * gst_rate / Decimal("100")) if gst_rate else Decimal("0")

            resolved.append(
                _ResolvedLine(
                    medicine_id=medicine.id,
                    medicine_name=medicine.name,
                    hsn_code=medicine.hsn_code,
                    batch_id=batch.id,
                    quantity=draw,
                    unit_price=unit_price,
                    gst_rate_applied=gst_rate,
                    gst_amount=gst_amount,
                    line_total=line_subtotal + gst_amount,
                )
            )
            subtotal += line_subtotal
            gst_total += gst_amount

        if remaining > 0:
            available = item.quantity - remaining
            raise HTTPException(
                status.HTTP_400_BAD_REQUEST,
                f"Insufficient stock for '{medicine.name}': requested {item.quantity}, only {available} available",
            )

    return resolved, subtotal, gst_total


async def _next_bill_number(db: AsyncSession, store: Store, series: InvoiceSeries) -> tuple[str, str | None]:
    """Atomic UPDATE ... RETURNING counter. fy_year is "" (not NULL) when not
    applicable, since Postgres unique constraints treat NULL as never-equal
    -to-itself and would silently allow duplicate counters otherwise."""
    fy_year = (
        _current_fy_label(date.today())
        if (series == InvoiceSeries.gst and store.gst_numbering_mode.value == "fy_reset")
        else ""
    )

    insert_stmt = (
        pg_insert(InvoiceCounter)
        .values(store_id=store.id, series=series, fy_year=fy_year, next_number=1)
        .on_conflict_do_nothing(index_elements=["store_id", "series", "fy_year"])
    )
    await db.execute(insert_stmt)

    update_stmt = (
        update(InvoiceCounter)
        .where(
            InvoiceCounter.store_id == store.id,
            InvoiceCounter.series == series,
            InvoiceCounter.fy_year == fy_year,
        )
        .values(next_number=InvoiceCounter.next_number + 1)
        .returning(InvoiceCounter.next_number)
    )
    new_next_number = (await db.execute(update_stmt)).scalar_one()
    number = new_next_number - 1

    prefix = (store.gst_invoice_prefix if series == InvoiceSeries.gst else None) or series.value.upper()
    bill_no = f"{prefix}/{fy_year}/{number:04d}" if fy_year else f"{prefix}/{number:04d}"
    return bill_no, (fy_year or None)


def _to_resolved_response(line: _ResolvedLine) -> ResolvedLineItem:
    return ResolvedLineItem(
        medicine_id=str(line.medicine_id),
        medicine_name=line.medicine_name,
        hsn_code=line.hsn_code,
        batch_id=str(line.batch_id),
        quantity=line.quantity,
        unit_price=line.unit_price,
        gst_rate_applied=line.gst_rate_applied,
        gst_amount=line.gst_amount,
        line_total=line.line_total,
    )


def _serialize_bill(bill: Bill) -> BillResponse:
    is_void_eligible = bill.status == BillStatus.completed and (
        datetime.now(timezone.utc) - bill.completed_at
    ) <= timedelta(minutes=VOID_GRACE_MINUTES)
    line_items = [
        BillLineItemResponse(
            id=str(li.id),
            medicine_id=str(li.medicine_id),
            medicine_name=li.medicine.name if li.medicine else "",
            hsn_code=li.medicine.hsn_code if li.medicine else None,
            batch_id=str(li.batch_id),
            quantity=li.quantity,
            unit_price=li.unit_price,
            gst_rate_applied=li.gst_rate_applied,
            gst_amount=_round2(li.unit_price * li.quantity * li.gst_rate_applied / Decimal("100"))
            if li.gst_rate_applied
            else Decimal("0"),
            line_total=li.line_total,
        )
        for li in bill.line_items
    ]
    return BillResponse(
        id=str(bill.id),
        bill_no=bill.bill_no,
        bill_type=bill.bill_type,
        fy_year=bill.fy_year,
        status=bill.status.value,
        payment_mode=bill.payment_mode.value,
        customer_id=str(bill.customer_id) if bill.customer_id else None,
        subtotal=bill.subtotal,
        gst_amount=bill.gst_amount,
        cgst_amount=_round2(bill.gst_amount / 2),
        sgst_amount=_round2(bill.gst_amount / 2),
        total=bill.total,
        completed_at=bill.completed_at.isoformat(),
        void_eligible=is_void_eligible,
        line_items=line_items,
    )


def _resolve_bill_type(requested: str | None, store: Store) -> str:
    if requested is None:
        return store.default_bill_type.value
    if requested not in ("simple", "gst"):
        raise HTTPException(status.HTTP_400_BAD_REQUEST, "bill_type must be 'simple' or 'gst'")
    return requested


@router.post("/preview", response_model=BillPreviewResponse)
async def preview_bill(
    payload: CreateBillRequest,
    store_id: uuid.UUID = Depends(get_tenant_store_id),
    db: AsyncSession = Depends(get_db),
):
    """Live cart total as Staff scans item after item — read-only, nothing
    is written. The frontend calls this on every cart change."""
    store = await db.get(Store, store_id)
    bill_type = _resolve_bill_type(payload.bill_type, store)
    if bill_type == "gst" and not store.gstin:
        raise HTTPException(status.HTTP_400_BAD_REQUEST, "Store GSTIN must be set before issuing GST invoices")

    resolved, subtotal, gst_total = await _resolve_cart(db, store_id, bill_type, payload.items, mutate=False)
    return BillPreviewResponse(
        bill_type=bill_type,
        subtotal=subtotal,
        gst_amount=gst_total,
        cgst_amount=_round2(gst_total / 2),
        sgst_amount=_round2(gst_total / 2),
        total=subtotal + gst_total,
        line_items=[_to_resolved_response(line) for line in resolved],
    )


@router.post("", response_model=BillResponse, status_code=status.HTTP_201_CREATED)
async def create_bill(
    payload: CreateBillRequest,
    principal: CurrentPrincipal = Depends(get_current_principal),
    store_id: uuid.UUID = Depends(get_tenant_store_id),
    db: AsyncSession = Depends(get_db),
):
    """The single checkout action at the end of a continuous cart. Batch
    stock is decremented here, atomically, under row locks — this is the
    one place in Fast Billing that actually mutates the ledger."""
    # Idempotency: an offline-queued bill retrying sync after a dropped
    # connection must not create a second sale.
    existing = await db.execute(
        select(Bill)
        .where(Bill.store_id == store_id, Bill.idempotency_key == payload.idempotency_key)
        .options(*_BILL_LOAD_OPTIONS)
    )
    existing_bill = existing.scalar_one_or_none()
    if existing_bill is not None:
        return _serialize_bill(existing_bill)

    if payload.payment_mode not in ("cash", "upi", "card"):
        raise HTTPException(status.HTTP_400_BAD_REQUEST, "Payment mode 'credit' (Udhaar) isn't available yet")

    store = await db.get(Store, store_id)
    bill_type = _resolve_bill_type(payload.bill_type, store)
    if bill_type == "gst" and not store.gstin:
        raise HTTPException(status.HTTP_400_BAD_REQUEST, "Store GSTIN must be set before issuing GST invoices")

    resolved, subtotal, gst_total = await _resolve_cart(db, store_id, bill_type, payload.items, mutate=True)

    series = InvoiceSeries.gst if bill_type == "gst" else InvoiceSeries.simple
    bill_no, fy_year = await _next_bill_number(db, store, series)

    bill = Bill(
        store_id=store_id,
        bill_no=bill_no,
        bill_type=bill_type,
        fy_year=fy_year,
        customer_id=payload.customer_id,
        subtotal=subtotal,
        gst_amount=gst_total,
        total=subtotal + gst_total,
        payment_mode=PaymentMode(payload.payment_mode),
        status=BillStatus.completed,
        idempotency_key=payload.idempotency_key,
        created_by_id=principal.subject_id,
        completed_at=datetime.now(timezone.utc),
    )
    db.add(bill)
    await db.flush()

    for line in resolved:
        db.add(
            BillLineItem(
                bill_id=bill.id,
                medicine_id=line.medicine_id,
                batch_id=line.batch_id,
                quantity=line.quantity,
                unit_price=line.unit_price,
                gst_rate_applied=line.gst_rate_applied,
                line_total=line.line_total,
            )
        )

    await db.commit()
    result = await db.execute(select(Bill).where(Bill.id == bill.id).options(*_BILL_LOAD_OPTIONS))
    bill = result.scalar_one()
    return _serialize_bill(bill)


@router.get("", response_model=list[BillResponse])
async def list_bills(
    bill_date: str | None = None,
    limit: int = 50,
    store_id: uuid.UUID = Depends(get_tenant_store_id),
    db: AsyncSession = Depends(get_db),
):
    """?bill_date=YYYY-MM-DD narrows to one day — this is what the Today
    dashboard's sales total will query against in Phase 6."""
    query = select(Bill).where(Bill.store_id == store_id).options(*_BILL_LOAD_OPTIONS)
    if bill_date:
        try:
            day = date.fromisoformat(bill_date)
        except ValueError:
            raise HTTPException(status.HTTP_400_BAD_REQUEST, "bill_date must be YYYY-MM-DD")
        start = datetime(day.year, day.month, day.day, tzinfo=timezone.utc)
        query = query.where(Bill.completed_at >= start, Bill.completed_at < start + timedelta(days=1))
    query = query.order_by(Bill.completed_at.desc()).limit(min(limit, 200))
    result = await db.execute(query)
    return [_serialize_bill(b) for b in result.scalars().unique().all()]


@router.get("/{bill_id}", response_model=BillResponse)
async def get_bill(
    bill_id: uuid.UUID,
    store_id: uuid.UUID = Depends(get_tenant_store_id),
    db: AsyncSession = Depends(get_db),
):
    result = await db.execute(
        select(Bill).where(Bill.id == bill_id, Bill.store_id == store_id).options(*_BILL_LOAD_OPTIONS)
    )
    bill = result.scalar_one_or_none()
    if bill is None:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "Bill not found")
    return _serialize_bill(bill)


@router.patch("/{bill_id}/void", response_model=BillResponse)
async def void_bill(
    bill_id: uuid.UUID,
    store_id: uuid.UUID = Depends(get_tenant_store_id),
    db: AsyncSession = Depends(get_db),
):
    """Reverses the stock deduction and marks the bill voided. Open to any
    Owner/Staff at this store, not just whoever rang it up — it's a shared
    counter and the grace window is short enough that this stays safe."""
    result = await db.execute(
        select(Bill).where(Bill.id == bill_id, Bill.store_id == store_id).options(*_BILL_LOAD_OPTIONS)
    )
    bill = result.scalar_one_or_none()
    if bill is None:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "Bill not found")
    if bill.status == BillStatus.voided:
        raise HTTPException(status.HTTP_400_BAD_REQUEST, "Bill is already voided")
    age = datetime.now(timezone.utc) - bill.completed_at
    if age > timedelta(minutes=VOID_GRACE_MINUTES):
        raise HTTPException(
            status.HTTP_409_CONFLICT,
            f"Undo window has passed ({VOID_GRACE_MINUTES} min grace) — this bill can no longer be voided directly",
        )

    for line_item in bill.line_items:
        batch = await db.get(Batch, line_item.batch_id)
        if batch is not None:
            batch.quantity_on_hand += line_item.quantity

    bill.status = BillStatus.voided
    await db.commit()
    result = await db.execute(select(Bill).where(Bill.id == bill.id).options(*_BILL_LOAD_OPTIONS))
    bill = result.scalar_one()
    return _serialize_bill(bill)
