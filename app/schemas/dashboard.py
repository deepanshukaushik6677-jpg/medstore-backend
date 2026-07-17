from decimal import Decimal

from pydantic import BaseModel


class TodayDashboardResponse(BaseModel):
    date: str
    sales_total: Decimal
    order_count: int
    low_stock_count: int
    expiring_soon_count: int
    # Purchase-ledger financial detail — Owner-only. Staff-facing responses
    # always carry these as None, same masking rule as everywhere else
    # supplier financials show up (see purchasing.py).
    pending_dues_count: int | None = None
    pending_dues_amount: Decimal | None = None
