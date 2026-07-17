import uuid
from datetime import date, datetime, timedelta, timezone
from decimal import ROUND_HALF_UP, Decimal

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.orm import selectinload


def round2(value: Decimal) -> Decimal:
    return value.quantize(Decimal("0.01"), rounding=ROUND_HALF_UP)


# India has one fixed offset year-round (no DST), so a plain fixed-offset
# tzinfo is correct here — a real multi-country product would need a proper
# per-store timezone field, but this app is explicitly India-only per the brief.
IST = timezone(timedelta(hours=5, minutes=30))


def period_bounds(period: str) -> tuple[datetime, datetime]:
    """Day/week/month boundaries computed in IST, returned as UTC — so
    "today" matches the calendar day the store owner is actually living in.
    Week starts Monday. Shared by Analytics and the Today dashboard so both
    agree on what "today" means."""
    today = datetime.now(IST).date()
    if period == "day":
        start = datetime(today.year, today.month, today.day, tzinfo=IST)
        end = start + timedelta(days=1)
    elif period == "week":
        monday = today - timedelta(days=today.weekday())
        start = datetime(monday.year, monday.month, monday.day, tzinfo=IST)
        end = start + timedelta(days=7)
    elif period == "month":
        start = datetime(today.year, today.month, 1, tzinfo=IST)
        end = datetime(today.year + 1, 1, 1, tzinfo=IST) if today.month == 12 else datetime(
            today.year, today.month + 1, 1, tzinfo=IST
        )
    else:
        raise ValueError("period must be 'day', 'week', or 'month'")
    return start.astimezone(timezone.utc), end.astimezone(timezone.utc)


def sellable_stock(medicine, today: date) -> int:
    """Stock that can actually be sold — excludes expired batches. Shared by
    Fast Billing's insufficient-stock check, the Reorder Assistant, Slow
    Movers, and the Today dashboard's low-stock count, so they all agree on
    what "in stock" means."""
    return sum(b.quantity_on_hand for b in medicine.batches if b.expiry_date >= today)


async def get_expiry_alert_rows(db: AsyncSession, store_id: uuid.UUID):
    """Raw (medicine, batch, supplier, days_until_expiry, return_window,
    status) tuples behind Expiry Protection. Shared by the /expiry-alerts
    endpoint and the Today dashboard's expiring-soon count, so a medicine
    only counts as "flagged" in one place in the code."""
    from .models import Medicine, PurchaseEntry, PurchaseLineItem, Supplier  # local import avoids a circular import

    today = date.today()
    query = (
        select(Medicine, PurchaseLineItem, Supplier)
        .join(PurchaseLineItem, PurchaseLineItem.medicine_id == Medicine.id)
        .outerjoin(PurchaseEntry, PurchaseEntry.id == PurchaseLineItem.purchase_entry_id)
        .outerjoin(Supplier, Supplier.id == PurchaseEntry.supplier_id)
        .where(Medicine.store_id == store_id)
        .options(selectinload(PurchaseLineItem.batch))
    )
    rows = (await db.execute(query)).all()

    results = []
    for medicine, line_item, supplier in rows:
        batch = line_item.batch
        if batch is None or batch.quantity_on_hand <= 0:
            continue
        days_until_expiry = (batch.expiry_date - today).days
        return_window = supplier.return_window_days if supplier else 0
        if days_until_expiry < 0:
            status_label = "expired"
        elif days_until_expiry <= return_window:
            status_label = "return_window"
        else:
            continue
        results.append((medicine, batch, supplier, days_until_expiry, return_window, status_label))
    return results
