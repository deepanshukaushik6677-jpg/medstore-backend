import uuid
from datetime import datetime

from sqlalchemy import DateTime, ForeignKey, String, func
from sqlalchemy.dialects.postgresql import UUID
from sqlalchemy.orm import Mapped, mapped_column, relationship

from .base import Base, TimestampMixin, new_uuid


class PlatformAdmin(Base, TimestampMixin):
    """Operated by the software provider, not store staff. Deliberately kept
    as its own entity (not a role on StoreUser) so that "no ambient access
    to store data" is a structural fact, not a permission check that could
    drift over time."""

    __tablename__ = "platform_admins"

    id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), primary_key=True, default=new_uuid)
    name: Mapped[str] = mapped_column(String(120), nullable=False)
    email: Mapped[str] = mapped_column(String(255), unique=True, nullable=False, index=True)
    password_hash: Mapped[str] = mapped_column(String(255), nullable=False)
    is_active: Mapped[bool] = mapped_column(default=True, nullable=False)

    access_logs: Mapped[list["AdminAccessLog"]] = relationship(back_populates="admin")


class AdminAccessLog(Base, TimestampMixin):
    """The ONLY mechanism through which an admin ever gets a scoped read into
    one store's data: explicit reason, time-boxed, permanently on record.
    See dependencies.get_admin_scoped_store_id, which enforces this at query
    time — an admin token alone grants nothing without a live row here."""

    __tablename__ = "admin_access_logs"

    id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), primary_key=True, default=new_uuid)
    admin_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), ForeignKey("platform_admins.id"), nullable=False, index=True
    )
    store_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), ForeignKey("stores.id"), nullable=False, index=True
    )
    reason: Mapped[str] = mapped_column(String(500), nullable=False)
    granted_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now(), nullable=False)
    expires_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    revoked_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)

    admin: Mapped["PlatformAdmin"] = relationship(back_populates="access_logs")
    store: Mapped["Store"] = relationship(back_populates="access_logs")
