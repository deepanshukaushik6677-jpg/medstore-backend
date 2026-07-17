from pydantic import BaseModel, Field


class StoreUserLoginRequest(BaseModel):
    phone: str
    password: str


class AdminLoginRequest(BaseModel):
    email: str
    password: str


class TokenResponse(BaseModel):
    access_token: str
    refresh_token: str
    token_type: str = "bearer"


class RefreshRequest(BaseModel):
    refresh_token: str


class CurrentUserResponse(BaseModel):
    id: str
    role: str
    store_id: str | None = None
    name: str
    tour_completed: bool = True  # Admin has no tour; Owner/Staff start false
    # Session bootstrap data Staff need to resolve "store default" locally
    # when offline (there's no server to ask at that point). None for Admin.
    default_bill_type: str | None = None
    gstin_set: bool = False
