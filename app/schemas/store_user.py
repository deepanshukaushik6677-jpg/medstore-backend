from pydantic import BaseModel, Field


class CreateStaffRequest(BaseModel):
    name: str = Field(min_length=2, max_length=120)
    phone: str = Field(min_length=8, max_length=20)
    password: str = Field(min_length=8)


class UpdateStaffRequest(BaseModel):
    is_active: bool | None = None
    new_password: str | None = Field(default=None, min_length=8)


class StaffResponse(BaseModel):
    id: str
    name: str
    phone: str
    is_active: bool
    tour_completed: bool
