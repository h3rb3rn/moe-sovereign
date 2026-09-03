"""tests/pipeline/test_chat_diagnostic_metadata.py — Unit tests for
services/pipeline/chat.py::_build_diagnostic_metadata.

Added for the benchmark-harness self-critique-round metric (see
benchmarks/run_scientific_benchmark.py). The response metadata dict is also
populated by the "sources" and "candidate" keys elsewhere in
chat_completions() via resp.setdefault("metadata", {})[...] / .update(...) --
this pure function must never be assigned directly onto resp["metadata"],
only merged, so it can't clobber those.
"""

from services.pipeline.chat import _build_diagnostic_metadata


class TestBuildDiagnosticMetadata:
    def test_defaults_when_fields_absent(self):
        meta = _build_diagnostic_metadata({})
        assert meta == {
            "self_critique_round": 0,
            "trust_score": None,
            "trust_verdict": None,
        }

    def test_extracts_present_fields(self):
        meta = _build_diagnostic_metadata({
            "self_critique_round": 2,
            "trust_score": 0.87,
            "trust_verdict": "PROCEED_WITH_ASSUMPTION",
        })
        assert meta == {
            "self_critique_round": 2,
            "trust_score": 0.87,
            "trust_verdict": "PROCEED_WITH_ASSUMPTION",
        }

    def test_self_critique_round_coerced_to_int(self):
        assert _build_diagnostic_metadata({"self_critique_round": "3"})["self_critique_round"] == 3

    def test_falsy_trust_verdict_normalized_to_none(self):
        assert _build_diagnostic_metadata({"trust_verdict": ""})["trust_verdict"] is None

    def test_merge_does_not_clobber_sources_or_candidate(self):
        resp = {"metadata": {"sources": ["a", "b"], "candidate": {"status": "degraded"}}}
        result = {"self_critique_round": 1, "trust_score": 0.5, "trust_verdict": "PASS"}
        resp.setdefault("metadata", {}).update(_build_diagnostic_metadata(result))
        assert resp["metadata"]["sources"] == ["a", "b"]
        assert resp["metadata"]["candidate"] == {"status": "degraded"}
        assert resp["metadata"]["self_critique_round"] == 1
        assert resp["metadata"]["trust_score"] == 0.5
        assert resp["metadata"]["trust_verdict"] == "PASS"
