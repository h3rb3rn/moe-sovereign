#!/usr/bin/env python3
"""Playwright test suite for Mario-style HTML5 Canvas games.

Runs 6 sequential tests against a deployed game and returns structured results.
Each test is independent (try/except) so one failure doesn't block others.

Usage:
    python3 game_tests.py [--port 42010] [--screenshot-dir /tmp/screenshots] [--epoch 1]
"""
import argparse
import json
import sys
import time
from pathlib import Path
from typing import Optional

from playwright.sync_api import sync_playwright, Page, Error as PlaywrightError


def _collect_errors(page: Page, timeout_ms: int = 3000) -> list[str]:
    """Collect JS errors during page load."""
    errors: list[str] = []
    page.on("pageerror", lambda exc: errors.append(str(exc)))
    page.on("console", lambda msg: errors.append(msg.text) if msg.type == "error" else None)
    page.wait_for_timeout(timeout_ms)
    return errors


def test_no_js_errors(page: Page) -> tuple[bool, str]:
    """Check for JavaScript errors on page load."""
    errors: list[str] = []
    page.on("pageerror", lambda exc: errors.append(f"pageerror: {exc}"))
    page.on("console", lambda msg: errors.append(f"console.error: {msg.text}") if msg.type == "error" else None)
    page.wait_for_load_state("networkidle")
    page.wait_for_timeout(2000)
    if errors:
        return False, f"{len(errors)} JS error(s): " + "; ".join(errors[:5])
    return True, "No JavaScript errors detected"


def test_start_button(page: Page) -> tuple[bool, str]:
    """Verify a clickable Start button exists."""
    # Try multiple selectors
    selectors = [
        "#startBtn",
        "#startButton",
        "button:has-text('Start')",
        "button:has-text('start')",
        "[onclick*='start' i]",
    ]
    for sel in selectors:
        try:
            loc = page.locator(sel)
            if loc.count() > 0 and loc.first.is_visible():
                return True, f"Start button found via selector: {sel}"
        except PlaywrightError:
            continue
    # List all buttons for diagnostics
    buttons = page.locator("button").all()
    btn_texts = [b.text_content() for b in buttons[:5]]
    return False, f"No Start button found. Buttons on page: {btn_texts}"


def test_canvas_renders(page: Page) -> tuple[bool, str]:
    """After clicking Start, verify canvas has non-empty pixels."""
    # Click start button
    for sel in ["#startBtn", "#startButton", "button:has-text('Start')"]:
        try:
            loc = page.locator(sel)
            if loc.count() > 0 and loc.first.is_visible():
                loc.first.click()
                break
        except PlaywrightError:
            continue

    page.wait_for_timeout(2000)  # Let game loop run

    # Check canvas pixels
    result = page.evaluate("""() => {
        const canvas = document.querySelector('canvas');
        if (!canvas) return {error: 'No canvas element found'};
        const ctx = canvas.getContext('2d');
        if (!ctx) return {error: 'No 2D context'};
        const data = ctx.getImageData(0, 0, canvas.width, canvas.height).data;
        let nonEmpty = 0;
        let totalPixels = canvas.width * canvas.height;
        // Sample every 4th pixel for speed
        for (let i = 0; i < data.length; i += 16) {
            if (data[i] !== 0 || data[i+1] !== 0 || data[i+2] !== 0) nonEmpty++;
        }
        // Count unique colors (sample)
        const colors = new Set();
        for (let i = 0; i < data.length && colors.size < 50; i += 64) {
            colors.add(`${data[i]},${data[i+1]},${data[i+2]}`);
        }
        return {nonEmpty, totalPixels, uniqueColors: colors.size, width: canvas.width, height: canvas.height};
    }""")

    if isinstance(result, dict) and "error" in result:
        return False, result["error"]

    non_empty = result.get("nonEmpty", 0)
    unique = result.get("uniqueColors", 0)
    w, h = result.get("width", 0), result.get("height", 0)

    if non_empty < 50:
        return False, f"Canvas {w}x{h} has only {non_empty} non-empty sampled pixels, {unique} unique colors — game not rendering"
    if unique < 3:
        return False, f"Canvas has {non_empty} pixels but only {unique} unique colors — likely just background, no game entities"

    return True, f"Canvas {w}x{h} rendering: {non_empty} non-empty pixels, {unique} unique colors"


def test_game_state(page: Page) -> tuple[bool, str]:
    """Check that game state variables are exposed globally."""
    result = page.evaluate("""() => {
        const checks = {};
        // Check various naming conventions
        const scoreVars = ['gameScore', 'score', 'game.score', 'window.game?.score'];
        const livesVars = ['gameLives', 'lives', 'game.lives', 'window.game?.lives'];
        const levelVars = ['gameLevel', 'level', 'game.level', 'window.game?.level'];

        function tryGet(name) {
            try { return eval('window.' + name); } catch(e) { return undefined; }
        }

        let foundScore = null, foundLives = null, foundLevel = null;
        for (const v of scoreVars) { const val = tryGet(v); if (val !== undefined) { foundScore = {name: v, value: val}; break; } }
        for (const v of livesVars) { const val = tryGet(v); if (val !== undefined) { foundLives = {name: v, value: val}; break; } }
        for (const v of levelVars) { const val = tryGet(v); if (val !== undefined) { foundLevel = {name: v, value: val}; break; } }

        // Also check if there's a global 'game' object with any state
        let gameObj = null;
        if (typeof window.game === 'object' && window.game !== null) {
            gameObj = Object.keys(window.game).filter(k => typeof window.game[k] !== 'function').slice(0, 10);
        }

        return {score: foundScore, lives: foundLives, level: foundLevel, gameObjKeys: gameObj};
    }""")

    found = []
    missing = []
    for key in ["score", "lives", "level"]:
        if result.get(key):
            found.append(f"{result[key]['name']}={result[key]['value']}")
        else:
            missing.append(key)

    game_keys = result.get("gameObjKeys")
    extra = f" (game object keys: {game_keys})" if game_keys else ""

    if len(found) >= 2:  # At least score + lives
        return True, f"Game state found: {', '.join(found)}{extra}"
    if found:
        return False, f"Partial game state: {', '.join(found)}, missing: {', '.join(missing)}{extra}"
    return False, f"No game state variables found (checked window.gameScore/gameLives/gameLevel, window.game.score/lives/level){extra}"


def test_keyboard_input(page: Page) -> tuple[bool, str]:
    """Test that keyboard input affects the game (canvas diff or game-state change)."""
    # Snapshot game state AND full canvas hash before input
    before = page.evaluate("""() => {
        try {
            const canvas = document.querySelector('canvas');
            const state = {
                score: window.gameScore ?? window.game?.score ?? null,
                lives: window.gameLives ?? window.game?.lives ?? null,
                level: window.gameLevel ?? window.game?.level ?? null,
                playerX: window.playerX ?? window.player?.x ?? null,
                playerY: window.playerY ?? window.player?.y ?? null,
            };
            let canvasHash = null;
            if (canvas) {
                const ctx = canvas.getContext('2d');
                const d = ctx.getImageData(0, 0, canvas.width, canvas.height).data;
                let h = 0;
                for (let i = 0; i < d.length; i += 97) h = ((h << 5) - h + d[i]) | 0;
                canvasHash = h;
            }
            return { state, canvasHash };
        } catch(e) { return null; }
    }""")

    # Press multiple key types to cover different control schemes
    for key in ["ArrowRight", "ArrowLeft", "ArrowUp", "ArrowDown", "Space"]:
        for _ in range(3):
            page.keyboard.press(key)
            page.wait_for_timeout(80)

    page.wait_for_timeout(600)

    # Snapshot after input
    after = page.evaluate("""() => {
        try {
            const canvas = document.querySelector('canvas');
            const state = {
                score: window.gameScore ?? window.game?.score ?? null,
                lives: window.gameLives ?? window.game?.lives ?? null,
                level: window.gameLevel ?? window.game?.level ?? null,
                playerX: window.playerX ?? window.player?.x ?? null,
                playerY: window.playerY ?? window.player?.y ?? null,
            };
            let canvasHash = null;
            if (canvas) {
                const ctx = canvas.getContext('2d');
                const d = ctx.getImageData(0, 0, canvas.width, canvas.height).data;
                let h = 0;
                for (let i = 0; i < d.length; i += 97) h = ((h << 5) - h + d[i]) | 0;
                canvasHash = h;
            }
            return { state, canvasHash };
        } catch(e) { return null; }
    }""")

    if before is None or after is None:
        return False, "Could not read game state or canvas data"

    # Check 1: game state changed (score, lives, level, position)
    changed_fields = []
    if before.get("state") and after.get("state"):
        for k in ("score", "lives", "level", "playerX", "playerY"):
            bv, av = before["state"].get(k), after["state"].get(k)
            if bv is not None and av is not None and bv != av:
                changed_fields.append(f"{k}: {bv}→{av}")
    if changed_fields:
        return True, f"Game state changed after input — {', '.join(changed_fields)}"

    # Check 2: canvas content changed (full-frame hash)
    if before.get("canvasHash") is not None and after.get("canvasHash") is not None:
        if before["canvasHash"] != after["canvasHash"]:
            return True, "Canvas changed after keyboard input — player movement confirmed"

    # Fallback: check for JS errors on keypress
    errors = []
    page.on("pageerror", lambda exc: errors.append(str(exc)))
    page.keyboard.press("ArrowRight")
    page.keyboard.press("Space")
    page.wait_for_timeout(500)
    if errors:
        return False, f"JS errors on keypress: {'; '.join(errors[:3])}"

    return False, "Neither game state nor canvas changed after keyboard input"


def test_screenshot(page: Page, screenshot_dir: Path, epoch: int) -> tuple[bool, str]:
    """Take a full-page screenshot. Always passes (informational)."""
    screenshot_dir.mkdir(parents=True, exist_ok=True)
    path = screenshot_dir / f"epoch_{epoch}.png"
    page.screenshot(path=str(path), full_page=True)
    return True, f"Screenshot saved: {path}"


def run_all_tests(
    port: int = 42010,
    screenshot_dir: Optional[Path] = None,
    epoch: int = 1,
) -> dict:
    """Run all game tests and return structured results.

    Returns: {test_name: {passed: bool, detail: str, duration_s: float}}
    """
    if screenshot_dir is None:
        screenshot_dir = Path("/tmp/game_screenshots")

    results = {}

    with sync_playwright() as p:
        browser = p.chromium.launch(headless=True)
        page = browser.new_page(viewport={"width": 1024, "height": 768})

        try:
            page.goto(f"http://localhost:{port}", wait_until="networkidle", timeout=15000)
        except Exception as e:
            browser.close()
            return {"page_load": {"passed": False, "detail": f"Failed to load page: {e}", "duration_s": 0}}

        tests = [
            ("no_js_errors", lambda: test_no_js_errors(page)),
            ("start_button", lambda: test_start_button(page)),
            ("canvas_renders", lambda: test_canvas_renders(page)),
            ("game_state", lambda: test_game_state(page)),
            ("keyboard_input", lambda: test_keyboard_input(page)),
            ("screenshot", lambda: test_screenshot(page, screenshot_dir, epoch)),
        ]

        for name, fn in tests:
            t0 = time.time()
            try:
                passed, detail = fn()
            except Exception as e:
                passed, detail = False, f"Exception: {type(e).__name__}: {e}"
            dt = time.time() - t0
            results[name] = {"passed": passed, "detail": detail, "duration_s": round(dt, 2)}

        browser.close()

    return results


def main():
    parser = argparse.ArgumentParser(description="Game Playwright Test Suite")
    parser.add_argument("--port", type=int, default=42010)
    parser.add_argument("--screenshot-dir", type=str, default="/tmp/game_screenshots")
    parser.add_argument("--epoch", type=int, default=1)
    args = parser.parse_args()

    results = run_all_tests(args.port, Path(args.screenshot_dir), args.epoch)

    passed = sum(1 for r in results.values() if r["passed"])
    total = len(results)

    print(f"\n{'='*60}")
    print(f"Game Test Results — Epoch {args.epoch}")
    print(f"{'='*60}")
    for name, r in results.items():
        status = "PASS" if r["passed"] else "FAIL"
        print(f"  [{status}] {name:20s} ({r['duration_s']:.1f}s) — {r['detail'][:80]}")
    print(f"{'='*60}")
    print(f"  {passed}/{total} tests passed")
    print(f"{'='*60}\n")

    # Write JSON
    out = Path(args.screenshot_dir) / f"epoch_{args.epoch}_tests.json"
    out.parent.mkdir(parents=True, exist_ok=True)
    out.write_text(json.dumps(results, indent=2))
    print(f"Results saved to {out}")

    return 0 if passed == total else 1


if __name__ == "__main__":
    sys.exit(main())
