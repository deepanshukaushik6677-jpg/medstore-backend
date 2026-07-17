import enum
import uuid

from sqlalchemy import Boolean, Enum, ForeignKey, String
from sqlalchemy.dialects.postgresql import UUID
from sqlalchemy.orm import Mapped, mapped_column, relationship

from .base import Base, TimestampMixin, new_uuid


class StoreStatus(str, enum.Enum):
    active = "active"
    suspended = "suspended"


class BillType(str, enum.Enum):
    simple = "simple"
    gst = "gst"


class GstNumberingMode(str, enum.Enum):
    """Confirmed: owner-configurable per store, not fixed platform-wide."""

    fy_reset = "fy_reset"  # resets every financial year (Apr-Mar), separate series
    continuous = "continuous"  # never resets


class StoreUserRole(str, enum.Enum):
    owner = "owner"
    staff = "staff"


class Store(Base, TimestampMixin):
    __tablename__ = "stores"

    id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), primary_key=True, default=new_uuid)
    name: Mapped[str] = mapped_column(String(200), nullable=False)
    address: Mapped[str | None] = mapped_column(String(500), nullable=True)
    gstin: Mapped[str | None] = mapped_column(String(20), nullable=True)
    # Confirmed optional: store can fill this in later, not a go-live gate.
    drug_license_no: Mapped[str | None] = mapped_column(String(100), nullable=True)
    status: Mapped[StoreStatus] = mapped_column(
        Enum(StoreStatus, name="store_status"), default=StoreStatus.active, nullable=False
    )
    default_bill_type: Mapped[BillType] = mapped_column(
        Enum(BillType, name="bill_type"), default=BillType.simple, nullable=False
    )
    gst_numbering_mode: Mapped[GstNumberingMode] = mapped_column(
        Enum(GstNumberingMode, name="gst_numbering_mode"), default=GstNumberingMode.fy_reset, nullable=False
    )
    gst_invoice_prefix: Mapped[str | None] = mapped_column(String(10), nullable=True)

    users: Mapped[list["StoreUser"]] = relationship(back_populates="store", cascade="all, delete-orphan")
    access_logs: Mapped[list["AdminAccessLog"]] = relationship(back_populates="store")


class StoreUser(Base, TimestampMixin):
    __tablename__ = "store_users"

    id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), primary_key=True, default=new_uuid)
    store_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), ForeignKey("stores.id"), nullable=False, index=True
    )
    role: Mapped[StoreUserRole] = mapped_column(Enum(StoreUserRole, name="store_user_role"), nullable=False)
    name: Mapped[str] = mapped_column(String(120), nullable=False)
    phone: Mapped[str] = mapped_column(String(20), unique=True, nullable=False, index=True)
    password_hash: Mapped[str] = mapped_column(String(255), nullable=False)
    is_active: Mapped[bool] = mapped_column(Boolean, default=True, nullable=False)
    tour_completed: Mapped[bool] = mapped_column(Boolean, default=False, nullable=False)

    store: Mapped["Store"] = relationship(back_populates="users")
