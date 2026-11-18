from datetime import datetime

from sqlalchemy import JSON, Boolean, CheckConstraint, DateTime, ForeignKey, Integer, String
from sqlalchemy.orm import DeclarativeBase, Mapped, mapped_column, relationship


class Base(DeclarativeBase):
    pass


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
