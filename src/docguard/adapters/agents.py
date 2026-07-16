from __future__ import annotations

from typing import Protocol

from docguard.domain.models import AgentBackend, AuditProfile, EvidenceRef, Finding


class AgentGateway(Protocol):
    backend: AgentBackend

    def audit_full_text(self, profile: AuditProfile, evidence: list[EvidenceRef]) -> list[Finding]: ...

    def audit_architecture(self, profile: AuditProfile, evidence: list[EvidenceRef]) -> list[Finding]: ...


class StubAgentGateway:
    """A deterministic local executor used until a real model adapter is configured."""

    backend = AgentBackend.STUB

    def audit_full_text(self, profile: AuditProfile, evidence: list[EvidenceRef]) -> list[Finding]:
        return []

    def audit_architecture(self, profile: AuditProfile, evidence: list[EvidenceRef]) -> list[Finding]:
        return []


class OpenClawAgentGateway:
    """Integration seam for an OpenClaw JSON-only audit skill or gateway endpoint."""

    backend = AgentBackend.OPENCLAW

    def audit_full_text(self, profile: AuditProfile, evidence: list[EvidenceRef]) -> list[Finding]:
        return self._not_configured("full_text")

    def audit_architecture(self, profile: AuditProfile, evidence: list[EvidenceRef]) -> list[Finding]:
        return self._not_configured("architecture")

    def _not_configured(self, audit_type: str) -> list[Finding]:
        raise RuntimeError(
            f"OpenClaw adapter is not configured for {audit_type}. "
            "Implement its transport with a JSON-only Finding[] response contract."
        )


class LangChainAgentGateway:
    """Integration seam for a LangChain structured-output runnable."""

    backend = AgentBackend.LANGCHAIN

    def audit_full_text(self, profile: AuditProfile, evidence: list[EvidenceRef]) -> list[Finding]:
        return self._not_configured("full_text")

    def audit_architecture(self, profile: AuditProfile, evidence: list[EvidenceRef]) -> list[Finding]:
        return self._not_configured("architecture")

    def _not_configured(self, audit_type: str) -> list[Finding]:
        raise RuntimeError(
            f"LangChain adapter is not configured for {audit_type}. "
            "Bind a model with structured output to the Finding[] contract."
        )


def gateway_for(backend: AgentBackend) -> AgentGateway:
    match backend:
        case AgentBackend.STUB:
            return StubAgentGateway()
        case AgentBackend.OPENCLAW:
            return OpenClawAgentGateway()
        case AgentBackend.LANGCHAIN:
            return LangChainAgentGateway()
