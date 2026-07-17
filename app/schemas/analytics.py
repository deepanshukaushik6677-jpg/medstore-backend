from decimal import Decimal

from pydantic import BaseModel


class AnalyticsOverviewResponse(BaseModel):
    period: str  # "day" | "week" | "month"
    period_start: str
    period_end: str
    order_count: int
    subtotal: Decimal  # pre-tax revenue — the basis for margin, since GST collected isn't real revenue
    gst_collected: Decimal
    total_collected: Decimal  # what customers actually paid (subtotal + gst)
    gross_profit: Decimal  # only counts line items whose batch has a known purchase_cost
    gross_margin_percent: Decimal
    lines_pending_cost: int  # sales made from a batch whose cost isn't in yet — margin is understated by this much


class TrendPoint(BaseModel):
    label: str  # e.g. "2026-07-04", ISO week start, or "2026-07"
    period_start: str
    period_end: str
    order_count: int
    total_collected: Decimal
    gross_profit: Decimal


class AnalyticsTrendResponse(BaseModel):
    granularity: str  # "day" | "week" | "month"
    points: list[TrendPoint]


class TopSellerItem(BaseModel):
    medicine_id: str
    medicine_name: str
    quantity_sold: int
    revenue: Decimal


class TopSellersResponse(BaseModel):
    period: str
    period_start: str
    period_end: str
    items: list[TopSellerItem]


class SlowMoverItem(BaseModel):
    medicine_id: str
    medicine_name: str
    current_stock: int
    units_sold_in_period: int


class SlowMoversResponse(BaseModel):
    days: int
    items: list[SlowMoverItem]  # sorted worst (least-selling) first
