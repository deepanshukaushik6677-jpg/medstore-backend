from datetime import date
from decimal import Decimal

from pydantic import BaseModel, Field


class StaffMedicineCreateRequest(BaseModel):
    """What Staff can set when adding a never-before-stocked medicine during
    receiving. No tax fields — those are an Owner decision."""

    name: str = Field(min_length=1, max_length=200)
    barcode: str | None = None
    category: str | None = None
    unit: str = "strip"
    zone: str | None = None
    rack: str | None = None
    shelf: str | None = None
    box: str | None = None
    reorder_threshold: int = Field(default=0, ge=0)


class OwnerMedicineCreateRequest(StaffMedicineCreateRequest):
    hsn_code: str | None = None
    gst_rate: Decimal | None = Field(default=None, ge=0, le=100)


class OwnerMedicineUpdateRequest(BaseModel):
    """Superset used to parse the request body; the handler rejects any
    field outside {barcode, zone, rack, shelf, box} if the caller is Staff —
    matching the Locator brief's "Owner/Staff can edit a medicine's location"."""

    name: str | None = None
    barcode: str | None = None
    category: str | None = None
    unit: str | None = None
    zone: str | None = None
    rack: str | None = None
    shelf: str | None = None
    box: str | None = None
    reorder_threshold: int | None = Field(default=None, ge=0)
    hsn_code: str | None = None
    gst_rate: Decimal | None = Field(default=None, ge=0, le=100)


class BatchResponse(BaseModel):
    id: str
    batch_number: str
    expiry_date: date
    quantity_on_hand: int
    mrp: Decimal
    # Cost price is Owner-only — always None in a Staff-facing response,
    # regardless of what's actually stored.
    purchase_cost: Decimal | None = None


class MedicineResponse(BaseModel):
    id: str
    name: str
    barcode: str | None
    category: str | None
    unit: str
    zone: str | None
    rack: str | None
    shelf: str | None
    box: str | None
    reorder_threshold: int
    hsn_code: str | None
    gst_rate: Decimal | None
    total_stock: int
    batches: list[BatchResponse] = []
