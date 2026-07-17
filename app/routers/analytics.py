import uuid
from datetime import date, datetime, timedelta, timezone
from decimal import Decimal

from fastapi import APIRouter, Depends, HTTPException, status
from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.orm import selectinload

from ..database import get_db
from ..dependencies import get_tenant_store_id, require_roles
from ..models import Batch, Bill, BillLineItem, BillStatus, Medicine
from ..schemas.analytics import (
    AnalyticsOverviewResponse,
    AnalyticsTrendResponse,
    SlowMoverItem,
    SlowMoversResponse,
    TopSellerItem,
    TopSellersResponse,
    TrendPoint,
)
from ..utils import IST, period_bounds, round2

router = APIRouter(prefix="/analytics", tags=["analytics"], dependencies=[Depends(require_roles("owner"))])


def _period_bounds(period: str) -> tuple[datetime, datetime]:
    try:
        return period_bounds(period)
    except ValueError as exc:
        raise HTTPException(status.HTTP_400_BAD_REQUEST, str(exc)) from exc


def _generate_buckets(granularity: str, count: int) -> list[tuple[datetime, datetime, str]]:
    """`count` consecutive IST-aligned buckets ending at the current one,
    returned as (utc_start, utc_end, label)."""
    today = datetime.now(IST).date()
    buckets: list[tuple[datetime, datetime, str]] = []

    if granularity == "day":
        for i in range(count - 1, -1, -1):
            d = today - timedelta(days=i)
            start = datetime(d.year, d.month, d.day, tzinfo=IST)
            buckets.append((start, start + timedelta(days=1), d.isoformat()))
    elif granularity == "week":
        this_monday = today - timedelta(days=today.weekday())
        for i in range(count - 1, -1, -1):
            monday = this_monday - timedelta(weeks=i)
            start = datetime(monday.year, monday.month, monday.day, tzinfo=IST)
            buckets.append((start, start + timedelta(days=7), monday.isoformat()))
    elif granularity == "month":
        base_index = today.year * 12 + (today.month - 1)
        for i in range(count - 1, -1, -1):
            idx = base_index - i
            y, m = divmod(idx, 12)
            m += 1
            start = datetime(y, m, 1, tzinfo=IST)
            end = datetime(y + 1, 1, 1, tzinfo=IST) if m == 12 else datetime(y, m + 1, 1, tzinfo=IST)
            buckets.append((start, end, f"{y:04d}-{m:02d}"))
    else:
        raise HTTPException(status.HTTP_400_BAD_REQUEST, "granularity must be 'day', 'week', or 'month'")

    return [(s.astimezone(timezone.utc), e.astimezone(timezone.utc), label) for s, e, label in buckets]


async def _bills_with_lines(db: AsyncSession, store_id: uuid.UUID, start: datetime, end: datetime) -> list[Bill]:
    result = await db.execute(
        select(Bill)
        .where(
            Bill.store_id == store_id,
            Bill.status == BillStatus.completed,
            Bill.completed_at >= start,
            Bill.completed_at < end,
        )
        .options(selectinload(Bill.line_items))
    )
    return result.scalars().unique().all()


async def _cost_by_batch(db: AsyncSession, bills: list[Bill]) -> dict[uuid.UUID, Decimal | None]:
    batch_ids = {li.batch_id for b in bills for li in b.line_items}
    if not batch_ids:
        return {}
    rows = (await db.execute(select(Batch.id, Batch.purchase_cost).where(Batch.id.in_(batch_ids)))).all()
    return {row[0]: row[1] for row in rows}


def _profit_for_bills(bills: list[Bill], cost_by_batch: dict[uuid.UUID, Decimal | None]) -> tuple[Decimal, int]:
    """Gross profit against subtotal (pre-tax) — GST collected isn't revenue.
    Line items whose batch cost isn't set yet (Owner hasn't completed that
    purchase entry's financials) are skipped and counted separately, so the
    figure is honest about being a lower bound rather than silently wrong."""
    profit = Decimal("0")
    pending = 0
    for bill in bills:
        for li in bill.line_items:
            cost = cost_by_batch.get(li.batch_id)
            if cost is None:
                pending += 1
                continue
            profit += (li.unit_price - cost) * li.quantity
    return round2(profit), pending


@router.get("/overview", response_model=AnalyticsOverviewResponse)
async def get_overview(
    period: str = "day", store_id: uuid.UUID = Depends(get_tenant_store_id), db: AsyncSession = Depends(get_db)
):
    start, end = _period_bounds(period)
    bills = await _bills_with_lines(db, store_id, start, end)
    cost_by_batch = await _cost_by_batch(db, bills)

    subtotal = round2(sum((b.subtotal for b in bills), Decimal("0")))
    gst_collected = round2(sum((b.gst_amount for b in bills), Decimal("0")))
    total_collected = round2(sum((b.total for b in bills), Decimal("0")))
    gross_profit, pending = _profit_for_bills(bills, cost_by_batch)
    margin_percent = round2(gross_profit / subtotal * 100) if subtotal > 0 else Decimal("0")

    return AnalyticsOverviewResponse(
        period=period,
        period_start=start.isoformat(),
        period_end=end.isoformat(),
        order_count=len(bills),
        subtotal=subtotal,
        gst_collected=gst_collected,
        total_collected=total_collected,
        gross_profit=gross_profit,
        gross_margin_percent=margin_percent,
        lines_pending_cost=pending,
    )


@router.get("/trend", response_model=AnalyticsTrendResponse)
async def get_trend(
    granularity: str = "day",
    count: int = 14,
    store_id: uuid.UUID = Depends(get_tenant_store_id),
    db: AsyncSession = Depends(get_db),
):
    count = max(1, min(count, 90))
    buckets = _generate_buckets(granularity, count)

    # One query across the whole range, then bucket in Python — simpler and
    # more correct than fighting Postgres timezone truncation for IST-aligned
    # boundaries, and plenty fast at this scale.
    overall_bills = await _bills_with_lines(db, store_id, buckets[0][0], buckets[-1][1])
    cost_by_batch = await _cost_by_batch(db, overall_bills)

    points = []
    for start, end, label in buckets:
        bucket_bills = [b for b in overall_bills if start <= b.completed_at < end]
        gross_profit, _pending = _profit_for_bills(bucket_bills, cost_by_batch)
        points.append(
            TrendPoint(
                label=label,
                period_start=start.isoformat(),
                period_end=end.isoformat(),
                order_count=len(bucket_bills),
                total_collected=round2(sum((b.total for b in bucket_bills), Decimal("0"))),
                gross_profit=gross_profit,
            )
        )

    return AnalyticsTrendResponse(granularity=granularity, points=points)


@router.get("/top-sellers", response_model=TopSellersResponse)
async def get_top_sellers(
    period: str = "week",
    limit: int = 10,
    store_id: uuid.UUID = Depends(get_tenant_store_id),
    db: AsyncSession = Depends(get_db),
):
    start, end = _period_bounds(period)
    rows = (
        await db.execute(
            select(
                BillLineItem.medicine_id,
                Medicine.name,
                func.sum(BillLineItem.quantity),
                func.sum(BillLineItem.line_total),
            )
            .join(Bill, Bill.id == BillLineItem.bill_id)
            .join(Medicine, Medicine.id == BillLineItem.medicine_id)
            .where(
                Bill.store_id == store_id,
                Bill.status == BillStatus.completed,
                Bill.completed_at >= start,
                Bill.completed_at < end,
            )
            .group_by(BillLineItem.medicine_id, Medicine.name)
            .order_by(func.sum(BillLineItem.quantity).desc())
            .limit(max(1, min(limit, 50)))
        )
    ).all()

    items = [
        TopSellerItem(medicine_id=str(r[0]), medicine_name=r[1], quantity_sold=r[2], revenue=round2(r[3]))
        for r in rows
    ]
    return TopSellersResponse(period=period, period_start=start.isoformat(), period_end=end.isoformat(), items=items)


@router.get("/slow-movers", response_model=SlowMoversResponse)
async def get_slow_movers(
    days: int = 60,
    limit: int = 20,
    store_id: uuid.UUID = Depends(get_tenant_store_id),
    db: AsyncSession = Depends(get_db),
):
    """In-stock medicines sorted by least sold over the lookback window —
    the dead-stock candidates that feed into not over-ordering them again."""
    days = max(1, min(days, 365))
    today = date.today()

    medicines = (
        (
            await db.execute(
                select(Medicine).where(Medicine.store_id == store_id).options(selectinload(Medicine.batches))
            )
        )
        .scalars()
        .unique()
        .all()
    )
    # Expired stock isn't "dead stock feeding an over-order decision" — it's
    # already a write-off (Expiry Protection's job), so it's excluded here too.
    in_stock = {
        m.id: (m, sum(b.quantity_on_hand for b in m.batches if b.expiry_date >= today))
        for m in medicines
        if sum(b.quantity_on_hand for b in m.batches if b.expiry_date >= today) > 0
    }
    if not in_stock:
        return SlowMoversResponse(days=days, items=[])

    since = datetime.now(timezone.utc) - timedelta(days=days)
    sold_rows = (
        await db.execute(
            select(BillLineItem.medicine_id, func.sum(BillLineItem.quantity))
            .join(Bill, Bill.id == BillLineItem.bill_id)
            .where(
                Bill.store_id == store_id,
                Bill.status == BillStatus.completed,
                Bill.completed_at >= since,
                BillLineItem.medicine_id.in_(in_stock.keys()),
            )
            .group_by(BillLineItem.medicine_id)
        )
    ).all()
    sold_by_medicine = {row[0]: row[1] for row in sold_rows}

    items = [
        SlowMoverItem(
            medicine_id=str(medicine_id),
            medicine_name=medicine.name,
            current_stock=stock,
            units_sold_in_period=sold_by_medicine.get(medicine_id, 0),
        )
        for medicine_id, (medicine, stock) in in_stock.items()
    ]
    items.sort(key=lambda i: i.units_sold_in_period)
    return SlowMoversResponse(days=days, items=items[: max(1, min(limit, 100))])
