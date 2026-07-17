import uuid
from decimal import Decimal

from pydantic import BaseModel, Field


class BillCartItemRequest(BaseModel):
    medicine_id: uuid.UUID
    quantity: int = Field(gt=0)


class CreateBillRequest(BaseModel):
    # Client-generated, unique per store. This is what makes the offline
    # queue safe: retrying this same request after a dropped connection
    # returns the original bill instead of creating a duplicate sale.
    idempotency_key: str = Field(min_length=8, max_length=64)
    bill_type: str | None = None  # "simple" | "gst"; omit to use the store's default
    payment_mode: str  # "cash" | "upi" | "card" — "credit" (Udhaar) isn't wired up yet
    customer_id: uuid.UUID | None = None
    items: list[BillCartItemRequest] = Field(min_length=1)


class ResolvedLineItem(BaseModel):
    """Shared shape for both a live preview and a persisted bill's line
    items. FEFO already happened by the time this is built — batch_id here
    is the batch actually drawn from, not a choice the client made."""

    medicine_id: str
    medicine_name: str
    hsn_code: str | None
    batch_id: str
    quantity: int
    unit_price: Decimal
    gst_rate_applied: Decimal
    gst_amount: Decimal
    line_total: Decimal


class BillLineItemResponse(ResolvedLineItem):
    id: str


class BillPreviewResponse(BaseModel):
    """Read-only cart total, computed live as Staff adds items — nothing is
    written to the ledger. This is what powers "watch the cart total grow"
    without committing a sale on every keystroke."""

    bill_type: str
    subtotal: Decimal
    gst_amount: Decimal
    cgst_amount: Decimal
    sgst_amount: Decimal
    total: Decimal
    line_items: list[ResolvedLineItem]


class BillResponse(BaseModel):
    id: str
    bill_no: str | None
    bill_type: str
    fy_year: str | None
    status: str
    payment_mode: str
    customer_id: str | None
    subtotal: Decimal
    gst_amount: Decimal
    cgst_amount: Decimal
    sgst_amount: Decimal
    total: Decimal
    completed_at: str
    void_eligible: bool  # true only while still inside the undo/edit grace window
    line_items: list[BillLineItemResponse]
