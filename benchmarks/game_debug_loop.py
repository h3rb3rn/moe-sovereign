#!/usr/bin/env python3
"""MoE Sovereign — Autonomous Game Debug Loop.

Generates a Mario-style HTML5 Canvas game via MoE API, then iteratively
tests and debugs it using Playwright until all tests pass or max epochs reached.

Each epoch:
  1. Send prompt to MoE API (generation or fix request)
  2. Extract HTML from response
  3. Deploy to Docker container
  4. Run Playwright tests
  5. Log everything (prompt, response, HTML, tests, screenshot)
  6. If all tests pass → exit early (success)
  7. If tests fail → build fix prompt with error history → next epoch

Usage:
    MOE_API_KEY=moe-sk-... python3 game_debug_loop.py [--max-epochs 10] [--template cc-expert-70b-deep]
"""
import argparse
import asyncio
import json
import os
import re
import subprocess
import sys
import time
import uuid
from dataclasses import dataclass, field, asdict
from datetime import datetime, timezone
from pathlib import Path
from typing import Optional

import httpx

# Import Playwright tests from sibling module
sys.path.insert(0, str(Path(__file__).parent))
from game_tests import run_all_tests


# ─── Configuration ────────────────────────────────────────────────────────────

MOE_API_BASE = os.getenv("MOE_API_BASE", "http://localhost:8002")
MOE_API_KEY = os.getenv("MOE_API_KEY", "")
MOE_TEMPLATE = os.getenv("MOE_TEMPLATE", "cc-expert-70b-deep")
MAX_EPOCHS = int(os.getenv("MAX_EPOCHS", "10"))
GAME_PORT = int(os.getenv("GAME_PORT", "42010"))
GAME_CONTAINER = os.getenv("GAME_CONTAINER", "moe-mario-game")
REQUEST_TIMEOUT = int(os.getenv("REQUEST_TIMEOUT", "3600"))  # 60 min
MAX_RETRIES = int(os.getenv("MAX_RETRIES", "3"))
RETRY_BACKOFF_BASE = int(os.getenv("RETRY_BACKOFF_BASE", "30"))  # seconds

RESULTS_BASE = Path(__file__).parent / "results" / "game_debug"


# ─── Data Classes ─────────────────────────────────────────────────────────────

@dataclass
class EpochResult:
    epoch: int
    prompt_type: str  # "generation" or "fix"
    prompt_text: str
    response_text: str
    html_code: str
    html_lines: int
    response_time_s: float
    prompt_tokens: int
    completion_tokens: int
    test_results: dict
    tests_passed: int
    tests_total: int
    all_passed: bool
    screenshot_path: str
    timestamp: str
    errors_found: list = field(default_factory=list)


@dataclass
class RunResult:
    template: str
    session_id: str
    max_epochs: int
    total_epochs: int
    success: bool
    total_time_s: float
    epochs: list = field(default_factory=list)


# ─── Prompts ──────────────────────────────────────────────────────────────────

GENERATION_PROMPT = """Create a complete Mario-style Jump & Run browser game as a SINGLE HTML file.

MANDATORY REQUIREMENTS:
1. Single HTML file with inline CSS and JavaScript. Zero external dependencies.
2. HTML5 Canvas-based rendering using requestAnimationFrame game loop.
3. Player controls: ArrowLeft/ArrowRight (or A/D) for movement, Space/ArrowUp/W for jump, ArrowDown/S for duck.
4. Game entities: enemies with patrol AI, collectible coins, bonus boxes (hit from below), green pipes as obstacles, platforms with gaps the player can fall through.
5. Score system displayed on screen, 3 lives, 3 levels with increasing difficulty and level progression when reaching the end.
6. Title/start screen with a visible Start button: <button id="startBtn">Start</button>
7. After the game starts, expose these global variables: window.gameScore (number), window.gameLives (number), window.gameLevel (number).
8. ZERO JavaScript errors. No duplicate const/let/var declarations. No redefined functions. No unreachable code.
9. All game entities must render as clearly visible colored shapes on the canvas (not transparent, not zero-size).
10. Canvas should be at least 800x400 pixels.
11. The game must be COMPLETE and PLAYABLE. Every single line of code must be present.

CRITICAL: Output the COMPLETE HTML file from <!DOCTYPE html> to </html>. Never truncate. Never use comments like "// rest of code unchanged" or "// ... continues". Every line must be written out."""


def _extract_script_block(html: str) -> str:
    """Extract the main <script> block from HTML to reduce prompt size."""
    m = re.search(r"<script[^>]*>(.*?)</script>", html, re.DOTALL)
    return m.group(1).strip() if m else html


def _diagnose_failures(test_results: dict) -> str:
    """Generate targeted diagnosis hints based on test failure patterns."""
    failing = {k for k, v in test_results.items() if not v["passed"]}
    hints = []

    if "canvas_renders" in failing:
        hints.append(
            "CANVAS NOT RENDERING: The game loop likely does not start after the Start button click. "
            "Common causes: (1) requestAnimationFrame is called before canvas context is ready, "
            "(2) the game loop function draws to a different canvas reference than the visible one, "
            "(3) the Start button click handler does not call the game loop, "
            "(4) clearRect clears the canvas but nothing is drawn afterward. "
            "VERIFY: the startBtn onclick handler calls a function that invokes requestAnimationFrame "
            "with a draw function that actually paints to the canvas context."
        )

    if "game_state" in failing:
        hints.append(
            "GAME STATE NOT EXPOSED: The test checks window.gameScore, window.gameLives, window.gameLevel. "
            "These MUST be set as global variables (not inside a function scope or class). "
            "Add at the top level: window.gameScore = 0; window.gameLives = 3; window.gameLevel = 1; "
            "and update them in the game loop."
        )

    if "keyboard_input" in failing and "canvas_renders" not in failing:
        hints.append(
            "KEYBOARD INPUT NOT WORKING: The test presses ArrowRight 10 times and checks if canvas "
            "pixels changed. Ensure: (1) keydown event listener is on document/window (not canvas), "
            "(2) the keys object is checked inside the game loop, (3) player.x actually changes."
        )

    return "\n\n".join(hints)


def build_fix_prompt(html: str, test_results: dict, epoch: int, error_history: list) -> str:
    """Build a compact debug prompt — sends only the script block, not full HTML."""
    failing = {k: v for k, v in test_results.items() if not v["passed"]}
    passing = {k: v for k, v in test_results.items() if v["passed"]}
    diagnosis = _diagnose_failures(test_results)

    history_section = ""
    if error_history:
        history_section = "\n\n=== PREVIOUS FIX ATTEMPTS (DO NOT REPEAT THESE) ===\n"
        for h in error_history[-3:]:
            history_section += f"\nEpoch {h['epoch']}: {', '.join(h['errors'])}. Result: {h['outcome']}\n"

    # Send only the script block to stay within context limits
    script = _extract_script_block(html)
    # Also extract the HTML structure (without script) for reference
    html_structure = re.sub(r"<script[^>]*>.*?</script>", "<script>/* SEE BELOW */</script>", html, flags=re.DOTALL)
    # Truncate HTML structure to first 2000 chars
    if len(html_structure) > 2000:
        html_structure = html_structure[:2000] + "\n<!-- ... truncated ... -->"

    return f"""Debug and fix this HTML5 Canvas game. {len(failing)} of {len(failing) + len(passing)} tests fail.

CRITICAL RULE: Make MINIMAL, TARGETED changes to fix ONLY the failing tests.
Do NOT redesign the game. Do NOT change the visual style, colors, layout, gameplay mechanics,
level design, entity behavior, or any other aspect that is already working.
Think like a debugger: find the specific bug, fix it, leave everything else untouched.

=== FAILING TESTS ===
{chr(10).join(f"- {name}: {info['detail']}" for name, info in failing.items())}

=== PASSING TESTS (DO NOT BREAK THESE — do not modify code related to these) ===
{chr(10).join(f"- {name}: {info['detail']}" for name, info in passing.items())}

=== DIAGNOSIS ===
{diagnosis}
{history_section}
=== HTML STRUCTURE (abbreviated — keep this structure intact) ===
```html
{html_structure}
```

=== JAVASCRIPT CODE (find and fix ONLY the bugs) ===
```javascript
{script}
```

INSTRUCTIONS:
1. Identify the SPECIFIC lines causing test failures.
2. Fix ONLY those lines — do not rewrite surrounding code.
3. Keep the visual design, colors, sprites, level layout, and gameplay IDENTICAL.
4. Output the COMPLETE fixed HTML file from <!DOCTYPE html> to </html>.
5. Do NOT truncate. Every line must be present.
6. The output should be 95%+ identical to the input — only the buggy parts changed.

Output ONLY the complete HTML file wrapped in ```html fences."""


# ─── API Communication ────────────────────────────────────────────────────────

async def call_moe_api(
    client: httpx.AsyncClient,
    messages: list,
    session_id: str,
) -> dict:
    """Send request to MoE API. Returns {response, usage, duration}."""
    headers = {
        "Authorization": f"Bearer {MOE_API_KEY}",
        "Content-Type": "application/json",
        "X-Session-ID": session_id,
    }
    payload = {
        "model": MOE_TEMPLATE,
        "messages": messages,
        "max_tokens": 16384,
        "temperature": 0.3,
        "stream": False,
    }

    prompt_chars = sum(len(m.get("content", "")) for m in messages)
    print(f"    ├─ Prompt: {prompt_chars:,} chars, max_tokens={payload['max_tokens']}")
    print(f"    ├─ Model: {MOE_TEMPLATE}, Session: {session_id[:20]}...")
    print(f"    ├─ Timeout: {REQUEST_TIMEOUT}s — waiting for response...", flush=True)

    t0 = time.time()
    # Print elapsed time every 60s while waiting
    last_tick = t0
    try:
        resp = await client.post(
            f"{MOE_API_BASE}/v1/chat/completions",
            json=payload,
            headers=headers,
            timeout=REQUEST_TIMEOUT,
        )
        dt = time.time() - t0

        if resp.status_code != 200:
            error_text = resp.text[:500]
            print(f"    └─ HTTP {resp.status_code} after {dt:.0f}s: {error_text[:200]}")
            return {"error": f"HTTP {resp.status_code}: {error_text}", "duration": dt}

        data = resp.json()
        content = data.get("choices", [{}])[0].get("message", {}).get("content", "")
        usage = data.get("usage", {})

        pt = usage.get("prompt_tokens", 0)
        ct = usage.get("completion_tokens", 0)
        print(f"    └─ OK in {dt:.0f}s — {pt:,} prompt tokens, {ct:,} completion tokens, {len(content):,} chars response")
        return {
            "response": content,
            "prompt_tokens": pt,
            "completion_tokens": ct,
            "duration": dt,
        }
    except httpx.TimeoutException:
        dt = time.time() - t0
        print(f"    └─ TIMEOUT after {dt:.0f}s")
        return {"error": f"Timeout after {REQUEST_TIMEOUT}s", "duration": dt}
    except Exception as e:
        dt = time.time() - t0
        print(f"    └─ EXCEPTION after {dt:.0f}s: {e}")
        return {"error": str(e), "duration": dt}


# ─── HTML Extraction ──────────────────────────────────────────────────────────

def extract_html(response: str) -> Optional[str]:
    """Extract complete HTML from MoE response."""
    # Strategy 1: ```html ... ``` code fence
    m = re.search(r"```html\s*\n(.*?)```", response, re.DOTALL)
    if m:
        html = m.group(1).strip()
        if "<canvas" in html.lower() and "<script" in html.lower():
            return html

    # Strategy 2: <!DOCTYPE html> ... </html>
    m = re.search(r"(<!DOCTYPE html>.*?</html>)", response, re.DOTALL | re.IGNORECASE)
    if m:
        html = m.group(1).strip()
        if "<canvas" in html.lower():
            return html

    # Strategy 3: <html> ... </html>
    m = re.search(r"(<html[^>]*>.*?</html>)", response, re.DOTALL | re.IGNORECASE)
    if m:
        html = m.group(1).strip()
        if "<canvas" in html.lower():
            return html

    return None


# ─── Docker Deployment ────────────────────────────────────────────────────────

def deploy_game(html: str, results_dir: Path) -> bool:
    """Deploy HTML to the Docker nginx container."""
    html_path = results_dir / "current_game.html"
    html_path.write_text(html, encoding="utf-8")

    try:
        # Copy to container
        subprocess.run(
            ["sudo", "docker", "cp", str(html_path),
             f"{GAME_CONTAINER}:/usr/share/nginx/html/index.html"],
            check=True, capture_output=True, timeout=10,
        )
        # Reload nginx
        subprocess.run(
            ["sudo", "docker", "exec", GAME_CONTAINER, "nginx", "-s", "reload"],
            check=True, capture_output=True, timeout=10,
        )
        time.sleep(2)

        # Verify
        resp = httpx.get(f"http://localhost:{GAME_PORT}", timeout=10)
        return resp.status_code == 200
    except Exception as e:
        print(f"  [!] Deploy failed: {e}")
        return False


# ─── Main Loop ────────────────────────────────────────────────────────────────

async def run_loop(max_epochs: int, results_dir: Path, screenshot_dir: Path,
                   initial_html: str = "") -> RunResult:
    """Main debug loop: generate → test → fix → repeat."""
    session_id = f"game-debug-{uuid.uuid4().hex[:12]}"
    run_result = RunResult(
        template=MOE_TEMPLATE,
        session_id=session_id,
        max_epochs=max_epochs,
        total_epochs=0,
        success=False,
        total_time_s=0,
    )

    messages = []
    error_history = []
    current_html = initial_html
    last_test_results = {}
    t_start = time.time()

    # If initial HTML provided, run baseline test first (epoch 0)
    if current_html:
        print(f"\n{'='*70}")
        print(f"  BASELINE TEST (provided HTML, {len(current_html):,} chars)")
        print(f"{'='*70}")

        # Deploy provided HTML
        print(f"  Deploying to {GAME_CONTAINER}:{GAME_PORT}...")
        deployed = await asyncio.to_thread(deploy_game, current_html, results_dir)
        if deployed:
            print("  Running Playwright tests...")
            test_results = await asyncio.to_thread(
                run_all_tests, port=GAME_PORT, screenshot_dir=screenshot_dir, epoch=0,
            )
            last_test_results = test_results
            passed = sum(1 for r in test_results.values() if r["passed"])
            total = len(test_results)

            print(f"\n  Baseline Results: {passed}/{total}")
            for name, r in test_results.items():
                status = "PASS" if r["passed"] else "FAIL"
                print(f"    [{status}] {name:20s} — {r['detail'][:70]}")

            (results_dir / "epoch_0_game.html").write_text(current_html, encoding="utf-8")
            (results_dir / "epoch_0_tests.json").write_text(
                json.dumps(test_results, indent=2), encoding="utf-8"
            )

            if passed == total:
                print(f"\n  *** ALL TESTS PASSED — no debugging needed! ***")
                run_result.success = True
                run_result.total_time_s = time.time() - t_start
                (results_dir / "run_result.json").write_text(
                    json.dumps(asdict(run_result), indent=2, default=str), encoding="utf-8"
                )
                return run_result
        else:
            print("  [ERROR] Deployment failed")

    client = httpx.AsyncClient()
    try:
      for epoch in range(1, max_epochs + 1):
            print(f"\n{'='*70}")
            print(f"  EPOCH {epoch}/{max_epochs}")
            print(f"{'='*70}")

            # Strategy: generate once → fix iteratively → only regenerate on total failure
            # Count consecutive API errors (not test failures — those are progress)
            api_failures = sum(
                1 for h in error_history[-3:]
                if h.get("outcome", "").startswith("API_ERROR")
            )
            # Only count zero-test-pass epochs as "no progress"
            zero_pass_streak = 0
            for h in reversed(error_history):
                if h.get("tests_passed", 0) == 0 and not h.get("outcome", "").startswith("API_ERROR"):
                    zero_pass_streak += 1
                else:
                    break

            print(f"  Strategy check: zero_pass_streak={zero_pass_streak}, api_failures={api_failures}, has_html={bool(current_html)}")

            # Regenerate ONLY if: no HTML yet, or 3+ consecutive API errors,
            # or 4+ epochs with zero tests passing (complete dead end)
            if not current_html or api_failures >= 3 or zero_pass_streak >= 4:
                # (Re)generate from scratch, but include learned constraints
                prompt_type = "generation"
                constraints = ""
                if error_history:
                    known_bugs = set()
                    for h in error_history:
                        known_bugs.update(h.get("errors", []))
                    if known_bugs:
                        constraints = (
                            "\n\nIMPORTANT — Previous attempts had these bugs. AVOID THEM:\n"
                            + "\n".join(f"- {b}" for b in known_bugs)
                            + "\n\nEnsure: (1) requestAnimationFrame loop starts on button click, "
                            "(2) window.gameScore/gameLives/gameLevel are set as top-level globals, "
                            "(3) keydown listeners are on document, not canvas."
                        )
                prompt_text = GENERATION_PROMPT + constraints
                messages = [{"role": "user", "content": prompt_text}]
                # Reset session to avoid stale context
                session_id = f"game-debug-{uuid.uuid4().hex[:12]}"
                current_html = ""
                if api_failures >= 3 or zero_pass_streak >= 4:
                    print(f"  [STRATEGY] Regenerating from scratch (constraints from {len(error_history)} prior epochs)")
            else:
                prompt_type = "fix"
                prompt_text = build_fix_prompt(
                    current_html, last_test_results, epoch, error_history
                )
                messages = [{"role": "user", "content": prompt_text}]

            # Save prompt
            (results_dir / f"epoch_{epoch}_prompt.txt").write_text(prompt_text, encoding="utf-8")
            prompt_lines = prompt_text.count('\n') + 1
            print(f"  [{prompt_type.upper()}] Prompt: {len(prompt_text):,} chars, {prompt_lines} lines")
            if prompt_type == "fix":
                script_size = len(_extract_script_block(current_html))
                print(f"    ├─ HTML: {len(current_html):,} chars total, Script block: {script_size:,} chars")
                failing_names = [k for k, v in last_test_results.items() if not v["passed"]]
                print(f"    ├─ Fixing: {', '.join(failing_names)}")
            print(f"  Sending to MoE API ({MOE_TEMPLATE})...", flush=True)

            # Call API with retry + backoff
            api_result = None
            for attempt in range(1, MAX_RETRIES + 1):
                api_result = await call_moe_api(client, messages, session_id)
                if "error" not in api_result:
                    break
                print(f"  [ERROR] Attempt {attempt}/{MAX_RETRIES}: {api_result['error']}")
                if attempt < MAX_RETRIES:
                    wait = RETRY_BACKOFF_BASE * (2 ** (attempt - 1))
                    print(f"  Waiting {wait}s before retry (backoff)...")
                    await asyncio.sleep(wait)
                    # Recreate client in case connection pool is stale
                    await client.aclose()
                    client = httpx.AsyncClient()

            if "error" in api_result:
                print(f"  [ERROR] All {MAX_RETRIES} retries exhausted: {api_result['error']}")
                # Reset session ID to avoid stale server-side state
                session_id = f"game-debug-{uuid.uuid4().hex[:12]}"
                print(f"  New session ID: {session_id}")
                (results_dir / f"epoch_{epoch}_error.txt").write_text(
                    api_result["error"], encoding="utf-8"
                )
                epoch_result = EpochResult(
                    epoch=epoch, prompt_type=prompt_type, prompt_text=prompt_text[:200],
                    response_text=api_result.get("error", ""), html_code="", html_lines=0,
                    response_time_s=api_result["duration"], prompt_tokens=0,
                    completion_tokens=0, test_results={}, tests_passed=0,
                    tests_total=0, all_passed=False, screenshot_path="",
                    timestamp=datetime.now(timezone.utc).isoformat(),
                    errors_found=["API_ERROR: " + api_result["error"][:100]],
                )
                run_result.epochs.append(asdict(epoch_result))
                run_result.total_epochs = epoch
                error_history.append({
                    "epoch": epoch,
                    "errors": ["API_ERROR"],
                    "tests_passed": 0,
                    "outcome": "API_ERROR: " + api_result["error"][:100],
                })
                continue

            response_text = api_result["response"]
            dt = api_result["duration"]

            # Save response
            (results_dir / f"epoch_{epoch}_response.txt").write_text(
                response_text, encoding="utf-8"
            )

            # Extract HTML
            print(f"  Extracting HTML from {len(response_text):,} char response...")
            html = extract_html(response_text)
            if not html:
                # Show what we got to help debug
                preview = response_text[:300].replace('\n', ' ')
                print(f"  [ERROR] Could not extract valid HTML from response")
                print(f"    Preview: {preview}...")
                epoch_result = EpochResult(
                    epoch=epoch, prompt_type=prompt_type, prompt_text=prompt_text[:200],
                    response_text=response_text[:500], html_code="", html_lines=0,
                    response_time_s=dt,
                    prompt_tokens=api_result.get("prompt_tokens", 0),
                    completion_tokens=api_result.get("completion_tokens", 0),
                    test_results={}, tests_passed=0, tests_total=0, all_passed=False,
                    screenshot_path="",
                    timestamp=datetime.now(timezone.utc).isoformat(),
                    errors_found=["HTML_EXTRACTION_FAILED"],
                )
                run_result.epochs.append(asdict(epoch_result))
                run_result.total_epochs = epoch
                error_history.append({
                    "epoch": epoch,
                    "errors": ["HTML_EXTRACTION_FAILED"],
                    "outcome": "Could not extract valid HTML — response may be truncated or wrapped incorrectly",
                })
                continue

            current_html = html
            html_lines = len(html.splitlines())
            (results_dir / f"epoch_{epoch}_game.html").write_text(html, encoding="utf-8")
            has_canvas = "<canvas" in html.lower()
            has_script = "<script" in html.lower()
            has_startbtn = "startBtn" in html or "startButton" in html
            has_raf = "requestAnimationFrame" in html
            has_gamestate = "gameScore" in html or "window.gameScore" in html
            print(f"  HTML extracted: {html_lines} lines, {len(html):,} chars")
            print(f"    ├─ <canvas>: {'yes' if has_canvas else 'NO!'}")
            print(f"    ├─ <script>: {'yes' if has_script else 'NO!'}")
            print(f"    ├─ Start button: {'yes' if has_startbtn else 'NO!'}")
            print(f"    ├─ requestAnimationFrame: {'yes' if has_raf else 'NO!'}")
            print(f"    └─ gameScore/gameLives/gameLevel: {'yes' if has_gamestate else 'NO!'}")

            # Deploy (in thread — uses subprocess)
            print(f"  Deploying to {GAME_CONTAINER}:{GAME_PORT}...")
            deployed = await asyncio.to_thread(deploy_game, html, results_dir)
            if not deployed:
                print("  [ERROR] Deployment failed")
                continue

            # Run tests (in thread — Playwright sync API cannot run inside asyncio loop)
            print("  Running Playwright tests...")
            test_results = await asyncio.to_thread(
                run_all_tests,
                port=GAME_PORT,
                screenshot_dir=screenshot_dir,
                epoch=epoch,
            )
            last_test_results = test_results

            passed = sum(1 for r in test_results.values() if r["passed"])
            total = len(test_results)
            all_passed = passed == total

            # Save test results
            (results_dir / f"epoch_{epoch}_tests.json").write_text(
                json.dumps(test_results, indent=2), encoding="utf-8"
            )

            # Print results
            print(f"\n  Test Results: {passed}/{total}")
            for name, r in test_results.items():
                status = "PASS" if r["passed"] else "FAIL"
                print(f"    [{status}] {name:20s} — {r['detail'][:70]}")

            # Build epoch result
            failing_tests = [k for k, v in test_results.items() if not v["passed"]]
            epoch_result = EpochResult(
                epoch=epoch, prompt_type=prompt_type, prompt_text=prompt_text[:200],
                response_text=response_text[:500], html_code=f"[{html_lines} lines]",
                html_lines=html_lines, response_time_s=dt,
                prompt_tokens=api_result.get("prompt_tokens", 0),
                completion_tokens=api_result.get("completion_tokens", 0),
                test_results=test_results, tests_passed=passed, tests_total=total,
                all_passed=all_passed,
                screenshot_path=str(screenshot_dir / f"epoch_{epoch}.png"),
                timestamp=datetime.now(timezone.utc).isoformat(),
                errors_found=failing_tests,
            )
            run_result.epochs.append(asdict(epoch_result))
            run_result.total_epochs = epoch

            elapsed = time.time() - t_start
            print(f"\n  ┌─ Epoch {epoch} Summary ─────────────────────────")
            print(f"  │ Type: {prompt_type} | Tests: {passed}/{total} | Time: {dt:.0f}s | Lines: {html_lines}")
            print(f"  │ Total elapsed: {elapsed/60:.1f} min")

            if all_passed:
                print(f"  │ *** ALL TESTS PASSED! ***")
                print(f"  └────────────────────────────────────────────")
                run_result.success = True
                break

            # Record error history for next epoch
            error_history.append({
                "epoch": epoch,
                "errors": failing_tests,
                "tests_passed": passed,
                "outcome": f"{passed}/{total} tests passed. Failing: {', '.join(failing_tests)}",
            })

            print(f"  │ Failing: {', '.join(failing_tests)}")
            print(f"  │ Next: epoch {epoch + 1}")
            print(f"  └────────────────────────────────────────────")

    finally:
        await client.aclose()

    run_result.total_time_s = time.time() - t_start

    # Save final result
    (results_dir / "run_result.json").write_text(
        json.dumps(asdict(run_result), indent=2, default=str), encoding="utf-8"
    )

    return run_result


# ─── Entry Point ──────────────────────────────────────────────────────────────

def main():
    global MOE_TEMPLATE, GAME_PORT

    parser = argparse.ArgumentParser(description="MoE Sovereign Game Debug Loop")
    parser.add_argument("--max-epochs", type=int, default=MAX_EPOCHS)
    parser.add_argument("--template", type=str, default=MOE_TEMPLATE)
    parser.add_argument("--port", type=int, default=GAME_PORT)
    parser.add_argument("--html", type=str, default="",
                        help="Path to existing HTML file to use as starting point (skip generation)")
    args = parser.parse_args()

    MOE_TEMPLATE = args.template
    GAME_PORT = args.port

    if not MOE_API_KEY:
        print("ERROR: MOE_API_KEY environment variable required")
        print("  export MOE_API_KEY=moe-sk-...")
        sys.exit(1)

    # Create results directory
    ts = datetime.now().strftime("%Y%m%d-%H%M%S")
    results_dir = RESULTS_BASE / f"run_{ts}"
    screenshot_dir = results_dir / "screenshots"
    results_dir.mkdir(parents=True, exist_ok=True)
    screenshot_dir.mkdir(parents=True, exist_ok=True)

    # Load initial HTML if provided
    initial_html = ""
    if args.html:
        html_path = Path(args.html)
        if not html_path.exists():
            print(f"ERROR: HTML file not found: {args.html}")
            sys.exit(1)
        initial_html = html_path.read_text(encoding="utf-8")
        html_mode = f"Provided ({html_path.name}, {len(initial_html):,} chars)"
    else:
        html_mode = "Generate from scratch"

    print(f"""
╔══════════════════════════════════════════════════════════════╗
║  MoE Sovereign — Autonomous Game Debug Loop                 ║
╠══════════════════════════════════════════════════════════════╣
║  Template:    {MOE_TEMPLATE:45s} ║
║  Max Epochs:  {args.max_epochs:<45d} ║
║  Game Port:   {GAME_PORT:<45d} ║
║  HTML Source: {html_mode[:45]:45s} ║
║  Results:     {str(results_dir)[:45]:45s} ║
╚══════════════════════════════════════════════════════════════╝
""")

    # Verify container is running
    try:
        r = subprocess.run(
            ["sudo", "docker", "inspect", GAME_CONTAINER, "--format", "{{.State.Running}}"],
            capture_output=True, text=True, timeout=5,
        )
        if "true" not in r.stdout.lower():
            print(f"ERROR: Container {GAME_CONTAINER} is not running")
            sys.exit(1)
    except Exception as e:
        print(f"ERROR: Cannot check container: {e}")
        sys.exit(1)

    # Verify API key
    try:
        resp = httpx.get(
            f"{MOE_API_BASE}/v1/models",
            headers={"Authorization": f"Bearer {MOE_API_KEY}"},
            timeout=10,
        )
        if resp.status_code != 200:
            print(f"ERROR: API key validation failed (HTTP {resp.status_code})")
            sys.exit(1)
        print("  API key validated OK")
    except Exception as e:
        print(f"ERROR: Cannot reach MoE API: {e}")
        sys.exit(1)

    # Run the loop
    result = asyncio.run(run_loop(args.max_epochs, results_dir, screenshot_dir,
                                  initial_html=initial_html))

    # Summary
    print(f"\n{'='*70}")
    print(f"  FINAL RESULT")
    print(f"{'='*70}")
    print(f"  Success:      {'YES' if result.success else 'NO'}")
    print(f"  Epochs Used:  {result.total_epochs}/{result.max_epochs}")
    print(f"  Total Time:   {result.total_time_s:.1f}s ({result.total_time_s/60:.1f}min)")
    print(f"  Results Dir:  {results_dir}")

    if result.epochs:
        print(f"\n  Epoch Summary:")
        for e in result.epochs:
            status = "PASS" if e["all_passed"] else f"{e['tests_passed']}/{e['tests_total']}"
            print(f"    Epoch {e['epoch']:2d}: [{status:8s}] {e['response_time_s']:.1f}s, "
                  f"{e['completion_tokens']} tokens, {e['html_lines']} lines")

    print(f"\n  Run result saved to: {results_dir / 'run_result.json'}")
    return 0 if result.success else 1


if __name__ == "__main__":
    sys.exit(main())
