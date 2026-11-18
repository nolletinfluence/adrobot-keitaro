from typing import Annotated, Literal

import pycountry
from pydantic import BaseModel, ConfigDict, Field, StrictInt, field_validator, model_validator

PositiveId = Annotated[StrictInt, Field(gt=0)]
Weight = Annotated[StrictInt, Field(ge=0, le=100)]


class InputModel(BaseModel):
    model_config = ConfigDict(extra="forbid")


class VersionInput(InputModel):
    version: Annotated[StrictInt, Field(ge=1)]


class FetchInput(InputModel):
    discard_draft: bool = False


class DemoFault(InputModel):
    kind: Literal["publish_timeout", "create_timeout"]


class CreateCampaign(InputModel):
    name: str = Field(min_length=1, max_length=255)
    country_code: str
    offer_id: PositiveId
    domain_id: PositiveId
    group_id: PositiveId
    source_id: PositiveId

    @field_validator("name")
    @classmethod
    def strip_name(cls, value: str) -> str:
        value = value.strip()
        if not value:
            raise ValueError("Campaign name is required")
        return value

    @field_validator("country_code")
    @classmethod
    def normalize_country(cls, value: str) -> str:
        value = value.strip().upper()
        if pycountry.countries.get(alpha_2=value) is None:
            raise ValueError("Use an ISO 3166-1 country code, for example KG")
        return value


class AddOffer(VersionInput):
    offer_id: PositiveId


class PatchOffer(VersionInput):
    removed: bool | None = None
    pinned: bool | None = None
    weight: Weight | None = None

    @model_validator(mode="after")
    def validate_changes(self):
        if not self.model_fields_set.intersection({"removed", "pinned", "weight"}):
            raise ValueError("Provide an offer change")
        if any(getattr(self, key) is None for key in self.model_fields_set - {"version"}):
            raise ValueError("Offer changes cannot be null")
        if self.weight is not None and self.pinned is False:
            raise ValueError("A manually set weight must be pinned")
        if self.removed is True and (self.weight is not None or self.pinned is True):
            raise ValueError("Removed offers cannot have a pinned weight")
        return self


class CatalogItem(BaseModel):
    id: int
    name: str


class StreamOfferView(CatalogItem):
    weight: int
    pinned: bool
    removed: bool


class OperationView(BaseModel):
    id: str
    kind: Literal["create_campaign", "publish_stream"]
    status: Literal["pending", "uncertain", "succeeded", "failed", "conflict"]
    phase: str
    campaign_id: int | None
    stream_id: int | None
    detail: str | None
    error_code: str | None
    created_at: str
    updated_at: str


class StreamView(BaseModel):
    id: int
    campaign_id: int
    name: str
    editable: bool
    dirty: bool
    version: int
    offers: list[StreamOfferView]
    published_offers: list[StreamOfferView]
    operation: OperationView | None
    total_weight: int
    synced_at: str | None


class CampaignView(CatalogItem):
    tracking_url: str | None
    streams: list[StreamView]
