from datetime import datetime

from sqlalchemy import (
    JSON,
    Boolean,
    CheckConstraint,
    DateTime,
    ForeignKey,
    Integer,
    String,
    UniqueConstraint,
)
from sqlalchemy.orm import DeclarativeBase, Mapped, mapped_column, relationship


class Base(DeclarativeBase):
    pass


class DatabaseTarget(Base):
    __tablename__ = "database_target"

    id: Mapped[int] = mapped_column(primary_key=True, autoincrement=False)
    target_json: Mapped[dict] = mapped_column(JSON)


class Campaign(Base):
    __tablename__ = "campaigns"

    id: Mapped[int] = mapped_column(primary_key=True, autoincrement=False)
    name: Mapped[str] = mapped_column(String(255))
    tracking_url: Mapped[str | None] = mapped_column(String(2048))
    streams: Mapped[list["Stream"]] = relationship(
        back_populates="campaign", order_by="Stream.position", cascade="all, delete-orphan"
    )


class Offer(Base):
    __tablename__ = "offers"

    id: Mapped[int] = mapped_column(primary_key=True, autoincrement=False)
    name: Mapped[str] = mapped_column(String(512))


class Stream(Base):
    __tablename__ = "streams"

    id: Mapped[int] = mapped_column(primary_key=True, autoincrement=False)
    campaign_id: Mapped[int] = mapped_column(ForeignKey("campaigns.id", ondelete="CASCADE"))
    name: Mapped[str] = mapped_column(String(255))
    position: Mapped[int] = mapped_column(Integer)
    editable: Mapped[bool] = mapped_column(Boolean, default=False)
    version: Mapped[int] = mapped_column(Integer, default=1)
    remote_offers: Mapped[list] = mapped_column(JSON, default=list)
    published_state: Mapped[list] = mapped_column(JSON, default=list)
    synced_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    pending_operation_id: Mapped[str | None] = mapped_column(String(32), index=True)
    campaign: Mapped[Campaign] = relationship(back_populates="streams")
    offers: Mapped[list["StreamOffer"]] = relationship(
        cascade="all, delete-orphan", order_by="StreamOffer.position", back_populates="stream"
    )


class StreamOffer(Base):
    __tablename__ = "stream_offers"
    __table_args__ = (CheckConstraint("weight >= 0 AND weight <= 100", name="valid_weight"),)

    stream_id: Mapped[int] = mapped_column(
        ForeignKey("streams.id", ondelete="CASCADE"), primary_key=True
    )
    offer_id: Mapped[int] = mapped_column(ForeignKey("offers.id"), primary_key=True)
    position: Mapped[int] = mapped_column(Integer)
    weight: Mapped[int] = mapped_column(Integer, default=0)
    pinned: Mapped[bool] = mapped_column(Boolean, default=False)
    removed: Mapped[bool] = mapped_column(Boolean, default=False)
    stream: Mapped[Stream] = relationship(back_populates="offers")
    offer: Mapped[Offer] = relationship()


class Operation(Base):
    __tablename__ = "operations"
    __table_args__ = (
        UniqueConstraint("kind", "idempotency_key", name="unique_operation_key"),
        CheckConstraint(
            "status IN ('pending','uncertain','succeeded','failed','conflict')",
            name="valid_operation_status",
        ),
    )

    id: Mapped[str] = mapped_column(String(32), primary_key=True)
    kind: Mapped[str] = mapped_column(String(32))
    idempotency_key: Mapped[str] = mapped_column(String(128))
    request_hash: Mapped[str] = mapped_column(String(64))
    status: Mapped[str] = mapped_column(String(16))
    phase: Mapped[str] = mapped_column(String(48))
    campaign_id: Mapped[int | None] = mapped_column(Integer, index=True)
    stream_id: Mapped[int | None] = mapped_column(Integer, index=True)
    context_json: Mapped[dict] = mapped_column(JSON)
    result_json: Mapped[dict | None] = mapped_column(JSON)
    detail: Mapped[str | None] = mapped_column(String(1024))
    error_code: Mapped[str | None] = mapped_column(String(64))
    error_status: Mapped[int | None] = mapped_column(Integer)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), index=True)
    updated_at: Mapped[datetime] = mapped_column(DateTime(timezone=True))
