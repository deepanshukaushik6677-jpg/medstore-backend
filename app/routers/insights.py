import uuid
from datetime import date, datetime, timedelta, timezone
from decimal import ROUND_HALF_UP, Decimal

from fastapi import APIRouter, Depends
from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.orm import selectinload

from ..database import get_db
from ..dependencies import get_tenant_store_id, require_roles
from ..models import Bill, BillLineItem, BillStatus, Medicine, PurchaseEntry, PurchaseLineItem, Supplier
from ..schemas.insights import (
    ExpiryAlertItem,
    ExpiryAlertsResponse,
    ReorderAssistantResponse,
    ReorderItem,
    ReorderSupplierGroup,
)
from ..utils import get_expiry_alert_rows, sellable_stock

# Expiry alerts are part of the Today dashboard for BOTH roles per the brief;
# Reorder Assistant is a purchasing/strategy call, so it stays Owner-only —
# same split as the rest of the analytical surface.
expiry_router = APIRouter(prefix="/medicines", tags=["insights"], dependencies=[Depends(require_roles("owner", "staff"))])
reorder_router = APIRouter(prefix="/medicines", tags=["insights"], dependencies=[Depends(require_roles("owner"))])

# How far back to look when estimating sales velocity, and how many weeks of
# that velocity a suggested reorder should cover. Not specified in the brief
# — chosen as a reasonable, explainable default rather than a hard rule.
VELOCITY_LOOKBACK_DAYS = 30
REORDER_COVER_WEEKS = 2


@expiry_router.get("/expiry-alerts", response_model=ExpiryAlertsResponse)
async def get_expiry_alerts(store_id: uuid.UUID = Depends(get_tenant_store_id), db: AsyncSession = Depends(get_db)):
    """Flags every in-stock batch that's either already expired or inside
    its supplier's return window — ahead of expiry, per the brief, not after.
    A batch with no traceable supplier (shouldn't normally happen, since
    every batch is created through a purchase entry) falls back to a 0-day
    window, so it only surfaces once actually expired."""
    rows = await get_expiry_alert_rows(db, store_id)
    items = [
        ExpiryAlertItem(
            medicine_id=str(medicine.id),
            medicine_name=medicine.name,
            batch_id=str(batch.id),
            batch_number=batch.batch_number,
            expiry_date=batch.expiry_date.isoformat(),
            quantity_on_hand=batch.quantity_on_hand,
            days_until_expiry=days_until_expiry,
            supplier_id=str(supplier.id) if supplier else None,
            supplier_name=supplier.name if supplier else "Unknown supplier",
            return_window_days=return_window,
            status=status_label,
        )
        for medicine, batch, supplier, days_until_expiry, return_window, status_label in rows
    ]
    items.sort(key=lambda i: i.days_until_expiry)
    return ExpiryAlertsResponse(generated_at=datetime.now(timezone.utc).isoformat(), items=items)


@reorder_router.get("/reorder-suggestions", response_model=ReorderAssistantResponse)
async def get_reorder_suggestions(
    store_id: uuid.UUID = Depends(get_tenant_store_id), db: AsyncSession = Depends(get_db)
):
    """Low-stock medicines (current stock <= reorder_threshold), each with an
    estimated weekly sales velocity and a suggested reorder quantity, grouped
    by whichever supplier most recently supplied that medicine — so the
    output reads "Supplier X: reorder these N items," not one flat list."""
    today = date.today()
    med_result = await db.execute(
        select(Medicine).where(Medicine.store_id == store_id).options(selectinload(Medicine.batches))
    )
    medicines = med_result.scalars().unique().all()

    low_stock = [m for m in medicines if sellable_stock(m, today) <= m.reorder_threshold]

    if not low_stock:
        return ReorderAssistantResponse(
            generated_at=datetime.now(timezone.utc).isoformat(), lookback_days=VELOCITY_LOOKBACK_DAYS, items=[]
        )

    low_stock_ids = [m.id for m in low_stock]

    # Sales velocity: units sold on completed bills only, over the lookback window.
    since = datetime.now(timezone.utc) - timedelta(days=VELOCITY_LOOKBACK_DAYS)
    velocity_rows = (
        await db.execute(
            select(BillLineItem.medicine_id, func.sum(BillLineItem.quantity))
            .join(Bill, Bill.id == BillLineItem.bill_id)
            .where(
                Bill.store_id == store_id,
                Bill.status == BillStatus.completed,
                Bill.completed_at >= since,
                BillLineItem.medicine_id.in_(low_stock_ids),
            )
            .group_by(BillLineItem.medicine_id)
        )
    ).all()
    total_sold_by_medicine = {row[0]: row[1] for row in velocity_rows}

    # Whoever most recently supplied each medicine is who we suggest reordering from.
    supplier_rows = (
        await db.execute(
            select(PurchaseLineItem.medicine_id, Supplier.id, Supplier.name, PurchaseEntry.created_at)
            .join(PurchaseEntry, PurchaseEntry.id == PurchaseLineItem.purchase_entry_id)
            .join(Supplier, Supplier.id == PurchaseEntry.supplier_id)
            .where(PurchaseLineItem.medicine_id.in_(low_stock_ids), PurchaseEntry.store_id == store_id)
            .order_by(PurchaseLineItem.medicine_id, PurchaseEntry.created_at.desc())
        )
    ).all()
    latest_supplier_by_medicine: dict[uuid.UUID, tuple[uuid.UUID, str]] = {}
    for medicine_id, supplier_id, supplier_name, _created_at in supplier_rows:
        latest_supplier_by_medicine.setdefault(medicine_id, (supplier_id, supplier_name))

    groups: dict[str, ReorderSupplierGroup] = {}
    for medicine in low_stock:
        current_stock = sellable_stock(medicine, today)
        total_sold = total_sold_by_medicine.get(medicine.id, 0)
        weekly_velocity = Decimal(total_sold) / (Decimal(VELOCITY_LOOKBACK_DAYS) / Decimal(7))
        weekly_velocity = weekly_velocity.quantize(Decimal("0.01"), rounding=ROUND_HALF_UP)
        velocity_target = int((weekly_velocity * REORDER_COVER_WEEKS).to_integral_value(rounding=ROUND_HALF_UP))
        target_stock = max(medicine.reorder_threshold, velocity_target)
        suggested_quantity = max(target_stock - current_stock, 1)

        supplier_id, supplier_name = latest_supplier_by_medicine.get(medicine.id, (None, "No purchase history yet"))
        group_key = str(supplier_id) if supplier_id else "unknown"
        if group_key not in groups:
            groups[group_key] = ReorderSupplierGroup(
                supplier_id=str(supplier_id) if supplier_id else None, supplier_name=supplier_name, items=[]
            )
        groups[group_key].items.append(
            ReorderItem(
                medicine_id=str(medicine.id),
                medicine_name=medicine.name,
                current_stock=current_stock,
                reorder_threshold=medicine.reorder_threshold,
                weekly_velocity=weekly_velocity,
                suggested_quantity=suggested_quantity,
            )
        )

    return ReorderAssistantResponse(
        generated_at=datetime.now(timezone.utc).isoformat(),
        lookback_days=VELOCITY_LOOKBACK_DAYS,
        items=list(groups.values()),
    )
