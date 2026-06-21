from __future__ import annotations

from typing import Any

from pydantic import BaseModel, ConfigDict, Field


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


def payload_to_dict(payload: BaseModel | dict[str, Any] | None) -> dict[str, Any]:
    if payload is None:
        return {}
    if isinstance(payload, BaseModel):
        return payload.model_dump(mode="python")
    if isinstance(payload, dict):
        return payload
    return {}
