from __future__ import annotations

import json
import os
from collections.abc import Mapping
from typing import Any

from pydantic import BaseModel, ConfigDict, Field, field_validator

from opencollab.bootstrap.vm import VMConnection, VMResource, vm_isolation_provider


class VMProfile(BaseModel):
    model_config = ConfigDict(extra="forbid", hide_input_in_errors=True)

    resources: tuple[VMResource, ...] = Field(min_length=1)
    lease_database: str
    wait_timeout: float | None = Field(default=0, ge=0, allow_inf_nan=False)

    @field_validator("resources", mode="before")
    @classmethod
    def _resources(cls, value: Any) -> Any:
        if not isinstance(value, (list, tuple)):
            raise ValueError("VM resources must be a sequence")
        resources = []
        for item in value:
            if isinstance(item, Mapping):
                item = dict(item)
                connection = item.get("connection")
                try:
                    if isinstance(connection, Mapping):
                        item["connection"] = VMConnection(**connection)
                    item = VMResource(**item)
                except TypeError as exc:
                    raise ValueError(f"invalid VM resource configuration {exc}") from exc
            if not isinstance(item, VMResource):
                raise ValueError("VM resources must contain VMResource values or resource objects")
            resources.append(item)
        return tuple(resources)

    @field_validator("lease_database")
    @classmethod
    def _lease_database(cls, value: str) -> str:
        if not os.path.isabs(value) or "\x00" in value:
            raise ValueError("VM lease_database must be an absolute local path")
        return value

    @field_validator("wait_timeout", mode="before")
    @classmethod
    def _wait_timeout(cls, value: Any) -> Any:
        if isinstance(value, bool):
            raise ValueError("VM wait_timeout must be a non-negative number or None")
        return value


def configured_environment_providers(
    config: Mapping[str, Any], providers: Mapping[str, Any] | None = None,
) -> dict[str, Any]:
    selected: dict[str, Any] = {}
    value = config.get("vm")
    if value is not None:
        if isinstance(value, str):
            value = json.loads(value)
        profile = value if isinstance(value, VMProfile) else VMProfile.model_validate(value)
        selected.update({
            f"vm-{scope}": vm_isolation_provider(
                profile.resources, lease_database=profile.lease_database,
                scope=scope, wait_timeout=profile.wait_timeout,
            )
            for scope in ("group", "session")
        })
    selected.update(providers or {})
    return selected
