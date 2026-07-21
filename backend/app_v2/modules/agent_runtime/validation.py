"""Shared validation helpers for Agent unit runtime descriptors."""
from __future__ import annotations

from typing import Any


def state_key_by_node(graph: dict[str, Any]) -> dict[str, str]:
    """Infer the public state key produced by each node from graph.state_design."""
    design = graph.get("state_design") if isinstance(graph, dict) else {}
    if not isinstance(design, dict):
        return {}
    out: dict[str, str] = {}
    for key, meta in design.items():
        if not isinstance(meta, dict):
            continue
        producer = str(meta.get("producer") or "").strip()
        if producer and producer != "runtime" and producer not in out:
            out[producer] = str(key)
    return out


def validate_graph_descriptor(graph: dict[str, Any]) -> list[str]:
    warnings: list[str] = []
    nodes = graph.get("nodes") if isinstance(graph, dict) else None
    edges = graph.get("edges") if isinstance(graph, dict) else None
    if not isinstance(nodes, list):
        warnings.append("graph.nodes must be a list")
    if not isinstance(edges, list):
        warnings.append("graph.edges must be a list")
    node_ids = {str(node.get("id") or "") for node in nodes or [] if isinstance(node, dict)}
    for edge in edges or []:
        if not isinstance(edge, dict):
            continue
        if str(edge.get("source") or "") not in node_ids or str(edge.get("target") or "") not in node_ids:
            warnings.append("graph edge references an unknown node")
            break
    return warnings
