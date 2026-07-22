from __future__ import annotations

from typing import TypedDict

from langgraph.graph import END, START, StateGraph

from docguard.adapters.agents import GraphAuditGateway
from docguard.domain.models import AuditTask, Finding
from docguard.services.reporting import render_markdown


class AuditState(TypedDict, total=False):
    task: AuditTask
    text_findings: list[Finding]
    architecture_findings: list[Finding]
    findings: list[Finding]
    report_markdown: str


def _merge_findings(state: AuditState) -> dict:
    # Deterministic root-cause de-duplication: first finding wins for a root-cause key.
    merged: dict[str, Finding] = {}
    for finding in state.get("text_findings", []) + state.get("architecture_findings", []):
        merged.setdefault(finding.root_cause_key, finding)
    return {"findings": list(merged.values())}


def build_audit_graph(gateway: GraphAuditGateway):
    graph = StateGraph(AuditState)
    graph.add_node(
        "full_text_audit",
        lambda state: {"text_findings": gateway.audit_full_text(state["task"].profile)},
    )
    graph.add_node("merge", _merge_findings)
    graph.add_node(
        "render",
        lambda state: {"report_markdown": render_markdown(state["task"].profile, state["findings"])},
    )
    graph.add_edge(START, "full_text_audit")
    graph.add_edge("full_text_audit", "merge")
    graph.add_edge("merge", "render")
    graph.add_edge("render", END)
    return graph.compile()
