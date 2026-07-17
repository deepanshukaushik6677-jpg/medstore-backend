import uuid
from datetime import date
from decimal import Decimal

from pydantic import BaseModel, Field


class SupplierCreateRequest(BaseModel):
    name: str = Field(min_length=2, max_length=200)
    contact_phone: str | None = None
    return_window_days: int = Field(default=90, ge=0)


class SupplierUpdateRequest(BaseModel):
    name: str | None = None
    contact_phone: str | None = None
    return_window_days: int | None = Field(default=None, ge=0)


class SupplierResponse(BaseModel):
    id: str
    name: str
    contact_phone: str | None
    return_window_days: int


class PurchaseLineItemCreateRequest(BaseModel):
    """Batch fields only — no cost. This is what Staff fills in on receiving."""

    medicine_id: uuid.UUID
    batch_number: str = Field(min_length=1, max_length=100)
    expiry_date: date
    quantity: int = Field(gt=0)
    mrp: Decimal = Field(gt=0)


class PurchaseEntryCreateRequest(BaseModel):
    supplier_id: uuid.UUID
    line_items: list[PurchaseLineItemCreateRequest] = Field(min_length=1)


class PurchaseLineItemCostInput(BaseModel):
    line_item_id: uuid.UUID
    unit_cost: Decimal = Field(ge=0)


class CompletePurchaseFinancialsRequest(BaseModel):
    """Owner's follow-up on the same record — the financial half."""

    invoice_no: str = Field(min_length=1, max_length=100)
    invoice_date: date
    due_date: date
    amount: Decimal = Field(gt=0)
    amount_paid: Decimal = Field(default=Decimal("0"), ge=0)
    payment_status: str  # "paid" | "partial" | "due"
    line_item_costs: list[PurchaseLineItemCostInput] = Field(min_length=1)


class PurchaseLineItemResponse(BaseModel):
    id: str
    medicine_id: str
    medicine_name: str
    batch_id: str
    quantity: int
    unit_cost: Decimal | None = None  # None in every Staff-facing response


class PurchaseEntryResponse(BaseModel):
    id: str
    supplier_id: str
    supplier_name: str
    financials_status: str
    created_at: str
    line_items: list[PurchaseLineItemResponse]
    # Purchase-ledger financial details — always None unless caller is Owner.
    invoice_no: str | None = None
    invoice_date: str | None = None
    due_date: str | None = None
    amount: Decimal | None = None
    amount_paid: Decimal | None = None
    payment_status: str | None = None
