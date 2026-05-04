#!/usr/bin/env python3
"""Playwright script to capture admin UI screenshots with privacy filter.

Blurs: logged-in username (navbar), user table data (names, emails),
server URLs, pipeline log usernames, and all sensitive text fields.
"""
import asyncio
from pathlib import Path
from playwright.async_api import async_playwright

ADMIN_URL = "http://localhost:8088"
ADMIN_USER = "philipp"
ADMIN_PASS = r"\NkQJh2gzS6sx]HK"
OUT = Path("/opt/moe-sovereign/docs/assets/screenshots")
VIEWPORT = {"width": 1440, "height": 900}

# CSS injected on every page — blurs all personally-identifiable or internal-infra data
PRIVACY_CSS = """
/* Navbar: logged-in username chip */
.d-flex.align-items-center.gap-2.ms-auto span.text-secondary,
nav span.text-secondary.small {
    filter: blur(7px) !important;
    pointer-events: none;
}
/* Offcanvas sidebar: user name repeated */
#admin-offcanvas span.text-secondary,
#admin-offcanvas .nav-link.text-danger { filter: blur(7px) !important; }

/* Users table: username (col 2) + email (col 3) */
#usersTable td:nth-child(2),
#usersTable td:nth-child(3) { filter: blur(7px) !important; }

/* Pipeline log: username column (col 2, JS-rendered) */
#log-body td:nth-child(2) { filter: blur(7px) !important; }

/* Tool-eval log: user column */
#eval-body td:nth-child(2),
#evalTable td:nth-child(2) { filter: blur(7px) !important; }

/* Live Monitoring — Active Requests table:
   col3=User, col8=Client-IP, col9=API-Key  (col4=Model: intentionally visible) */
#active-table td:nth-child(3),
#active-tbody td:nth-child(3),
#active-table td:nth-child(8),
#active-tbody td:nth-child(8),
#active-table td:nth-child(9),
#active-tbody td:nth-child(9) { filter: blur(7px) !important; }

/* Live Monitoring — Process History table:
   col4=User, col8=API-Key  (col5=Model: intentionally visible) */
#history-table td:nth-child(4),
#history-tbody td:nth-child(4),
#history-table td:nth-child(8),
#history-tbody td:nth-child(8) { filter: blur(7px) !important; }

/* Server cards (/servers page): URL line per card */
#server-cards .text-muted.small.font-monospace,
#server-cards .font-monospace { filter: blur(7px) !important; }

/* Dashboard server table (/): URL column inputs + name inputs */
#servers-table td:nth-child(1) input,
#servers-table td:nth-child(2) input,
#server-tbody td:nth-child(1) input,
#server-tbody td:nth-child(2) input { filter: blur(7px) !important; }

/* ALL font-monospace inputs — catches URL, API key, model, host fields */
input.font-monospace, textarea.font-monospace { filter: blur(7px) !important; }

/* Explicit input type=url and sensitive named inputs */
input[type="url"],
input[name*="url"], input[name*="URL"],
input[name*="host"], input[name*="smtp"],
input[name*="key"],  input[name*="KEY"],
input[name*="secret"], input[name*="password"],
textarea[name*="url"], textarea[name*="servers"] {
    filter: blur(7px) !important;
}

/* Public URL section values */
#APP_BASE_URL, #PUBLIC_ADMIN_URL, #PUBLIC_API_URL,
#LOG_URL, #SEARXNG_URL, #OLLAMA_API_KEY { filter: blur(7px) !important; }

/* Dashboard: inference server JSON textarea / env display */
.env-value, code.env-val { filter: blur(7px) !important; }
"""


async def inject_privacy(page):
    await page.add_style_tag(content=PRIVACY_CSS)
    # Extra JS pass: blur any element whose text matches an IP or domain pattern
    await page.evaluate("""
        const re = /(\\d{1,3}\\.){3}\\d{1,3}|[a-z0-9-]+\\.(de|dev|local|internal|home)(:\\d+)?/gi;
        document.querySelectorAll('td, span, div, p, code, pre').forEach(el => {
            if (re.test(el.textContent) && el.children.length === 0) {
                el.style.filter = 'blur(7px)';
            }
        });
    """)


async def shot(page, filename, url, *, wait_ms=800, pre=None, post=None):
    await page.goto(f"{ADMIN_URL}{url}")
    await page.wait_for_load_state("networkidle")
    if pre:
        await pre(page)
    await page.wait_for_timeout(wait_ms)
    await inject_privacy(page)
    if post:
        await post(page)
    await page.wait_for_timeout(200)
    await page.screenshot(path=OUT / filename, full_page=False)
    print(f"  ✓  {filename}")


async def main():
    OUT.mkdir(parents=True, exist_ok=True)
    async with async_playwright() as p:
        browser = await p.chromium.launch(headless=True)
        ctx = await browser.new_context(viewport=VIEWPORT)
        page = await ctx.new_page()

        # ── Login page (unauthenticated) ──────────────────────────────────
        await page.goto(f"{ADMIN_URL}/login")
        await page.wait_for_load_state("networkidle")
        await page.screenshot(path=OUT / "admin_login.png", full_page=False)
        print("  ✓  admin_login.png")

        # ── Authenticate ──────────────────────────────────────────────────
        await page.fill('input[name="username"]', ADMIN_USER)
        await page.fill('input[name="password"]', ADMIN_PASS)
        await page.click('button[type="submit"]')
        await page.wait_for_url(f"{ADMIN_URL}/", timeout=15000)
        print("  ✓  logged in")

        # ── Dashboard ─────────────────────────────────────────────────────
        await shot(page, "admin_dashboard.png", "/")

        # ── System Monitoring ─────────────────────────────────────────────
        await shot(page, "admin_monitoring_system.png", "/monitoring", wait_ms=1500)

        # ── Live Monitoring — Active Processes ────────────────────────────
        await shot(page, "admin_live_monitoring_active.png", "/live-monitoring", wait_ms=2000)

        # ── Live Monitoring — LLM Instances tab ───────────────────────────
        async def click_llm_tab(page):
            for sel in ['[data-bs-target="#llm"]', 'a[href="#llm"]',
                        'button:has-text("LLM")', 'a:has-text("LLM Instances")']:
                loc = page.locator(sel)
                if await loc.count() > 0:
                    await loc.first.click()
                    await page.wait_for_timeout(600)
                    break

        await shot(page, "admin_live_monitoring_llm.png", "/live-monitoring",
                   wait_ms=2000, post=click_llm_tab)

        # ── Starfleet ─────────────────────────────────────────────────────
        await shot(page, "admin_starfleet.png", "/starfleet", wait_ms=1500)

        # ── Pipeline Log ──────────────────────────────────────────────────
        await shot(page, "admin_pipeline_log.png", "/pipeline-log", wait_ms=2000)

        # ── Expert Templates ──────────────────────────────────────────────
        await shot(page, "admin_expert_templates.png", "/templates")

        # ── Claude Code Profiles ──────────────────────────────────────────
        await shot(page, "admin_profiles.png", "/profiles")
        await shot(page, "admin_cc_profiles.png", "/profiles")

        # ── Users & Roles ─────────────────────────────────────────────────
        await shot(page, "admin_users.png", "/users", wait_ms=1000)

        # ── Inference Servers ─────────────────────────────────────────────
        await shot(page, "admin_servers.png", "/servers", wait_ms=1500)

        # ── Tool Evaluation Log ───────────────────────────────────────────
        await shot(page, "admin_tool_eval.png", "/tool-eval", wait_ms=1500)

        # ── MCP Precision Tools ───────────────────────────────────────────
        await shot(page, "admin_mcp_tools.png", "/mcp-tools")

        # ── Skills ───────────────────────────────────────────────────────
        await shot(page, "admin_skills.png", "/skills")

        # ── Quarantine ────────────────────────────────────────────────────
        await shot(page, "admin_quarantine.png", "/quarantine")

        # ── Maintenance ───────────────────────────────────────────────────
        await shot(page, "admin_maintenance.png", "/maintenance")

        await browser.close()
    print("\nAll admin screenshots captured with privacy filter.")


if __name__ == "__main__":
    asyncio.run(main())
