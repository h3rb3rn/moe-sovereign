"""services/rust_compile_sandbox/app.py — isolated Rust compile-only check.

Runs `rustc --emit=metadata` (type-check + borrow-check, no codegen, no
linker) against untrusted, model-generated source in a network-isolated,
read-only container. Never executes the checked code. See
docs/experiments/graphrag_efficacy_ringbuffer.md and
docs/experiments/lumig_posttraining_candidates.md for the motivating
evidence (recurring compiler-catchable defects in LLM-generated Rust).
"""

import asyncio
import json
import logging
import os
import shutil
import tempfile
import uuid
from typing import Any, Optional

from fastapi import FastAPI
from pydantic import BaseModel, Field

logger = logging.getLogger("rust-compile-sandbox")
logging.basicConfig(level=logging.INFO)

app = FastAPI(title="rust-compile-sandbox")

# Single concurrent rustc process at a time: the container's own cgroup
# memory/cpu limits bound one invocation, but the host this runs on has
# historically had very little free RAM headroom -- serializing prevents
# N simultaneous compiles from jointly exceeding the container limit's
# intent even though each individually stays under it.
_COMPILE_SEMAPHORE = asyncio.Semaphore(1)

_COMPILE_TIMEOUT_S = 10.0
_MAX_SOURCE_BYTES = 200_000
_SCRATCH_ROOT = "/tmp/rust-compile-sandbox"
_ALLOWED_EDITIONS = {"2015", "2018", "2021", "2024"}


class CompileCheckRequest(BaseModel):
    source: str = Field(..., description="Rust source to type/borrow-check")
    edition: str = Field("2021", description="Rust edition")


class Diagnostic(BaseModel):
    level: str
    message: str
    line: Optional[int] = None
    column: Optional[int] = None


class CompileCheckResponse(BaseModel):
    compiles: bool
    diagnostics: list[Diagnostic]
    duration_ms: int
    timed_out: bool = False


def _parse_rustc_json(stderr_text: str) -> list[Diagnostic]:
    """Parse rustc --error-format=json output (one JSON object per line)."""
    diagnostics: list[Diagnostic] = []
    for line in stderr_text.splitlines():
        line = line.strip()
        if not line or not line.startswith("{"):
            continue
        try:
            obj: dict[str, Any] = json.loads(line)
        except json.JSONDecodeError:
            continue
        level = obj.get("level")
        if level not in ("error", "warning"):
            continue
        message = obj.get("message") or ""
        span = (obj.get("spans") or [None])[0]
        line_no = span.get("line_start") if span else None
        col_no = span.get("column_start") if span else None
        diagnostics.append(
            Diagnostic(level=level, message=message, line=line_no, column=col_no)
        )
    return diagnostics


@app.post("/compile-check", response_model=CompileCheckResponse)
async def compile_check(req: CompileCheckRequest) -> CompileCheckResponse:
    if req.edition not in _ALLOWED_EDITIONS:
        return CompileCheckResponse(
            compiles=False,
            diagnostics=[Diagnostic(level="error", message=f"unsupported edition {req.edition!r}")],
            duration_ms=0,
        )
    if len(req.source.encode("utf-8", errors="replace")) > _MAX_SOURCE_BYTES:
        return CompileCheckResponse(
            compiles=False,
            diagnostics=[Diagnostic(level="error", message="source exceeds size limit")],
            duration_ms=0,
        )

    os.makedirs(_SCRATCH_ROOT, exist_ok=True)
    workdir = os.path.join(_SCRATCH_ROOT, uuid.uuid4().hex)
    os.makedirs(workdir, mode=0o700)
    src_path = os.path.join(workdir, "check.rs")

    try:
        with open(src_path, "w", encoding="utf-8") as f:
            f.write(req.source)

        loop = asyncio.get_event_loop()
        t0 = loop.time()
        async with _COMPILE_SEMAPHORE:
            # -o must point at a writable path inside our own workdir, not
            # /dev/null: rustc derives its internal temp-file directory from
            # the output path's parent, and /dev is neither writable by the
            # non-root user nor intended for that -- observed live as
            # "couldn't create a temp dir ... Permission denied" even for
            # otherwise-valid source. The .rmeta output is discarded with
            # the rest of workdir in the `finally` block below.
            out_path = os.path.join(workdir, "out.rmeta")
            proc = await asyncio.create_subprocess_exec(
                "rustc",
                "--edition", req.edition,
                "--crate-type", "lib",
                "--emit=metadata",
                "-o", out_path,
                "--error-format=json",
                src_path,
                stdout=asyncio.subprocess.PIPE,
                stderr=asyncio.subprocess.PIPE,
                cwd=workdir,
            )
            try:
                _, stderr = await asyncio.wait_for(
                    proc.communicate(), timeout=_COMPILE_TIMEOUT_S
                )
            except asyncio.TimeoutError:
                proc.kill()
                await proc.wait()
                duration_ms = int((loop.time() - t0) * 1000)
                return CompileCheckResponse(
                    compiles=False,
                    diagnostics=[Diagnostic(level="error", message="rustc timed out")],
                    duration_ms=duration_ms,
                    timed_out=True,
                )
        duration_ms = int((loop.time() - t0) * 1000)
        diagnostics = _parse_rustc_json(stderr.decode("utf-8", errors="replace"))
        compiles = proc.returncode == 0
        return CompileCheckResponse(
            compiles=compiles, diagnostics=diagnostics, duration_ms=duration_ms
        )
    finally:
        shutil.rmtree(workdir, ignore_errors=True)


@app.get("/health")
async def health() -> dict:
    return {"status": "ok"}
