from pydantic import BaseModel, Field


class StoreSettingsUpdateRequest(BaseModel):
    name: str | None = Field(default=None, min_length=2, max_length=200)
    address: str | None = None
    gstin: str | None = None
    drug_license_no: str | None = None  # confirmed optional — never enforced as a go-live gate
    default_bill_type: str | None = None  # "simple" | "gst"
    gst_numbering_mode: str | None = None  # "fy_reset" | "continuous" — confirmed owner-configurable
    gst_invoice_prefix: str | None = None


class StoreSettingsResponse(BaseModel):
    id: str
    name: str
    address: str | None
    gstin: str | None
    drug_license_no: str | None
    status: str
    default_bill_type: str
    gst_numbering_mode: str
    gst_invoice_prefix: str | None
