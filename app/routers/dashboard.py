import uuid
from datetime import date
from decimal import Decimal

from fastapi import APIRouter, Depends
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.orm import selectinload

from ..database import get_db
from ..dependencies import CurrentPrincipal, get_current_principal, get_tenant_store_id, require_roles
from ..models import Bill, BillStatus, Medicine, PurchaseEntry, PurchaseFinancialsStatus, PurchasePaymentStatus
from ..schemas.dashboard import TodayDashboardResponse
from ..utils import get_expiry_alert_rows, period_bounds, round2, sellable_stock

router = APIRouter(prefix="/dashboard", tags=["dashboard"], dependencies=[Depends(require_roles("owner", "staff"))])


@router.get("/today", response_model=TodayDashboardResponse)
async def get_today_dashboard(
    principal: CurrentPrincipal = Depends(get_current_principal),
    store_id: uuid.UUID = Depends(get_tenant_store_id),
    db: AsyncSession = Depends(get_db),
):
    """One call for the whole Today home screen: sales total, low-stock
    count, expiring-soon count, pending supplier dues — no menu-digging, per
    the brief. Pending supplier dues is purchase-ledger financial detail, so
    it's the one field that's null for Staff — same masking rule used
    everywhere else in the app."""
    today = date.today()
    start, end = period_bounds("day")

    bills = (
        (
            await db.execute(
                select(Bill).where(
                    Bill.store_id == store_id,
                    Bill.status == BillStatus.completed,
                    Bill.completed_at >= start,
                    Bill.completed_at < end,
                )
            )
        )
        .scalars()
        .all()
    )
    sales_total = round2(sum((b.total for b in bills), Decimal("0")))

    medicines = (
        (await db.execute(select(Medicine).where(Medicine.store_id == store_id).options(selectinload(Medicine.batches))))
        .scalars()
        .unique()
        .all()
    )
    low_stock_count = sum(1 for m in medicines if sellable_stock(m, today) <= m.reorder_threshold)

    expiring_soon_count = len(await get_expiry_alert_rows(db, store_id))

    pending_dues_count = None
    pending_dues_amount = None
    if principal.role == "owner":
        due_entries = (
            (
                await db.execute(
                    select(PurchaseEntry).where(
                        PurchaseEntry.store_id == store_id,
                        PurchaseEntry.financials_status == PurchaseFinancialsStatus.completed,
                        PurchaseEntry.payment_status.in_(
                            [PurchasePaymentStatus.due, PurchasePaymentStatus.partial]
                        ),
                    )
                )
            )
            .scalars()
            .all()
        )
        pending_dues_count = len(due_entries)
        pending_dues_amount = round2(sum(((e.amount - e.amount_paid) for e in due_entries), Decimal("0")))

    return TodayDashboardResponse(
        date=today.isoformat(),
        sales_total=sales_total,
        order_count=len(bills),
        low_stock_count=low_stock_count,
        expiring_soon_count=expiring_soon_count,
        pending_dues_count=pending_dues_count,
        pending_dues_amount=pending_dues_amount,
    )
