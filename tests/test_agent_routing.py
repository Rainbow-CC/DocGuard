import pytest

from docguard.adapters.agents import graph_gateway_for
from docguard.domain.models import AgentBackend


def test_graph_gateway_factory_rejects_artifact_delivered_openclaw() -> None:
    with pytest.raises(ValueError, match="OpenClaw is artifact-delivered"):
        graph_gateway_for(AgentBackend.OPENCLAW)
