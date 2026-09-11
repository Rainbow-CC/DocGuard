from pathlib import Path

import pytest

from docguard.domain.models import AgentBackend, AgentRouteDefinition
from docguard.services.runtime_routes import RuntimeRouteError, RuntimeRouteResolver


def _route(route_id: str = "technical-audit/content-reviewer") -> AgentRouteDefinition:
    return AgentRouteDefinition(
        route_id=route_id,
        version="1.0.0",
        agent_id="content-reviewer",
        agent_version="1.0.0",
    )


def test_runtime_route_defaults_to_openclaw_and_resolves_dsh_model() -> None:
    resolver = RuntimeRouteResolver.from_file(
        Path(__file__).parents[1] / "deploy" / "runtime-routes.json"
    )

    assert resolver.default_backend(_route()) is AgentBackend.OPENCLAW
    assert resolver.resolve(_route()).target_ref == "openclaw/audit-runtime"

    dsh = resolver.resolve(_route(), AgentBackend.DSH)
    assert dsh.provider == "minimax-cn"
    assert dsh.model == "MiniMax-M3"
    assert dsh.profile == "sdk"


def test_runtime_route_rejects_unknown_logical_route() -> None:
    resolver = RuntimeRouteResolver.from_file(
        Path(__file__).parents[1] / "deploy" / "runtime-routes.json"
    )

    with pytest.raises(RuntimeRouteError, match="Runtime route is not configured"):
        resolver.resolve(_route("technical-audit/unknown"))
