from pydantic import BaseModel, Field


class CreateStoreRequest(BaseModel):
    store_name: str = Field(min_length=2, max_length=200)
    owner_name: str = Field(min_length=2, max_length=120)
    owner_phone: str = Field(min_length=8, max_length=20)
    owner_password: str = Field(min_length=8)


class StoreSummaryResponse(BaseModel):
    id: str
    name: str
    status: str
    created_at: str


class AccessGrantRequest(BaseModel):
    reason: str = Field(min_length=5, max_length=500)
    duration_hours: int = Field(default=24, ge=1, le=168)


class AccessGrantResponse(BaseModel):
    id: str
    reason: str
    granted_at: str
    expires_at: str
    revoked_at: str | None


class UpdateStoreStatusRequest(BaseModel):
    status: str  # "active" | "suspended"
