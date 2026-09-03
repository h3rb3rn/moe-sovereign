"""tests/test_rust_loom_sandbox.py — Unit tests for
services/rust_loom_sandbox/app.py::_classify_output and the forbidden-source
pre-filter.

Only pure, undecorated helper functions are testable this way -- see
tests/test_rust_compile_sandbox.py's module docstring for why the FastAPI
route handler itself (loom_check) isn't unit-testable under the stubbed
fastapi in tests/conftest.py. Subprocess timeout/kill behavior and the real
Loom-detected-violation case are covered by the container's live end-to-end
verification instead.
"""

import importlib.util
import pathlib

_MODULE_PATH = pathlib.Path(__file__).resolve().parents[1] / "services/rust_loom_sandbox/app.py"
_spec = importlib.util.spec_from_file_location("rust_loom_sandbox_app", _MODULE_PATH)
rust_loom_sandbox_app = importlib.util.module_from_spec(_spec)
_spec.loader.exec_module(rust_loom_sandbox_app)


class TestClassifyOutput:
    def test_clean_pass(self):
        combined = "running 1 test\ntest tests::test_it ... ok\n\ntest result: ok. 1 passed; 0 failed;\n"
        compiles, passed = rust_loom_sandbox_app._classify_output(0, combined)
        assert compiles is True
        assert passed is True

    def test_loom_detected_violation(self):
        combined = (
            "running 1 test\n"
            "thread 'tests::test_it' panicked at ...\n"
            "test tests::test_it ... FAILED\n\n"
            "test result: FAILED. 0 passed; 1 failed;\n"
        )
        compiles, passed = rust_loom_sandbox_app._classify_output(101, combined)
        assert compiles is True
        assert passed is False

    def test_pure_compile_error_never_reached_harness(self):
        combined = (
            "error[E0425]: cannot find value `x` in this scope\n"
            " --> src/lib.rs:3:5\n"
            "error: aborting due to 1 previous error\n"
        )
        compiles, passed = rust_loom_sandbox_app._classify_output(101, combined)
        assert compiles is False
        assert passed is None

    def test_ambiguous_output_without_recognizable_markers(self):
        compiles, passed = rust_loom_sandbox_app._classify_output(1, "some unrelated cargo warning\n")
        assert compiles is None
        assert passed is None


class TestForbiddenPatterns:
    def test_rejects_process_spawn(self):
        assert rust_loom_sandbox_app._FORBIDDEN_PATTERNS.search("std::process::Command::new(\"ls\")")

    def test_rejects_extern_c(self):
        assert rust_loom_sandbox_app._FORBIDDEN_PATTERNS.search('extern "C" { fn evil(); }')

    def test_rejects_net(self):
        assert rust_loom_sandbox_app._FORBIDDEN_PATTERNS.search("std::net::TcpStream::connect(\"x\")")

    def test_allows_ordinary_loom_test(self):
        source = (
            "use loom::sync::Arc;\n"
            "use loom::sync::atomic::AtomicUsize;\n"
            "use loom::sync::atomic::Ordering::SeqCst;\n"
            "use loom::thread;\n\n"
            "#[test]\n"
            "fn test_it() {\n"
            "    loom::model(|| {\n"
            "        let v = Arc::new(AtomicUsize::new(0));\n"
            "        let v2 = v.clone();\n"
            "        thread::spawn(move || { v2.store(1, SeqCst); });\n"
            "        v.load(SeqCst);\n"
            "    });\n"
            "}\n"
        )
        assert rust_loom_sandbox_app._FORBIDDEN_PATTERNS.search(source) is None
