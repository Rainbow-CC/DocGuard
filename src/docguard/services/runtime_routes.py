"""Resolve database Agent routes through deployment-owned runtime configuration."""

from __future__ import annotations

import json
from pathlib import Path

from pydantic import BaseModel, ConfigDict

from docguard.domain.models import AgentBackend, AgentRouteDefinition, AgentRuntimeBinding


class RuntimeTarget(BaseModel):
    model_config = ConfigDict(extra="forbid")

    target_ref: str
    provider: str | None = None
    model: str | None = None
    profile: str | None = None


class RuntimeRoute(BaseModel):
    model_config = ConfigDict(extra="forbid")

    default_backend: AgentBackend
    backends: dict[AgentBackend, RuntimeTarget]


class RuntimeRoutesConfig(BaseModel):
    model_config = ConfigDict(extra="forbid")

    version: str
    routes: dict[str, RuntimeRoute]


class RuntimeRouteError(ValueError):
    pass


class RuntimeRouteResolver:
    def __init__(self, config: RuntimeRoutesConfig) -> None:
        self.config = config

    @classmethod
    def from_file(cls, path: Path | str) -> "RuntimeRouteResolver":
        config_path = Path(path)
        try:
            payload = json.loads(config_path.read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError) as exc:
            raise RuntimeRouteError(f"Unable to load runtime routes from {config_path}: {exc}") from exc
        return cls(RuntimeRoutesConfig.model_validate(payload))

    def default_backend(self, route: AgentRouteDefinition) -> AgentBackend:
        return self._route(route).default_backend

    def resolve(
        self, route: AgentRouteDefinition, backend: AgentBackend | None = None
    ) -> AgentRuntimeBinding:
        configured = self._route(route)
        selected = backend or configured.default_backend
        try:
            target = configured.backends[selected]
        except KeyError:
            raise RuntimeRouteError(
                f"Route {route.route_id} has no {selected.value} runtime target"
            ) from None
        return AgentRuntimeBinding(
            route_id=route.route_id,
            route_version=route.version,
            route_config_version=self.config.version,
            backend=selected,
            target_ref=target.target_ref,
            provider=target.provider,
            model=target.model,
            profile=target.profile,
        )

    def _route(self, route: AgentRouteDefinition) -> RuntimeRoute:
        try:
            return self.config.routes[route.route_id]
        except KeyError:
            raise RuntimeRouteError(f"Runtime route is not configured: {route.route_id}") from None
