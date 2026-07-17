import enum
import uuid
from datetime import date
from decimal import Decimal

from sqlalchemy import Date, Enum, ForeignKey, Integer, Numeric, String
from sqlalchemy.dialects.postgresql import UUID
from sqlalchemy.orm import Mapped, mapped_column, relationship

from .base import Base, TimestampMixin, new_uuid


class PurchaseFinancialsStatus(str, enum.Enum):
    # Confirmed workflow: one form, but financial fields are locked from Staff.
    # Staff submits batch fields only -> record is created in this state.
    pending_review = "pending_review"
    # Owner opens the same record and fills in invoice_no/amount/due_date/
    # payment_status/line-item unit_cost -> record moves here.
    completed = "completed"


class PurchasePaymentStatus(str, enum.Enum):
    paid = "paid"
    partial = "partial"
    due = "due"


class Supplier(Base, TimestampMixin):
    __tablename__ = "suppliers"

    id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), primary_key=True, default=new_uuid)
    store_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), ForeignKey("stores.id"), nullable=False, index=True
    )
    name: Mapped[str] = mapped_column(String(200), nullable=False)
    contact_phone: Mapped[str | None] = mapped_column(String(20), nullable=True)
    # Per-supplier, never one fixed number applied to all stock.
    return_window_days: Mapped[int] = mapped_column(Integer, nullable=False, default=90)

    purchase_entries: Mapped[list["PurchaseEntry"]] = relationship(back_populates="supplier")


class PurchaseEntry(Base, TimestampMixin):
    """Created by Staff on stock-in receiving (batch fields only — see
    PurchaseLineItem/Batch). invoice_no/invoice_date/due_date/amount/
    payment_status stay NULL and are hidden from Staff in the API response
    (see schemas + router) until an Owner completes them on this same
    record. Batches are live the moment Staff submits, regardless of
    financials_status — stock accuracy never waits on bookkeeping."""

    __tablename__ = "purchase_entries"

    id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), primary_key=True, default=new_uuid)
    store_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), ForeignKey("stores.id"), nullable=False, index=True
    )
    supplier_id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), ForeignKey("suppliers.id"), nullable=False)
    financials_status: Mapped[PurchaseFinancialsStatus] = mapped_column(
        Enum(PurchaseFinancialsStatus, name="purchase_financials_status"),
        default=PurchaseFinancialsStatus.pending_review,
        nullable=False,
        index=True,
    )
    invoice_no: Mapped[str | None] = mapped_column(String(100), nullable=True)
    invoice_date: Mapped[date | None] = mapped_column(Date, nullable=True)
    due_date: Mapped[date | None] = mapped_column(Date, nullable=True)
    amount: Mapped[Decimal | None] = mapped_column(Numeric(12, 2), nullable=True)
    amount_paid: Mapped[Decimal] = mapped_column(Numeric(12, 2), nullable=False, default=0)
    payment_status: Mapped[PurchasePaymentStatus | None] = mapped_column(
        Enum(PurchasePaymentStatus, name="purchase_payment_status"), nullable=True
    )
    created_by_id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), ForeignKey("store_users.id"), nullable=False)
    completed_by_id: Mapped[uuid.UUID | None] = mapped_column(
        UUID(as_uuid=True), ForeignKey("store_users.id"), nullable=True
    )

    supplier: Mapped["Supplier"] = relationship(back_populates="purchase_entries")
    line_items: Mapped[list["PurchaseLineItem"]] = relationship(
        back_populates="purchase_entry", cascade="all, delete-orphan"
    )


class PurchaseLineItem(Base, TimestampMixin):
    __tablename__ = "purchase_line_items"

    id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), primary_key=True, default=new_uuid)
    purchase_entry_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), ForeignKey("purchase_entries.id"), nullable=False, index=True
    )
    medicine_id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), ForeignKey("medicines.id"), nullable=False)
    batch_id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), ForeignKey("batches.id"), nullable=False)
    quantity: Mapped[int] = mapped_column(Integer, nullable=False)
    # NULL when Staff creates the line; filled in when Owner completes financials.
    unit_cost: Mapped[Decimal | None] = mapped_column(Numeric(10, 2), nullable=True)

    purchase_entry: Mapped["PurchaseEntry"] = relationship(back_populates="line_items")
    medicine: Mapped["Medicine"] = relationship()
    batch: Mapped["Batch"] = relationship()
