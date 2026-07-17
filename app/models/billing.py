import enum
import uuid
from datetime import datetime
from decimal import Decimal

from sqlalchemy import DateTime, Enum, ForeignKey, Integer, Numeric, String, UniqueConstraint
from sqlalchemy.dialects.postgresql import UUID
from sqlalchemy.orm import Mapped, mapped_column, relationship

from .base import Base, TimestampMixin, new_uuid


class BillStatus(str, enum.Enum):
    completed = "completed"
    voided = "voided"  # short undo/edit window on the most recently completed bill


class PaymentMode(str, enum.Enum):
    cash = "cash"
    upi = "upi"
    card = "card"
    credit = "credit"  # Udhaar — schema ready, ledger logic deferred to a later phase


class InvoiceSeries(str, enum.Enum):
    """GST and Simple bills are numbered in separate series so a Simple sale
    never introduces a gap in the legally-sensitive GST sequence."""

    gst = "gst"
    simple = "simple"


class Bill(Base, TimestampMixin):
    __tablename__ = "bills"
    __table_args__ = (UniqueConstraint("store_id", "idempotency_key", name="uq_bill_idempotency"),)

    id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), primary_key=True, default=new_uuid)
    store_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), ForeignKey("stores.id"), nullable=False, index=True
    )
    # NULL while only queued locally offline; assigned by the server (via
    # InvoiceCounter, under a row lock) the moment the bill successfully syncs.
    bill_no: Mapped[str | None] = mapped_column(String(50), nullable=True)
    bill_type: Mapped[str] = mapped_column(String(10), nullable=False)  # "simple" | "gst" — switchable per bill
    fy_year: Mapped[str | None] = mapped_column(String(10), nullable=True)  # e.g. "2026-27"; only used in fy_reset mode
    customer_id: Mapped[uuid.UUID | None] = mapped_column(UUID(as_uuid=True), ForeignKey("customers.id"), nullable=True)
    subtotal: Mapped[Decimal] = mapped_column(Numeric(12, 2), nullable=False)
    gst_amount: Mapped[Decimal] = mapped_column(Numeric(12, 2), nullable=False, default=0)
    total: Mapped[Decimal] = mapped_column(Numeric(12, 2), nullable=False)
    payment_mode: Mapped[PaymentMode] = mapped_column(Enum(PaymentMode, name="payment_mode"), nullable=False)
    status: Mapped[BillStatus] = mapped_column(
        Enum(BillStatus, name="bill_status"), default=BillStatus.completed, nullable=False
    )
    # Client-generated (UUID). This is what lets the server dedupe a bill that
    # was completed offline and retries sync after a dropped connection,
    # instead of creating a duplicate sale.
    idempotency_key: Mapped[str] = mapped_column(String(64), nullable=False)
    created_by_id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), ForeignKey("store_users.id"), nullable=False)
    completed_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)

    line_items: Mapped[list["BillLineItem"]] = relationship(back_populates="bill", cascade="all, delete-orphan")


class BillLineItem(Base, TimestampMixin):
    __tablename__ = "bill_line_items"

    id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), primary_key=True, default=new_uuid)
    bill_id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), ForeignKey("bills.id"), nullable=False, index=True)
    medicine_id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), ForeignKey("medicines.id"), nullable=False)
    # FEFO-selected by the server at add-to-cart time — never a manual choice.
    batch_id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), ForeignKey("batches.id"), nullable=False)
    quantity: Mapped[int] = mapped_column(Integer, nullable=False)
    unit_price: Mapped[Decimal] = mapped_column(Numeric(10, 2), nullable=False)
    gst_rate_applied: Mapped[Decimal] = mapped_column(Numeric(5, 2), nullable=False, default=0)
    line_total: Mapped[Decimal] = mapped_column(Numeric(12, 2), nullable=False)

    bill: Mapped["Bill"] = relationship(back_populates="line_items")
    medicine: Mapped["Medicine"] = relationship()
    batch: Mapped["Batch"] = relationship()


class InvoiceCounter(Base, TimestampMixin):
    """Backs sequential invoice numbering. One row per (store, series,
    fy_year): series is 'gst' or 'simple' (kept separate — see InvoiceSeries);
    fy_year is the financial-year label (e.g. "2026-27") when the store is in
    fy_reset mode, or "" (not NULL — Postgres treats NULL as never-equal-to-
    NULL, which would break the uniqueness guarantee this table exists for)
    when in continuous mode or for the simple series. next_number is
    incremented via an atomic UPDATE ... RETURNING at bill-completion time —
    never assigned client-side, so an offline-queued GST bill gets its
    official number only once it syncs."""

    __tablename__ = "invoice_counters"
    __table_args__ = (UniqueConstraint("store_id", "series", "fy_year", name="uq_invoice_counter_store_series_fy"),)

    id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), primary_key=True, default=new_uuid)
    store_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), ForeignKey("stores.id"), nullable=False, index=True
    )
    series: Mapped[InvoiceSeries] = mapped_column(Enum(InvoiceSeries, name="invoice_series"), nullable=False)
    fy_year: Mapped[str] = mapped_column(String(10), nullable=False, default="")
    next_number: Mapped[int] = mapped_column(Integer, nullable=False, default=1)
