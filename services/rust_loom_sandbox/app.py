"""services/rust_loom_sandbox/app.py — isolated Rust Loom concurrency-model
checker (rust_compile_check Phase 2).

Phase 1 (services/rust_compile_sandbox) proves Rust code *compiles*, but a
data race from incorrect memory ordering is not a compile error -- the
program compiles cleanly and only a real concurrency-model checker (Loom)
or sanitizer can catch it (see docs/experiments/lumig_posttraining_candidates.md).
Catching that requires actually *running* the submitted code under Loom's
interpreter, which Phase 1's sandbox deliberately never does. This is a
separate, more tightly hardened container for exactly that reason: a bug
in this new execution path must never be able to compromise the
compile-only checker that the rest of the system already depends on.

Compensating controls versus the compile-only sandbox (see docker-compose.yml
for the enforced half of these -- read_only, no egress, cap_drop, non-root,
resource limits are identical or stricter):
  - The scratch workdir tmpfs allows exec (unavoidable -- Loom must run the
    compiled test binary), unlike the compile-only sandbox's noexec tmpfs.
  - A conservative pre-filter rejects source referencing process/FFI/fs
    escape hatches before it ever reaches rustc. This is defense in depth
    only, never the primary control -- the sandbox isolation is.
  - CARGO_NET_OFFLINE=true plus --offline/--frozen: even if the egress-
    blocked network were somehow bypassed, Cargo itself refuses to fetch
    anything at request time; the `loom` dependency is vendored into the
    image at build time (see Dockerfile).
"""

import asyncio
import json
import logging
import os
import re
import shutil
import time
import uuid
from typing import Optional

from fastapi import FastAPI
from pydantic import BaseModel, Field

logger = logging.getLogger("rust-loom-sandbox")
logging.basicConfig(level=logging.INFO)

app = FastAPI(title="rust-loom-sandbox")

# Loom's state-space exploration is inherently more expensive than a plain
# compile -- serialize requests exactly like the compile-only sandbox, for
# the same host-RAM-headroom reason.
_RUN_SEMAPHORE = asyncio.Semaphore(1)

_RUN_TIMEOUT_S = float(os.environ.get("RUST_LOOM_TIMEOUT_S", "45.0"))
_MAX_SOURCE_BYTES = 200_000
_SCAFFOLD_DIR = "/app/scaffold"
_BUILD_ROOT = "/build"
_ALLOWED_EDITIONS = {"2021"}  # loom-scaffold Cargo.toml pins edition 2021

# Defense-in-depth only (see module docstring): reject source that reaches
# outside the process before it ever gets to rustc. The sandbox network/
# filesystem/capability isolation is the actual control.
_FORBIDDEN_PATTERNS = re.compile(
    r"\bstd::process\b|\bstd::fs::(?:remove|write|File::create)\b|"
    r'\bextern\s+"C"|#\[link\b|\binclude!\s*\(|\bstd::net\b'
)


class LoomCheckRequest(BaseModel):
    source: str = Field(..., description="Rust source for src/lib.rs, containing a #[test] fn that calls loom::model(...)")
    edition: str = Field("2021", description="Rust edition (only 2021 supported)")


class LoomCheckResponse(BaseModel):
    compiles: Optional[bool]
    passed: Optional[bool]
    output_tail: str
    duration_ms: int
    timed_out: bool = False


def _classify_output(returncode: int, combined: str) -> tuple[Optional[bool], Optional[bool]]:
    """Best-effort classification of `cargo test` output.

    Returns (compiles, passed). `passed is None` when the run never reached
    the test harness at all (a pure compile failure) -- that's a different
    failure mode than "compiled but Loom found a violation" and the caller
    should feed a different retry prompt for each.
    """
    reached_harness = "running " in combined and " test" in combined
    has_compile_error = re.search(r"^error(\[E\d+\])?:", combined, re.MULTILINE) is not None
    if has_compile_error and not reached_harness:
        return False, None
    if "test result: ok" in combined:
        return True, True
    if "test result: FAILED" in combined:
        return True, False
    # Reached neither a clean compile-error nor a recognizable test-result
    # line (e.g. killed by timeout before finishing) -- unknown, not a hard
    # false either way.
    return (True if reached_harness else None), None


@app.post("/loom-check", response_model=LoomCheckResponse)
async def loom_check(req: LoomCheckRequest) -> LoomCheckResponse:
    if req.edition not in _ALLOWED_EDITIONS:
        return LoomCheckResponse(
            compiles=False, passed=None,
            output_tail=f"unsupported edition {req.edition!r}", duration_ms=0,
        )
    if len(req.source.encode("utf-8", errors="replace")) > _MAX_SOURCE_BYTES:
        return LoomCheckResponse(
            compiles=False, passed=None,
            output_tail="source exceeds size limit", duration_ms=0,
        )
    if _FORBIDDEN_PATTERNS.search(req.source):
        return LoomCheckResponse(
            compiles=False, passed=None,
            output_tail="source rejected by pre-filter (process/FFI/fs/net escape pattern)",
            duration_ms=0,
        )

    os.makedirs(_BUILD_ROOT, exist_ok=True)
    workdir = os.path.join(_BUILD_ROOT, uuid.uuid4().hex)
    # Copy the pre-built scaffold (Cargo.toml/Cargo.lock + primed target/ and
    # vendored registry cache) so `cargo test --offline` only ever needs to
    # (re)compile the tiny user-submitted lib.rs, not the loom dependency
    # tree, keeping each request within the timeout.
    shutil.copytree(_SCAFFOLD_DIR, workdir)
    src_path = os.path.join(workdir, "src", "lib.rs")

    try:
        with open(src_path, "w", encoding="utf-8") as f:
            f.write(req.source)

        env = dict(os.environ)
        env["CARGO_NET_OFFLINE"] = "true"

        t0 = time.monotonic()
        async with _RUN_SEMAPHORE:
            proc = await asyncio.create_subprocess_exec(
                # --lib: unit tests only, skip doctests -- rustdoc's doctest
                # runner needs a writable /tmp, which this read-only-root
                # container doesn't provide, and there is no legitimate use
                # for doc-comment examples in submitted loom test source.
                "cargo", "test", "--lib", "--offline", "--frozen", "--release", "--", "--nocapture",
                stdout=asyncio.subprocess.PIPE,
                stderr=asyncio.subprocess.STDOUT,
                cwd=workdir,
                env=env,
            )
            try:
                stdout, _ = await asyncio.wait_for(proc.communicate(), timeout=_RUN_TIMEOUT_S)
            except asyncio.TimeoutError:
                proc.kill()
                await proc.wait()
                duration_ms = int((time.monotonic() - t0) * 1000)
                return LoomCheckResponse(
                    compiles=None, passed=None,
                    output_tail="cargo test timed out (possible unbounded Loom state-space exploration)",
                    duration_ms=duration_ms, timed_out=True,
                )
        duration_ms = int((time.monotonic() - t0) * 1000)
        combined = stdout.decode("utf-8", errors="replace")
        compiles, passed = _classify_output(proc.returncode, combined)
        return LoomCheckResponse(
            compiles=compiles, passed=passed,
            output_tail=combined[-4000:], duration_ms=duration_ms,
        )
    finally:
        shutil.rmtree(workdir, ignore_errors=True)


@app.get("/health")
async def health() -> dict:
    return {"status": "ok"}
