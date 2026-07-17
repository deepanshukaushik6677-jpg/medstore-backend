import uuid
from datetime import date
from decimal import Decimal

from sqlalchemy import Date, ForeignKey, Integer, Numeric, String
from sqlalchemy.dialects.postgresql import UUID
from sqlalchemy.orm import Mapped, mapped_column, relationship

from .base import Base, TimestampMixin, new_uuid


class Medicine(Base, TimestampMixin):
    __tablename__ = "medicines"

    id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), primary_key=True, default=new_uuid)
    store_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), ForeignKey("stores.id"), nullable=False, index=True
    )
    name: Mapped[str] = mapped_column(String(200), nullable=False, index=True)
    barcode: Mapped[str | None] = mapped_column(String(64), nullable=True, index=True)
    category: Mapped[str | None] = mapped_column(String(100), nullable=True)
    hsn_code: Mapped[str | None] = mapped_column(String(20), nullable=True)
    # Per-medicine, never a flat platform-wide rate — different categories attract different GST rates.
    gst_rate: Mapped[Decimal | None] = mapped_column(Numeric(5, 2), nullable=True)
    zone: Mapped[str | None] = mapped_column(String(50), nullable=True)
    rack: Mapped[str | None] = mapped_column(String(50), nullable=True)
    shelf: Mapped[str | None] = mapped_column(String(50), nullable=True)
    box: Mapped[str | None] = mapped_column(String(50), nullable=True)
    reorder_threshold: Mapped[int] = mapped_column(Integer, default=0, nullable=False)
    unit: Mapped[str] = mapped_column(String(20), default="strip", nullable=False)

    batches: Mapped[list["Batch"]] = relationship(back_populates="medicine", cascade="all, delete-orphan")


class Batch(Base, TimestampMixin):
    """The single source of truth for stock. Medicine never carries a
    quantity field directly — a medicine's total stock is the sum of its
    batches' quantity_on_hand. Every sale and every purchase line touches
    this table directly; there is no separate sync step."""

    __tablename__ = "batches"

    id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), primary_key=True, default=new_uuid)
    medicine_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), ForeignKey("medicines.id"), nullable=False, index=True
    )
    store_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), ForeignKey("stores.id"), nullable=False, index=True
    )
    batch_number: Mapped[str] = mapped_column(String(100), nullable=False)
    expiry_date: Mapped[date] = mapped_column(Date, nullable=False, index=True)
    quantity_on_hand: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    # Nullable: set by Staff's stock-in as NULL, filled in once Owner completes
    # the purchase entry's financials. Profit/margin queries join against this
    # live value rather than a snapshot, so a late cost entry corrects past
    # margin reports automatically once filled in.
    purchase_cost: Mapped[Decimal | None] = mapped_column(Numeric(10, 2), nullable=True)
    mrp: Mapped[Decimal] = mapped_column(Numeric(10, 2), nullable=False)

    medicine: Mapped["Medicine"] = relationship(back_populates="batches")
