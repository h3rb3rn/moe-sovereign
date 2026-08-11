"""Unit tests for the Microsoft GraphRAG-Style Hierarchical Community Clustering Engine."""

import pytest
from services.graphrag.graph_clustering import GraphCommunityClusterer


def test_compute_leiden_communities():
    """Verify graph nodes are partitioned into distinct connected community clusters."""
    clusterer = GraphCommunityClusterer()
    nodes = [
        {"id": "a1", "name": "Planner"},
        {"id": "a2", "name": "Z3"},
        {"id": "b1", "name": "Kafka"},
        {"id": "b2", "name": "Valkey"}
    ]
    edges = [
        {"source": "a1", "target": "a2"},
        {"source": "b1", "target": "b2"}
    ]
    clusters = clusterer.compute_leiden_communities(nodes, edges)
    
    assert len(clusters) == 4
    assert clusters["a1"] == clusters["a2"]
    assert clusters["b1"] == clusters["b2"]
    assert clusters["a1"] != clusters["b1"]


def test_format_community_meta_summary():
    """Verify meta-summary generation produces valid Cypher query and member count."""
    clusterer = GraphCommunityClusterer()
    nodes = [{"id": "n1", "name": "Planner"}, {"id": "n2", "name": "Z3"}]
    summary = clusterer.format_community_meta_summary(1, nodes)
    
    assert summary["community_id"] == "comm_0001"
    assert summary["member_count"] == 2
    assert "Community 1" in summary["meta_summary"]
    assert "MATCH (n)-[:BELONGS_TO]->(c:Community" in summary["cypher_query"]
