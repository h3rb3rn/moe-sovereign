"""tests/test_rust_compile_sandbox.py — Unit tests for
services/rust_compile_sandbox/app.py::_parse_rustc_json.

Phase 1 of the Rust code-quality-in-the-loop feature had no test coverage at
all before this; added while building Phase 2 (rust_loom_check) alongside it.
Only pure, undecorated helper functions are testable this way -- the FastAPI
route handlers themselves are exercised by the container's own health check
and the live end-to-end verification (see docs/experiments/
lumig_posttraining_candidates.md), since tests/conftest.py stubs `fastapi`
as a MagicMock, which silently replaces any @app.post-decorated function.
"""

import importlib.util
import pathlib

import pytest

_MODULE_PATH = pathlib.Path(__file__).resolve().parents[1] / "services/rust_compile_sandbox/app.py"
_spec = importlib.util.spec_from_file_location("rust_compile_sandbox_app", _MODULE_PATH)
rust_compile_sandbox_app = importlib.util.module_from_spec(_spec)
_spec.loader.exec_module(rust_compile_sandbox_app)


class TestParseRustcJson:
    def test_parses_error_and_warning_lines(self):
        stderr = (
            '{"level":"error","message":"cannot borrow as mutable",'
            '"spans":[{"line_start":5,"column_start":9}]}\n'
            '{"level":"warning","message":"unused variable",'
            '"spans":[{"line_start":2,"column_start":1}]}\n'
        )
        diags = rust_compile_sandbox_app._parse_rustc_json(stderr)
        assert len(diags) == 2
        assert diags[0].level == "error"
        assert diags[0].line == 5
        assert diags[0].column == 9
        assert diags[1].level == "warning"

    def test_ignores_non_json_and_non_diagnostic_lines(self):
        stderr = (
            "warning: `foo` (lib) generated 1 warning\n"
            '{"level":"note","message":"see also"}\n'
            "not json at all {\n"
        )
        assert rust_compile_sandbox_app._parse_rustc_json(stderr) == []

    def test_handles_missing_spans(self):
        stderr = '{"level":"error","message":"E0308"}\n'
        diags = rust_compile_sandbox_app._parse_rustc_json(stderr)
        assert len(diags) == 1
        assert diags[0].line is None
        assert diags[0].column is None

    def test_empty_input_returns_empty_list(self):
        assert rust_compile_sandbox_app._parse_rustc_json("") == []
