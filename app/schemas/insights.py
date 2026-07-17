from decimal import Decimal

from pydantic import BaseModel


class ExpiryAlertItem(BaseModel):
    medicine_id: str
    medicine_name: str
    batch_id: str
    batch_number: str
    expiry_date: str
    quantity_on_hand: int
    days_until_expiry: int
    supplier_id: str | None
    supplier_name: str
    return_window_days: int
    status: str  # "expired" | "return_window"


class ExpiryAlertsResponse(BaseModel):
    generated_at: str
    items: list[ExpiryAlertItem]


class ReorderItem(BaseModel):
    medicine_id: str
    medicine_name: str
    current_stock: int
    reorder_threshold: int
    weekly_velocity: Decimal
    suggested_quantity: int


class ReorderSupplierGroup(BaseModel):
    supplier_id: str | None
    supplier_name: str
    items: list[ReorderItem]


class ReorderAssistantResponse(BaseModel):
    generated_at: str
    lookback_days: int
    items: list[ReorderSupplierGroup]
