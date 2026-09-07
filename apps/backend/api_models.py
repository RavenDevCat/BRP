from __future__ import annotations

from typing import Any, Literal

from pydantic import BaseModel, ConfigDict, Field, field_validator, model_validator


class FlexiblePayload(BaseModel):
    model_config = ConfigDict(extra="allow")


class ComputeRequest(BaseModel):
    model_config = ConfigDict(extra="allow")

    config: dict[str, Any] = Field(default_factory=dict)
    prepared_payload: dict[str, Any] = Field(default_factory=dict)


class CreateJobRequest(BaseModel):
    model_config = ConfigDict(extra="allow")

    config: dict[str, Any] = Field(default_factory=dict)
    prepared_payload: dict[str, Any] = Field(default_factory=dict)
    metadata: dict[str, Any] = Field(default_factory=dict)


class AiAuditRequest(BaseModel):
    model_config = ConfigDict(extra="allow")

    language: str | None = None
    force: bool = False


class MeasurementReviewRequest(BaseModel):
    model_config = ConfigDict(extra="forbid", strict=True)

    mode: Literal["selected_routes", "full_direct_school"] = "selected_routes"
    route_keys: list[str] = Field(default_factory=list, max_length=20)
    request_key: str = Field(min_length=1, max_length=80)
    provider_call_limit: int = Field(ge=1, le=500)
    confirm_provider_calls: bool

    @model_validator(mode="after")
    def check_scope(self):
        if self.mode == "selected_routes" and not self.route_keys:
            raise ValueError("Select the routes to remeasure.")
        if self.mode == "full_direct_school" and self.route_keys:
            raise ValueError("Full correction includes every saved route; do not supply a partial selection.")
        return self

    @field_validator("confirm_provider_calls")
    @classmethod
    def require_confirmation(cls, value: bool) -> bool:
        if value is not True:
            raise ValueError("Explicit approval of the bounded provider calls is required.")
        return value


def payload_to_dict(payload: BaseModel | dict[str, Any] | None) -> dict[str, Any]:
    if payload is None:
        return {}
    if isinstance(payload, BaseModel):
        return payload.model_dump(mode="python")
    if isinstance(payload, dict):
        return payload
    return {}
