#!/usr/bin/env python3
"""MoE Sovereign — Iterative Game Enhancement Pipeline.

Takes an existing HTML5 Canvas game and enhances it phase-by-phase via the
MoE API, adding authentic Super Mario Bros gameplay features.

Each phase:
  1. Send research context + current HTML + feature request to MoE API
  2. Extract enhanced HTML from response
  3. Deploy to Docker container
  4. Run Playwright tests (all must pass)
  5. If tests fail → attempt fix (max 2 attempts)
  6. If fix fails → revert to previous phase and continue

Usage:
    MOE_API_KEY=moe-sk-... python3 game_enhance.py --html path/to/game.html
"""
import argparse
import asyncio
import json
import os
import sys
import time
import uuid
from dataclasses import dataclass, field, asdict
from datetime import datetime, timezone
from pathlib import Path
from typing import Optional

import httpx

# Import shared functions from debug loop and test suite
sys.path.insert(0, str(Path(__file__).parent))
from game_debug_loop import (
    call_moe_api, extract_html, deploy_game, build_fix_prompt,
    MOE_API_BASE, GAME_PORT, GAME_CONTAINER, REQUEST_TIMEOUT,
    MAX_RETRIES, RETRY_BACKOFF_BASE,
)
from game_tests import run_all_tests


# ─── Configuration ────────────────────────────────────────────────────────────

MOE_API_KEY = os.getenv("MOE_API_KEY", "")
MOE_TEMPLATE = os.getenv("MOE_TEMPLATE", "cc-expert-70b-deep")
MAX_FIX_ATTEMPTS = int(os.getenv("MAX_FIX_ATTEMPTS", "2"))

RESULTS_BASE = Path(__file__).parent / "results" / "game_enhance"


# ─── Enhancement Phases ──────────────────────────────────────────────────────

RESEARCH_PROMPT = """You are a game design expert. Analyze the original Super Mario Bros (NES, 1985)
and Super Mario Bros 3 gameplay mechanics in detail.

List ALL core features and mechanics that make the game feel authentic:

1. **Physics & Movement**: How does Mario accelerate, decelerate, jump (variable height),
   slide, duck, run vs walk? What makes the movement feel "tight"?

2. **Power-up System**: Mushroom, Fire Flower, Star, 1-Up. How do they spawn from blocks?
   What are the state transitions (small → big → fire)?

3. **Enemy Types & Behavior**: Goomba, Koopa Troopa (shell mechanics), Piranha Plant,
   Hammer Bro, Bullet Bill, Lakitu, Buzzy Beetle. How does each behave?

4. **Block & Item Interactions**: Brick breaking (only when big), ? blocks, invisible blocks,
   coin blocks (multiple hits), vines from blocks.

5. **Level Design Patterns**: Ground sections, underground pipes, platforming gaps, staircase
   patterns, hidden areas, flag pole ending, castle transitions.

6. **Visual Feedback**: Coin collection animation, enemy squish animation, power-up jingle
   indication (screen flash for star), score popup numbers, death animation.

7. **Scoring System**: Points for enemies (100-8000 depending on combo), coins (200),
   flag pole height bonus, time bonus, 1-Up at 100 coins.

Be comprehensive and specific — this will be used as a reference to implement these features
in an HTML5 Canvas recreation."""

ENHANCEMENT_PHASES = [
    {
        "name": "Physics & Movement",
        "prompt": """Enhance the game's physics and movement to match Super Mario Bros feel.

IMPLEMENT THESE SPECIFIC FEATURES:
1. **Momentum/acceleration**: Player should accelerate gradually (not instant full speed).
   Add acceleration (0.5 px/frame), max speed (6 px/frame), friction/deceleration (0.3 px/frame).
2. **Variable jump height**: Holding jump = higher jump, tapping = short hop.
   While jump key held AND velocityY < 0, reduce gravity by 50%.
3. **Running**: Hold Shift to run faster (max speed 9 px/frame, faster acceleration).
4. **Camera scrolling**: The world should be wider than the canvas. Camera follows player
   horizontally with a dead zone. Levels should be 3000-4000px wide instead of 1024px.
   All entity positions become world coordinates, camera offsets rendering.
5. **Coyote time**: Allow jumping for 6 frames after walking off a ledge.
6. **Jump buffer**: If jump pressed up to 6 frames before landing, jump immediately on landing.

CRITICAL RULES:
- Keep ALL existing entity types (platforms, enemies, coins, bonus boxes, pipes).
- Keep the Start button (#startBtn), game state variables (window.gameScore, etc.).
- Keep all visual styling and colors.
- Extend level data to be wider (3000+ px) with more platforms and entities.
- Output the COMPLETE HTML file from <!DOCTYPE html> to </html>. Never truncate."""
    },
    {
        "name": "Power-ups & Items",
        "prompt": """Add the Super Mario Bros power-up system to the game.

IMPLEMENT THESE SPECIFIC FEATURES:
1. **Player states**: small (current size), big (double height), fire (big + shoots).
   Getting hit when big → shrink to small (with brief invincibility flash).
   Getting hit when small → lose life.
2. **Mushroom power-up**: Spawns from ? blocks when player is small. Mushroom slides along
   ground, bounces off walls. Player collects it → grows big (animate growing).
3. **Fire Flower**: Spawns from ? blocks when player is already big. Player turns fire-colored.
   Press X or F key to shoot fireballs (max 2 on screen). Fireballs bounce along ground,
   destroy enemies on contact, disappear after 3 bounces or hitting a wall.
4. **Star (invincibility)**: Spawns from hidden ? blocks. Player flashes rainbow colors for
   10 seconds, kills enemies on contact, immune to damage.
5. **Brick breaking**: When big Mario hits a brick platform from below, it breaks (particle
   animation). When small, brick just bumps up slightly.
6. **Coin blocks**: Some ? blocks give coins instead of power-ups (3-5 hits before depleted).
7. **1-Up mushroom**: Green mushroom from specific hidden blocks, grants extra life.

CRITICAL RULES:
- Preserve all existing game mechanics (movement, scrolling, enemies).
- Keep window.gameScore, window.gameLives, window.gameLevel globals updated.
- Keep #startBtn and all screen overlays working.
- Output the COMPLETE HTML file. Never truncate."""
    },
    {
        "name": "Enemies & AI",
        "prompt": """Expand the enemy system with classic Mario enemy types and behaviors.

IMPLEMENT THESE SPECIFIC FEATURES:
1. **Koopa Troopa** (green turtle): Patrols like Goomba but when stomped, retreats into shell.
   Shell can be kicked (slides fast, kills other enemies, bounces off walls).
   Shell can hit player too if moving. Draw as green rectangle with shell shape.
2. **Piranha Plant**: Appears in pipes. Bobs up and down on a timer (2s up, 2s down).
   Cannot be stomped. Killed only by fireball. Does NOT appear if player is standing near pipe.
3. **Flying enemy (Paragoomba)**: Like Goomba but hops periodically (small jump every 2 seconds).
   When stomped once, loses wings and becomes regular Goomba.
4. **Stomp combo scoring**: Consecutive stomps without touching ground give increasing points:
   100 → 200 → 400 → 800 → 1000 → 2000 → 4000 → 8000 → 1-Up.
5. **Enemy death animation**: When stomped, enemy flattens briefly then disappears.
   When killed by fireball/shell/star, enemy flips upside down and falls off screen.
6. **Score popups**: When collecting coins or defeating enemies, show the point value
   as floating text that rises and fades out.

CRITICAL RULES:
- Keep all existing enemy types working alongside new ones.
- Preserve power-up system, movement physics, and camera scrolling.
- Keep window.gameScore, window.gameLives, window.gameLevel globals.
- Keep #startBtn and all UI elements.
- Output the COMPLETE HTML file. Never truncate."""
    },
    {
        "name": "Level Design & Polish",
        "prompt": """Improve level design and add polish to match classic Mario level structure.

IMPLEMENT THESE SPECIFIC FEATURES:
1. **Level themes**: Level 1 = overworld (current sky theme), Level 2 = underground (dark
   background, blue/gray platforms), Level 3 = castle (dark red/black, lava pits).
2. **Proper level ending**: Flag pole at end of each level. Player slides down pole,
   score bonus based on height (5000 at top, 100 at bottom). Short victory walk animation.
3. **Moving platforms**: Add 2-3 moving platforms per level (horizontal or vertical movement,
   smooth back-and-forth). Player rides on them.
4. **Warp pipes**: Some pipes are enterable (press Down on top of pipe). Teleports player
   to a bonus room or another section of the level.
5. **Checkpoints**: Halfway flag in each level. If player dies after checkpoint, respawn there.
6. **Level progression**: After completing level 3, show "YOU WIN!" screen with final score
   instead of looping back to level 1.
7. **Timer**: 300-second countdown per level displayed in UI. Running out = lose a life.
8. **Coin counter**: Show coin count in UI (separate from score). 100 coins = extra life.

CRITICAL RULES:
- Preserve all existing mechanics (physics, power-ups, enemies).
- Keep window.gameScore, window.gameLives, window.gameLevel updated.
- Keep #startBtn and screen overlays.
- Expand levels to have varied, interesting platforming challenges.
- Output the COMPLETE HTML file. Never truncate."""
    },
    {
        "name": "Visual & Audio Polish",
        "prompt": """Add visual polish to make the game look and feel more like a finished product.

IMPLEMENT THESE SPECIFIC FEATURES:
1. **Pixel art style sprites**: Replace colored rectangles with pixel-art style drawings
   using canvas primitives (small fillRects to create pixel patterns). Mario should have
   a recognizable hat, mustache, overalls. Goombas should look mushroom-shaped. Koopas
   should have shells. Keep everything as canvas drawing (no external images).
2. **Animated sprites**: Player has walk cycle (alternate between 2-3 frames based on movement),
   jump frame (different pose mid-air), duck frame, death frame (hands up, falls off screen).
3. **Parallax background**: 2-3 background layers that scroll at different speeds (mountains
   far = slow, hills mid = medium, foreground details = full speed).
4. **Particle effects**: Brick break particles, coin sparkle, landing dust puff,
   fireball trail, star sparkle trail on invincible player.
5. **Screen effects**: Brief screen shake on enemy stomp combo (4+ kills). Flash white
   briefly when getting star. Fade to black on death, fade in on respawn.
6. **Smooth UI animations**: Score counter rolls up smoothly. Lives display pulses red
   when at 1 life. Level transition has a brief black screen with "WORLD X-1" text.

CRITICAL RULES:
- Do NOT add any external image files or audio files. Everything is canvas-drawn and CSS.
- Preserve ALL gameplay mechanics.
- Keep window.gameScore, window.gameLives, window.gameLevel.
- Keep #startBtn and overlays.
- Output the COMPLETE HTML file. Never truncate."""
    },
]


# ─── Data Classes ─────────────────────────────────────────────────────────────

@dataclass
class PhaseResult:
    phase: int
    name: str
    prompt_type: str  # "research", "enhance", "fix"
    response_time_s: float
    prompt_tokens: int
    completion_tokens: int
    html_lines: int
    tests_passed: int
    tests_total: int
    all_passed: bool
    fix_attempts: int
    reverted: bool
    timestamp: str


@dataclass
class EnhanceRunResult:
    template: str
    session_id: str
    total_phases: int
    completed_phases: int
    total_time_s: float
    phases: list = field(default_factory=list)


# ─── Enhancement Loop ────────────────────────────────────────────────────────

async def run_enhance(
    initial_html: str,
    results_dir: Path,
    screenshot_dir: Path,
    max_fix_attempts: int = 2,
) -> EnhanceRunResult:
    """Run the enhancement pipeline: research → phase 1-5 with testing."""
    session_id = f"game-enhance-{uuid.uuid4().hex[:12]}"
    run_result = EnhanceRunResult(
        template=MOE_TEMPLATE,
        session_id=session_id,
        total_phases=len(ENHANCEMENT_PHASES),
        completed_phases=0,
        total_time_s=0,
    )

    current_html = initial_html
    research_context = ""
    t_start = time.time()

    # Save initial HTML
    (results_dir / "phase_0_initial.html").write_text(current_html, encoding="utf-8")

    client = httpx.AsyncClient()
    try:
        # ── Phase 0: Research ─────────────────────────────────────────────
        print(f"\n{'='*70}")
        print(f"  PHASE 0: RESEARCH — Super Mario Bros Game Design")
        print(f"{'='*70}")

        messages = [{"role": "user", "content": RESEARCH_PROMPT}]
        print(f"  Sending research prompt ({len(RESEARCH_PROMPT):,} chars)...")

        research_session = f"game-enhance-research-{uuid.uuid4().hex[:12]}"
        api_result = await _call_with_retry(client, messages, research_session)

        if "error" in api_result:
            print(f"  [ERROR] Research failed: {api_result['error']}")
            print(f"  Continuing without research context...")
            research_context = ""
        else:
            research_context = api_result["response"]
            dt = api_result["duration"]
            pt = api_result.get("prompt_tokens", 0)
            ct = api_result.get("completion_tokens", 0)
            print(f"  Research complete: {len(research_context):,} chars in {dt:.0f}s")
            print(f"  Tokens: {pt:,} prompt, {ct:,} completion")

            (results_dir / "phase_0_research.txt").write_text(
                research_context, encoding="utf-8"
            )

            run_result.phases.append(asdict(PhaseResult(
                phase=0, name="Research", prompt_type="research",
                response_time_s=dt, prompt_tokens=pt, completion_tokens=ct,
                html_lines=0, tests_passed=0, tests_total=0, all_passed=True,
                fix_attempts=0, reverted=False,
                timestamp=datetime.now(timezone.utc).isoformat(),
            )))

        # ── Phases 1-5: Enhancement ──────────────────────────────────────
        for phase_idx, phase in enumerate(ENHANCEMENT_PHASES, start=1):
            print(f"\n{'='*70}")
            print(f"  PHASE {phase_idx}/{len(ENHANCEMENT_PHASES)}: {phase['name']}")
            print(f"{'='*70}")

            previous_html = current_html  # Save for rollback

            # Build enhancement prompt
            research_section = ""
            if research_context:
                # Truncate research to keep prompt manageable
                truncated = research_context[:4000]
                if len(research_context) > 4000:
                    truncated += "\n[... truncated for context ...]"
                research_section = f"""
=== MARIO BROS GAME DESIGN REFERENCE ===
{truncated}

"""

            enhance_prompt = f"""{research_section}=== CURRENT GAME CODE ===
Below is the current HTML5 Canvas game. Enhance it with the features described.

```html
{current_html}
```

=== ENHANCEMENT REQUEST: {phase['name']} ===
{phase['prompt']}"""

            messages = [{"role": "user", "content": enhance_prompt}]
            prompt_size = len(enhance_prompt)
            print(f"  Prompt: {prompt_size:,} chars (HTML: {len(current_html):,}, "
                  f"research: {len(research_section):,})")
            print(f"  Sending to MoE API ({MOE_TEMPLATE})...")

            # Each phase gets its own session to avoid cached responses
            phase_session = f"game-enhance-p{phase_idx}-{uuid.uuid4().hex[:12]}"
            api_result = await _call_with_retry(client, messages, phase_session)

            if "error" in api_result:
                print(f"  [ERROR] Enhancement failed: {api_result['error']}")
                print(f"  Skipping phase {phase_idx}, keeping current HTML.")
                run_result.phases.append(asdict(PhaseResult(
                    phase=phase_idx, name=phase["name"], prompt_type="enhance",
                    response_time_s=api_result.get("duration", 0),
                    prompt_tokens=0, completion_tokens=0, html_lines=0,
                    tests_passed=0, tests_total=0, all_passed=False,
                    fix_attempts=0, reverted=True,
                    timestamp=datetime.now(timezone.utc).isoformat(),
                )))
                continue

            response = api_result["response"]
            dt = api_result["duration"]
            pt = api_result.get("prompt_tokens", 0)
            ct = api_result.get("completion_tokens", 0)
            print(f"  Response: {len(response):,} chars in {dt:.0f}s "
                  f"({pt:,} prompt, {ct:,} completion tokens)")

            # Save response
            (results_dir / f"phase_{phase_idx}_response.txt").write_text(
                response, encoding="utf-8"
            )

            # Extract HTML
            html = extract_html(response)
            if not html:
                print(f"  [ERROR] Could not extract HTML from response")
                print(f"  Skipping phase, keeping current HTML.")
                run_result.phases.append(asdict(PhaseResult(
                    phase=phase_idx, name=phase["name"], prompt_type="enhance",
                    response_time_s=dt, prompt_tokens=pt, completion_tokens=ct,
                    html_lines=0, tests_passed=0, tests_total=0, all_passed=False,
                    fix_attempts=0, reverted=True,
                    timestamp=datetime.now(timezone.utc).isoformat(),
                )))
                continue

            enhanced_html = html
            html_lines = len(html.splitlines())
            print(f"  HTML extracted: {html_lines} lines, {len(html):,} chars")

            # Deploy and test
            fix_session = f"game-enhance-fix{phase_idx}-{uuid.uuid4().hex[:8]}"
            passed, total, test_results, fix_attempts = await _deploy_and_test(
                enhanced_html, results_dir, screenshot_dir, phase_idx,
                client, fix_session, max_fix_attempts,
            )

            if passed == total:
                current_html = enhanced_html
                (results_dir / f"phase_{phase_idx}_game.html").write_text(
                    current_html, encoding="utf-8"
                )
                run_result.completed_phases = phase_idx
                print(f"\n  Phase {phase_idx} COMPLETE — all {total} tests passed")
            else:
                print(f"\n  Phase {phase_idx} FAILED ({passed}/{total}) after "
                      f"{fix_attempts} fix attempts")
                print(f"  Reverting to previous phase HTML.")
                current_html = previous_html
                # Re-deploy previous version
                await asyncio.to_thread(deploy_game, current_html, results_dir)

            elapsed = time.time() - t_start
            run_result.phases.append(asdict(PhaseResult(
                phase=phase_idx, name=phase["name"], prompt_type="enhance",
                response_time_s=dt, prompt_tokens=pt, completion_tokens=ct,
                html_lines=html_lines, tests_passed=passed, tests_total=total,
                all_passed=(passed == total), fix_attempts=fix_attempts,
                reverted=(passed != total),
                timestamp=datetime.now(timezone.utc).isoformat(),
            )))

            print(f"  Total elapsed: {elapsed/60:.1f} min")

    finally:
        await client.aclose()

    run_result.total_time_s = time.time() - t_start

    # Save final result
    (results_dir / "run_result.json").write_text(
        json.dumps(asdict(run_result), indent=2, default=str), encoding="utf-8"
    )

    # Save final game HTML
    (results_dir / "final_game.html").write_text(current_html, encoding="utf-8")

    return run_result


async def _call_with_retry(
    client: httpx.AsyncClient, messages: list, session_id: str
) -> dict:
    """Call MoE API with retry and backoff."""
    api_result = None
    for attempt in range(1, MAX_RETRIES + 1):
        api_result = await call_moe_api(client, messages, session_id)
        if "error" not in api_result:
            break
        print(f"  [ERROR] Attempt {attempt}/{MAX_RETRIES}: {api_result['error']}")
        if attempt < MAX_RETRIES:
            wait = RETRY_BACKOFF_BASE * (2 ** (attempt - 1))
            print(f"  Waiting {wait}s before retry...")
            await asyncio.sleep(wait)
            # Recreate client in case connection pool is stale
            await client.aclose()
            client = httpx.AsyncClient()
    return api_result


async def _deploy_and_test(
    html: str,
    results_dir: Path,
    screenshot_dir: Path,
    phase: int,
    client: httpx.AsyncClient,
    session_id: str,
    max_fix_attempts: int,
) -> tuple:
    """Deploy HTML, run tests, attempt fixes if needed. Returns (passed, total, results, fix_count)."""
    current = html
    fix_attempts = 0

    for attempt in range(max_fix_attempts + 1):  # 0 = initial, 1..N = fixes
        label = "initial" if attempt == 0 else f"fix {attempt}"
        print(f"  [{label}] Deploying to {GAME_CONTAINER}:{GAME_PORT}...")

        deployed = await asyncio.to_thread(deploy_game, current, results_dir)
        if not deployed:
            print(f"  [{label}] Deployment failed!")
            return 0, 0, {}, fix_attempts

        print(f"  [{label}] Running Playwright tests...")
        test_results = await asyncio.to_thread(
            run_all_tests, port=GAME_PORT, screenshot_dir=screenshot_dir,
            epoch=phase * 10 + attempt,
        )

        passed = sum(1 for r in test_results.values() if r["passed"])
        total = len(test_results)

        print(f"  [{label}] Tests: {passed}/{total}")
        for name, r in test_results.items():
            status = "PASS" if r["passed"] else "FAIL"
            print(f"    [{status}] {name:20s} — {r['detail'][:60]}")

        # Save test results
        suffix = f"phase_{phase}" if attempt == 0 else f"phase_{phase}_fix{attempt}"
        (results_dir / f"{suffix}_tests.json").write_text(
            json.dumps(test_results, indent=2), encoding="utf-8"
        )

        if passed == total:
            return passed, total, test_results, fix_attempts

        # Try to fix
        if attempt < max_fix_attempts:
            fix_attempts += 1
            print(f"\n  Attempting fix {fix_attempts}/{max_fix_attempts}...")

            fix_prompt = build_fix_prompt(current, test_results, phase, [])
            fix_messages = [{"role": "user", "content": fix_prompt}]

            fix_result = await _call_with_retry(client, fix_messages, session_id)
            if "error" in fix_result:
                print(f"  Fix API call failed: {fix_result['error']}")
                continue

            fixed_html = extract_html(fix_result["response"])
            if fixed_html:
                current = fixed_html
                (results_dir / f"phase_{phase}_fix{fix_attempts}.html").write_text(
                    current, encoding="utf-8"
                )
            else:
                print(f"  Could not extract HTML from fix response")

    return passed, total, test_results, fix_attempts


# ─── Entry Point ──────────────────────────────────────────────────────────────

def main():
    global MOE_TEMPLATE

    parser = argparse.ArgumentParser(description="MoE Sovereign Game Enhancement Pipeline")
    parser.add_argument("--html", type=str, required=True,
                        help="Path to the base game HTML file")
    parser.add_argument("--template", type=str, default=MOE_TEMPLATE)
    parser.add_argument("--port", type=int, default=GAME_PORT)
    parser.add_argument("--max-fix-attempts", type=int, default=MAX_FIX_ATTEMPTS)
    args = parser.parse_args()

    MOE_TEMPLATE = args.template
    # Update imported module's globals
    import game_debug_loop
    game_debug_loop.MOE_TEMPLATE = MOE_TEMPLATE
    game_debug_loop.GAME_PORT = args.port
    game_debug_loop.MOE_API_KEY = MOE_API_KEY

    if not MOE_API_KEY:
        print("ERROR: MOE_API_KEY environment variable required")
        sys.exit(1)

    html_path = Path(args.html)
    if not html_path.exists():
        print(f"ERROR: HTML file not found: {args.html}")
        sys.exit(1)

    initial_html = html_path.read_text(encoding="utf-8")

    # Create results directory
    ts = datetime.now().strftime("%Y%m%d-%H%M%S")
    results_dir = RESULTS_BASE / f"run_{ts}"
    screenshot_dir = results_dir / "screenshots"
    results_dir.mkdir(parents=True, exist_ok=True)
    screenshot_dir.mkdir(parents=True, exist_ok=True)

    print(f"""
╔══════════════════════════════════════════════════════════════╗
║  MoE Sovereign — Game Enhancement Pipeline                  ║
╠══════════════════════════════════════════════════════════════╣
║  Template:      {MOE_TEMPLATE:43s} ║
║  Base HTML:     {html_path.name:43s} ║
║  HTML Size:     {f'{len(initial_html):,} chars':43s} ║
║  Phases:        {len(ENHANCEMENT_PHASES):<43d} ║
║  Max Fix Tries: {args.max_fix_attempts:<43d} ║
║  Results:       {str(results_dir)[:43]:43s} ║
╚══════════════════════════════════════════════════════════════╝
""")

    # Verify container
    import subprocess
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

    # Verify API
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

    # Run enhancement pipeline
    result = asyncio.run(run_enhance(
        initial_html, results_dir, screenshot_dir,
        max_fix_attempts=args.max_fix_attempts,
    ))

    # Summary
    print(f"\n{'='*70}")
    print(f"  ENHANCEMENT COMPLETE")
    print(f"{'='*70}")
    print(f"  Phases Completed: {result.completed_phases}/{result.total_phases}")
    print(f"  Total Time:       {result.total_time_s:.1f}s ({result.total_time_s/60:.1f}min)")
    print(f"  Results Dir:      {results_dir}")
    print(f"  Final Game:       {results_dir / 'final_game.html'}")

    if result.phases:
        print(f"\n  Phase Summary:")
        for p in result.phases:
            status = "PASS" if p["all_passed"] else "FAIL"
            reverted = " (REVERTED)" if p.get("reverted") else ""
            fixes = f", {p['fix_attempts']} fixes" if p["fix_attempts"] > 0 else ""
            print(f"    Phase {p['phase']}: [{status:4s}] {p['name']:25s} "
                  f"{p['response_time_s']:.0f}s, {p['completion_tokens']} tokens"
                  f"{fixes}{reverted}")

    return 0


if __name__ == "__main__":
    sys.exit(main())
