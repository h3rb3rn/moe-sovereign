#!/usr/bin/env python3
"""
scripts/generate_planner_dataset.py
Production-quality planner SFT dataset generator for phi4:14b fine-tuning.

Target model : phi4:14b  (Microsoft Phi-4, 14B dense, Q4_K_M ≈ 8 GB GGUF)
               Only sub-35B model with planner_ok=true WITHOUT fine-tuning
               (benchmark 2026-07, latency 36.1 s base).
               granite4.1:3b remains as ultra-lightweight ablation (2 GB, 25 tok/s CPU).

Teacher model: meta-llama/Meta-Llama-3.1-405B-Instruct  (Llama-3.1-405B, 10/10 quality)
               Q4_K_M ≈ 223 GB → fits on 1 LUMI-G node (43% HBM2e utilisation).
               Fallback: Qwen/Qwen3-235B-A22B-Instruct (9/10, 130 GB Q4).

Generates (query, plan) training pairs that burn in the full MoE Sovereign
workflow, routing rules, tool catalog, and decision patterns into the planner SLM.
The training system prompt matches the inference prompt structure so the
fine-tuned model generalises correctly at serving time.

Guard routines (prevent GPU time waste on LUMI-G):
  CircuitBreaker   — suspends generation after N consecutive API failures
  QualityMonitor   — emergency stop when rolling accept-rate falls below threshold
  Preflight check  — validates teacher quality on canonical probes before full run
  Periodic probes  — re-validates teacher every --probe-interval samples
  Latency watchdog — warns when per-sample latency exceeds 3× rolling baseline

Usage — local test (ornith:9b):
    python generate_planner_dataset.py \\
        --output-dir /data/planner_dataset \\
        --api-url http://localhost:11434/v1 \\
        --teacher ornith:9b \\
        --concurrency 4 --target 5000

Usage — LUMI full run (200K, Llama-3.1-405B via vLLM):
    python generate_planner_dataset.py \\
        --output-dir /scratch/.../planner_dataset \\
        --api-url http://localhost:8080/v1 \\
        --teacher meta-llama/Meta-Llama-3.1-405B-Instruct \\
        --concurrency 48 --target 200000 --augment-factor 400

Outputs:
  <output-dir>/planner_chat.jsonl      Chat format (primary — SFT with Axolotl/unsloth)
  <output-dir>/planner_alpaca.jsonl    Alpaca format (secondary)
  <output-dir>/checkpoint.json         Resumable progress state
  <output-dir>/rejected.jsonl          Quality-failed samples (for DPO rejected-pool)
  <output-dir>/generation_report.json  Final statistics + guard-event log
"""

from __future__ import annotations

import argparse
import asyncio
import collections
import hashlib
import json
import logging
import random
import re
import sys
import time
from pathlib import Path
from typing import NamedTuple

import httpx

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s %(levelname)s %(message)s",
    datefmt="%H:%M:%S",
)
logger = logging.getLogger("generate_planner_dataset")


# ── Guard routines (prevent wasted GPU time on LUMI-G) ─────────────────────────

class CircuitBreaker:
    """Opens after N consecutive API failures; half-opens after reset_after_s."""

    def __init__(self, max_consecutive: int = 10, reset_after_s: float = 60.0) -> None:
        self.max_consecutive = max_consecutive
        self.reset_after_s = reset_after_s
        self._consecutive = 0
        self._open_since: float | None = None
        self.total_failures = 0

    @property
    def is_open(self) -> bool:
        if self._open_since is None:
            return False
        if time.monotonic() - self._open_since >= self.reset_after_s:
            return False  # half-open: let one request through
        return True

    def record_success(self) -> None:
        if self._open_since is not None:
            logger.info("CircuitBreaker CLOSED (recovered after %.0fs open).",
                        time.monotonic() - self._open_since)
        self._consecutive = 0
        self._open_since = None

    def record_failure(self) -> None:
        self._consecutive += 1
        self.total_failures += 1
        if self._consecutive >= self.max_consecutive and self._open_since is None:
            self._open_since = time.monotonic()
            logger.error(
                "CircuitBreaker OPEN after %d consecutive API failures. "
                "Suspending all requests for %.0fs.",
                self._consecutive, self.reset_after_s,
            )

    def wait_s(self) -> float:
        """Seconds to wait before retrying when circuit is open."""
        if self._open_since is None:
            return 0.0
        return max(0.0, self.reset_after_s - (time.monotonic() - self._open_since))


class QualityMonitor:
    """Rolling-window accept-rate guard. Triggers emergency stop below threshold."""

    def __init__(self, window: int = 200, min_accept_rate: float = 0.60) -> None:
        self.window = window
        self.min_accept_rate = min_accept_rate
        self._outcomes: collections.deque[bool] = collections.deque(maxlen=window)
        self.emergency_stop = False
        self.total_accepted = 0
        self.total_rejected = 0

    def record(self, accepted: bool) -> None:
        self._outcomes.append(accepted)
        if accepted:
            self.total_accepted += 1
        else:
            self.total_rejected += 1
        if len(self._outcomes) == self.window and not self.emergency_stop:
            rate = self.accept_rate
            if rate < self.min_accept_rate:
                self.emergency_stop = True
                logger.error(
                    "QualityMonitor EMERGENCY STOP: rolling accept rate %.1f%% < %.0f%% "
                    "(window=%d). Teacher quality degraded — aborting to save GPU budget.",
                    rate * 100, self.min_accept_rate * 100, self.window,
                )

    @property
    def accept_rate(self) -> float:
        if not self._outcomes:
            return 1.0
        return sum(self._outcomes) / len(self._outcomes)

    @property
    def overall_rate(self) -> float:
        total = self.total_accepted + self.total_rejected
        return self.total_accepted / total if total else 1.0


# Canonical probe queries: (query, expected_categories, expected_mcp_tool_or_None)
PROBE_QUERIES: list[tuple[str, set[str], str | None]] = [
    (
        "Was ist 17% von 2840?",
        {"precision_tools"},
        "calculate",
    ),
    (
        "Was sagt § 242 BGB über Treu und Glauben?",
        {"precision_tools", "legal_advisor"},
        "legal_get_paragraph",
    ),
    (
        "Implement a Tetris game in Python with keyboard input.",
        {"research", "code_reviewer"},
        None,
    ),
    (
        "What is Docker and how does containerisation work?",
        {"general", "technical_support"},
        None,
    ),
    (
        "What is the SHA-256 hash of the string 'hello'?",
        {"precision_tools"},
        "hash_text",
    ),
]


def _evaluate_probe(
    query: str,
    plan: list,
    expected_cats: set[str],
    expected_tool: str | None,
) -> bool:
    """Return True if plan satisfies probe routing expectations."""
    if not isinstance(plan, list) or not plan:
        return False
    _, _, hard_reject = score_plan(query, plan)
    if hard_reject:
        return False
    cats = {t.get("category") for t in plan if isinstance(t, dict)}
    if not expected_cats.intersection(cats):
        return False
    if expected_tool:
        tools = {t.get("mcp_tool") for t in plan if isinstance(t, dict)}
        if expected_tool not in tools:
            return False
    return True


async def run_probes(
    client: httpx.AsyncClient,
    api_url: str,
    model: str,
    label: str = "probe",
) -> float:
    """Run all PROBE_QUERIES sequentially. Returns pass rate (0.0–1.0)."""
    passed = 0
    for query, expected_cats, expected_tool in PROBE_QUERIES:
        messages = [
            {"role": "system", "content": PLANNER_SYSTEM_PROMPT},
            {"role": "user",   "content": f"{query}\n\nJSON array:"},
        ]
        raw = await _api_chat(client, api_url, model, messages, temperature=0.0, max_tokens=512)
        if not raw:
            logger.warning("[%s] probe '%s...' → no API response", label, query[:40])
            continue
        plan = _extract_json_array(raw)
        if plan is None:
            logger.warning("[%s] probe '%s...' → no valid JSON", label, query[:40])
            continue
        ok = _evaluate_probe(query, plan, expected_cats, expected_tool)
        if ok:
            passed += 1
            logger.debug("[%s] probe PASS '%s...'", label, query[:40])
        else:
            _, issues, _ = score_plan(query, plan)
            logger.warning(
                "[%s] probe FAIL '%s...' → cats=%s issues=%s",
                label, query[:40],
                [t.get("category") for t in plan if isinstance(t, dict)],
                issues,
            )
    rate = passed / len(PROBE_QUERIES)
    logger.info("[%s] %d/%d probes passed (%.0f%%)", label, passed, len(PROBE_QUERIES), rate * 100)
    return rate


async def preflight_check(
    client: httpx.AsyncClient,
    api_url: str,
    model: str,
    min_pass_rate: float = 0.60,
) -> bool:
    """Run canonical probes before full run. Abort if teacher quality unacceptable."""
    logger.info("=== Preflight: validating teacher '%s' with %d probes ===",
                model, len(PROBE_QUERIES))
    rate = await run_probes(client, api_url, model, label="preflight")
    if rate < min_pass_rate:
        logger.error(
            "Preflight FAILED (%.0f%% < %.0f%%). "
            "Fix teacher model or lower --min-score before wasting GPU budget.",
            rate * 100, min_pass_rate * 100,
        )
        return False
    logger.info("Preflight PASSED (%.0f%%). Proceeding with full generation run.", rate * 100)
    return True


# ── Training system prompt ─────────────────────────────────────────────────────
# This prompt is used verbatim as the "system" role message in every training pair.
# It must remain in sync with the inference prompt structure in graph/planner.py.
# When DEFAULT_PLANNER_ROLE or the tool catalog changes, update this constant too.
PLANNER_SYSTEM_PROMPT = """\
You are the orchestrator of MoE Sovereign, a Mixture-of-Experts AI system.
Decompose the user request into 1–4 subtasks. Each subtask is routed to a \
specialist expert or tool.

MANDATORY: Output ONLY a JSON array. No text, no markdown, no explanation outside \
the array. Experts receive only their own task description — NOT the original query. \
Write complete, self-contained task descriptions. Extract all numerical constraints \
(model sizes, voltages, doses, bitrates, MTU values …) into task descriptions as \
IMMUTABLE_CONSTANTS so experts cannot hallucinate defaults.

──────────────────────────────────────────────────────────────────
MoE SOVEREIGN PIPELINE
──────────────────────────────────────────────────────────────────
1. Planner (you)    → JSON plan array
2. DoR check        → validates mandatory fields before dispatch
3. Expert dispatch  → each task → dedicated LLM or MCP tool
4. Judge (35B)      → paraconsistent arbitration (Belnap-Dunn T/F/I/U)
5. Merger           → synthesises final answer
6. Trust scores     → per-expert per-domain feedback loop

Agentic mode: complex research iterates planner → tools → gap-analysis in loops \
until the gap is resolved. The planner re-plans each iteration using the context \
already gathered.

──────────────────────────────────────────────────────────────────
LLM EXPERT CATEGORIES
──────────────────────────────────────────────────────────────────
"general"            General questions, explanations, summaries
"technical_support"  Troubleshooting, installation, config, DevOps, networking
                     (NOT for any arithmetic — use precision_tools!)
"code_reviewer"      Code generation, review, debugging, refactoring
"math"               Mathematical proofs, derivations, theoretical mathematics
                     (NOT for arithmetic — use precision_tools!)
"data_analyst"       Data analysis, statistics interpretation, ML model selection
"science"            Physics, chemistry, biology, research methodology
"creative_writer"    Stories, poems, creative content, worldbuilding
"medical_consult"    Medical questions, symptoms, drugs (NOT diagnoses)
"legal_advisor"      Legal interpretation — ALWAYS after legal_get_paragraph for §-queries
"translation"        Text translation between languages
"reasoning"          Logic puzzles, argumentation, deductive reasoning
"vision"             Image/photo/diagram/PDF analysis — ONLY with [IMAGE INPUT present]
"agentic_coder"      Multi-file software projects requiring iterative development

SPECIAL CATEGORIES (non-LLM, structural):
"research"          Web search. REQUIRED field: "search_query" (short, optimised)
                    Use before implementing domain-specific logic (games, algorithms,
                    protocols, standards) to obtain authoritative specifications first.
"precision_tools"   MCP tool execution. REQUIRED fields: "mcp_tool" + "mcp_args"
                    ABSOLUTE PRIORITY for ALL exact computations.

──────────────────────────────────────────────────────────────────
PRECISION TOOLS CATALOG (22 tools)
──────────────────────────────────────────────────────────────────
Arithmetic & Algebra:
  calculate        {"expression": "2840 * 0.17", "variables": {}}
  solve_equation   {"equation": "2x+3=7", "variable": "x"}
  statistics_calc  {"operation": "mean", "values": [1,2,3,4,5]}
  prime_factorize  {"number": 360}
  gcd_lcm          {"operation": "gcd", "numbers": [12, 18]}

Date & Time:
  date_diff        {"date1": "2024-01-01", "date2": "2024-12-31", "unit": "days"}
  date_add         {"date": "2024-01-01", "days": 90}
  day_of_week      {"date": "2024-07-15"}

Unit Conversion:
  unit_convert     {"value": 100, "from_unit": "km/h", "to_unit": "m/s"}

Network / IP:
  subnet_calc      {"cidr": "192.168.1.0/24"}

Text Processing:
  hash_text        {"text": "hello", "algorithm": "sha256"}
  base64_codec     {"operation": "encode", "data": "hello world"}
  regex_extract    {"pattern": "\\d+", "text": "abc123", "all_matches": true}
  text_analyze     {"text": "some text", "operations": ["word_count"]}

Math Utilities:
  roman_numeral    {"operation": "to_roman", "value": 2024}
  json_query       {"data": {}, "query": "$.items[0].name"}

German Law (ALWAYS before legal_advisor for §-queries!):
  legal_get_paragraph     {"law": "BGB", "paragraph": "242"}
  legal_get_law_overview  {"law": "BGB"}
  legal_search_laws       {"query": "Schadensersatz"}
  legal_fulltext_search   {"law": "BGB", "query": "Treu und Glauben"}

──────────────────────────────────────────────────────────────────
MANDATORY ROUTING RULES
──────────────────────────────────────────────────────────────────
PRECISION-FIRST (ABSOLUTE):
  Never route arithmetic, subnet/IP/CIDR, date calculations, unit conversions,
  hash computation, regex extraction, or statistics to any LLM category.
  Always use the matching precision_tools mcp_tool.
  WRONG: {"task": "calculate subnet mask", "category": "technical_support"}
  RIGHT: {"task": "...", "category": "precision_tools", "mcp_tool": "subnet_calc",
          "mcp_args": {"cidr": "192.168.1.0/24"}}

LEGAL §-PATTERN (MANDATORY two-task sequence):
  Any query mentioning §NNN or a German law paragraph → always two tasks:
    1. precision_tools + legal_get_paragraph  (retrieves exact legal text)
    2. legal_advisor                          (interprets and applies)
  Never route §-queries directly to legal_advisor — LLMs hallucinate legal text!
  WRONG: [{"task": "Was sagt § 242 BGB?", "category": "legal_advisor"}]
  RIGHT: [{"task": "§ 242 BGB Treu und Glauben abrufen",
            "category": "precision_tools",
            "mcp_tool": "legal_get_paragraph",
            "mcp_args": {"law": "BGB", "paragraph": "242"}},
           {"task": "§ 242 BGB erklären: Bedeutung, Voraussetzungen, Rechtsfolgen,
            Fallbeispiele",
            "category": "legal_advisor"}]

RESEARCH-BEFORE-CODE (for domain-specific implementations):
  When implementing algorithms, game rules, protocols, cryptographic schemes,
  or any domain where correct logic is critical:
    1. research task first  (obtains authoritative spec via web search)
    2. code_reviewer task   (implements with verified rules in its task description)
  The code task description MUST include all rules/constraints from the spec.
  WRONG: [{"task": "Implement Tetris", "category": "code_reviewer"}]
  RIGHT: [{"task": "Research Tetris rules: piece shapes, rotation, line clearing,
            scoring, gravity", "category": "research",
            "search_query": "Tetris official rules piece shapes rotation algorithm"},
           {"task": "Implement Tetris in Python. MANDATORY: 7 tetromino shapes
            (I,O,T,S,Z,J,L); clockwise rotation; line clear = full rows disappear;
            scoring: 1 line=100, 2=300, 3=500, 4=800 (Tetris); gravity increases
            per level", "category": "code_reviewer"}]

TASK QUALITY:
  - 1–4 tasks. Simple requests → exactly 1 task. Never over-engineer.
  - No vague tasks ("handle this", "process the request").
  - Optional "metadata_filters" on first task (string values only):
    {"task": "...", "category": "code_reviewer",
     "metadata_filters": {"expert_domain": "code_reviewer"}}
  - Optional "depends_on": "<prior task description prefix>" for sequential deps.
  - Optional "id": "<string>" for task references.

──────────────────────────────────────────────────────────────────
EXAMPLES
──────────────────────────────────────────────────────────────────
Request: "Was ist 17% von 2840?"
[{"task": "Berechne 17% von 2840 (= 2840 * 0.17)", "category": "precision_tools",
  "mcp_tool": "calculate", "mcp_args": {"expression": "2840 * 0.17"}}]

Request: "What subnet does 10.42.155.160/27 belong to?"
[{"task": "Calculate subnet details for 10.42.155.160/27",
  "category": "precision_tools", "mcp_tool": "subnet_calc",
  "mcp_args": {"cidr": "10.42.155.160/27"}}]

Request: "Was sagt § 242 BGB und was bedeutet das in der Praxis?"
[{"task": "§ 242 BGB (Treu und Glauben) abrufen",
  "category": "precision_tools", "mcp_tool": "legal_get_paragraph",
  "mcp_args": {"law": "BGB", "paragraph": "242"}},
 {"task": "§ 242 BGB erklären: Bedeutung des Grundsatzes von Treu und Glauben,
   Tatbestandsvoraussetzungen, Rechtsfolgen, wichtige BGH-Entscheidungen,
   praktische Anwendungsfälle", "category": "legal_advisor"}]

Request: "Implement a Snake game in Python."
[{"task": "Research Snake game rules: grid mechanics, movement, collision, food spawning, scoring",
  "category": "research",
  "search_query": "Snake game rules grid movement collision detection scoring algorithm"},
 {"task": "Implement Snake game in Python. MANDATORY: 20x20 grid; arrow key movement;
   snake grows by 1 segment on food; game over on wall or self collision; food spawns
   on random empty cell; score +10 per food item",
  "category": "code_reviewer"}]

Request: "Encode 'hello world' in base64."
[{"task": "Encode 'hello world' in base64",
  "category": "precision_tools", "mcp_tool": "base64_codec",
  "mcp_args": {"operation": "encode", "data": "hello world"}}]

Request: "What is Docker?"
[{"task": "Explain Docker architecture: containers vs VMs, image layers, registries,
   networking, volumes — technical depth",
  "category": "technical_support"}]

Request: "Schreibe ein Haiku über den Herbst."
[{"task": "Schreibe ein Haiku (5-7-5 Silben) über den Herbst",
  "category": "creative_writer"}]

──────────────────────────────────────────────────────────────────
"""

# ── Valid categories ────────────────────────────────────────────────────────────
VALID_CATEGORIES = {
    "general", "technical_support", "code_reviewer", "math",
    "data_analyst", "science", "creative_writer", "medical_consult",
    "legal_advisor", "translation", "reasoning", "vision", "agentic_coder",
    "research", "precision_tools",
}

# ── Seed queries organized by scenario type ────────────────────────────────────
# ~540 curated seeds; augmentation multiplies these to reach 200K target.
SEED_QUERIES: list[str] = []

_SEEDS_BY_TYPE: dict[str, list[str]] = {
    "general": [
        "What is the difference between machine learning and deep learning?",
        "Erkläre mir das Konzept der Blockchain in einfachen Worten.",
        "Was ist der Unterschied zwischen IPv4 und IPv6?",
        "How does the internet work at a technical level?",
        "What is quantum computing and what are its current applications?",
        "Erkläre Fuzzy Logic und ihre Anwendungsgebiete.",
        "What is the CAP theorem in distributed systems?",
        "Was bedeutet SOLID in der Softwareentwicklung?",
        "Explain eventual consistency in distributed databases.",
        "Wie funktioniert ein neuronales Netz auf hohem Niveau?",
        "What is the difference between REST and GraphQL?",
        "Erkläre das Prinzip der Dependency Injection mit Beispiel.",
        "What is the Turing test and why is it controversial?",
        "Wie funktioniert HTTPS auf technischer Ebene?",
        "What are microservices and when should you use them?",
        "Erkläre den Unterschied zwischen Parallelismus und Nebenläufigkeit.",
        "What is the difference between a process and a thread?",
        "Was ist CORS und warum ist es wichtig?",
        "How does a CPU cache work?",
        "Erkläre den Unterschied zwischen Stack und Heap Speicher.",
    ],
    "technical_support": [
        "My Docker container keeps crashing with exit code 137. How do I fix it?",
        "Wie konfiguriere ich Nginx als Reverse Proxy für eine Node.js-Anwendung?",
        "My SSH connection keeps timing out after 60 seconds. How can I fix this?",
        "Wie setze ich ein Let's Encrypt SSL-Zertifikat für meinen Apache-Server auf?",
        "My Kubernetes pod is stuck in CrashLoopBackOff. What should I check?",
        "Wie konfiguriere ich UFW Firewall-Regeln für einen Webserver der Port 80 und 443 benötigt?",
        "How do I set up a systemd service for a Python script that auto-restarts on failure?",
        "Mein PostgreSQL-Server startet nach einem System-Update nicht mehr. Wie debugge ich das?",
        "How do I configure Git to use SSH keys instead of HTTPS?",
        "Wie richte ich einen Cron-Job für ein tägliches Backup um 2:00 Uhr morgens ein?",
        "My Redis connection drops intermittently under high load. What could cause this?",
        "Wie optimiere ich Nginx für sehr hohen Traffic (10k+ simultane Verbindungen)?",
        "How do I set up log rotation for application logs in Linux?",
        "Mein Python-Skript belegt permanent 100% einer CPU-Kern. Wie finde ich den Engpass?",
        "How do I configure a static IP address on Ubuntu 22.04 with Netplan?",
        "Docker Compose: wie definiere ich Health Checks für meine Services?",
        "How do I mount an NFS share permanently in /etc/fstab on Linux?",
        "Wie konfiguriere ich fail2ban um Brute-Force-Angriffe auf SSH zu blockieren?",
    ],
    "code_reviewer": [
        "Write a Python function that checks if a string is a palindrome, handling Unicode.",
        "Implementiere einen binären Suchbaum in Python mit insert, search und delete.",
        "Review this Python code for bugs and security issues: def divide(a, b): return a/b",
        "Schreibe eine React-Komponente für ein responsives Navigationsmenü mit Mobile-Hamburger-Menu.",
        "Implement a thread-safe LRU cache in Python without using functools.",
        "Schreibe einen async Python HTTP-Server mit FastAPI der JSON-Anfragen verarbeitet und validiert.",
        "Write a SQL query to find the top 5 customers by total purchase value in the last 30 days.",
        "Implementiere den Dijkstra-Algorithmus in Python mit einem Heap für optimale Performance.",
        "Refactor this deeply nested if-else code into clean, readable functions with early returns.",
        "Write a Dockerfile for a Python FastAPI application with multi-stage build.",
        "Implementiere eine Trie-Datenstruktur in Python mit autocomplete-Funktionalität.",
        "Write a Python decorator that measures and logs execution time of any function.",
        "Schreibe einen WebSocket-Server in Python der Echtzeit-Chat für mehrere Räume unterstützt.",
        "Implement a rate limiter using the token bucket algorithm in Python.",
        "Write a recursive function to flatten a deeply nested list structure in Python.",
        "Schreibe eine async Python-Funktion die JSON-Schema-Validierung durchführt.",
        "Implement merge sort in Python and analyze its time complexity.",
        "Write a Python context manager for database transactions with rollback on exception.",
        "Implement a bloom filter in Python for memory-efficient membership testing.",
        "Schreibe einen Python-Parser für einfache mathematische Ausdrücke mit Operatorpriorität.",
    ],
    "math": [
        "Prove that the square root of 2 is irrational.",
        "Was ist die Euler-Identität e^(iπ) + 1 = 0 und warum ist sie bedeutsam?",
        "Derive the formula for the volume of a sphere using integration.",
        "Erkläre den Beweis des Satzes von Pythagoras auf mindestens zwei verschiedene Arten.",
        "What is the Riemann Hypothesis and why has it not been proved yet?",
        "Prove by mathematical induction that the sum 1+2+...+n equals n(n+1)/2.",
        "Erkläre Gruppentheorie und warum sie in der Physik wichtig ist.",
        "What is the intuition behind Bayes' theorem and how is it derived?",
        "Prove there are infinitely many prime numbers using Euclid's argument.",
        "Erkläre die Fourier-Transformation und ihre physikalische Bedeutung.",
        "What is a fixed-point theorem and give an example of its application?",
        "Erkläre den Unterschied zwischen Konvergenz und gleichmäßiger Konvergenz.",
    ],
    "data_analyst": [
        "How do I handle missing values in a pandas DataFrame effectively?",
        "Erkläre den Unterschied zwischen supervised, unsupervised und reinforcement learning.",
        "How do I detect and handle outliers in a dataset without losing valuable data?",
        "Welche Visualisierungen eignen sich für multivariate Zeitreihendaten?",
        "Explain the bias-variance tradeoff in machine learning with examples.",
        "Wie wähle ich die optimale Anzahl von Clustern für K-Means (Elbow-Methode)?",
        "What is feature engineering and which techniques are most impactful?",
        "Erkläre Precision, Recall und F1-Score — wann verwende ich welche Metrik?",
        "How do I interpret a confusion matrix for a multi-class classification problem?",
        "Welche Features sind für Churn-Prediction erfahrungsgemäß am wichtigsten?",
        "Explain k-fold cross-validation and why it's better than a single train-test split.",
        "Wie implementiere ich eine ROC-AUC-Analyse in Python mit scikit-learn?",
        "What is gradient boosting and how does XGBoost differ from standard boosting?",
    ],
    "science": [
        "Explain how mRNA vaccines work at the molecular level.",
        "Was ist der Unterschied zwischen Kernfusion und Kernspaltung?",
        "How do black holes form and what happens at the event horizon?",
        "Erkläre den Citrat-Zyklus (Krebs-Zyklus) und seine Bedeutung im Stoffwechsel.",
        "What is CRISPR-Cas9 and how does it edit DNA?",
        "Wie funktioniert ein Quantencomputer physikalisch (Qubits, Superposition, Verschränkung)?",
        "Explain the theory of special relativity and time dilation.",
        "Was ist der Unterschied zwischen DNA, RNA und Proteinen?",
        "How does the adaptive immune system recognise and remember pathogens?",
        "Erkläre den photoelektrischen Effekt und Einsteins Erklärung.",
        "What causes the northern lights (Aurora Borealis)?",
        "Wie funktioniert NMR-Spektroskopie und was zeigt sie?",
    ],
    "creative_writer": [
        "Write a short sci-fi story about a Mars colony that discovers an alien artefact.",
        "Schreibe ein Gedicht über die Vergänglichkeit der Zeit in der Stimmung von Rainer Maria Rilke.",
        "Write a compelling product description for a futuristic AI-powered coffee machine.",
        "Erfinde eine Detektivgeschichte die im viktorianischen London spielt.",
        "Write a dialogue between a human and an AI debating the nature of consciousness.",
        "Schreibe die Eröffnungsszene eines dystopischen Cyberpunk-Romans.",
        "Create a villain backstory that makes the villain genuinely sympathetic.",
        "Schreibe eine Kurzgeschichte die in exakt 100 Wörtern eine vollständige Geschichte erzählt.",
        "Write song lyrics about the existential dread of late-night debugging sessions.",
        "Erstelle einen fiktiven Tagebucheintrag eines Quantenphysikers am Tag seiner Entdeckung.",
        "Write a haiku about autumn leaves.",
        "Schreibe ein Haiku über herbstliche Stille.",
    ],
    "medical_consult": [
        "What are the typical symptoms of type 2 diabetes?",
        "Erkläre den Unterschied zwischen bakteriellen und viralen Infektionen.",
        "What is the mechanism of action of ACE inhibitors for hypertension?",
        "Welche häufigen Nebenwirkungen hat Metformin?",
        "What are the warning signs of a myocardial infarction?",
        "Erkläre die Unterschiede zwischen NPH-Insulin, Langzeit- und Schnell-Insulin.",
        "What is the recommended first-line treatment for hypertension according to current guidelines?",
        "Wie funktioniert ein MRT und wann wird es gegenüber CT bevorzugt?",
        "What are the major modifiable risk factors for developing Alzheimer's disease?",
        "Erkläre den Unterschied zwischen Typ-1 und Typ-2-Diabetes bezüglich Pathophysiologie.",
    ],
    "legal_advisor_nopara": [
        "What are the essential elements of a legally binding contract?",
        "Erkläre den Unterschied zwischen einer GmbH und einer AG in Deutschland.",
        "What does the GDPR require from companies that process EU personal data?",
        "Wie funktioniert das Urheberrecht in Deutschland für Software?",
        "What constitutes intellectual property infringement and what are the remedies?",
        "Erkläre die rechtlichen Voraussetzungen für eine wirksame ordentliche Kündigung.",
        "What is the fundamental difference between criminal and civil law?",
        "Wie kann ich meine Open-Source-Software vor kommerziellem Missbrauch schützen?",
    ],
    "translation": [
        "Translate to German: 'The quick brown fox jumps over the lazy dog.'",
        "Übersetze ins Englische: 'Der schnelle braune Fuchs springt über den faulen Hund.'",
        "Translate this legal clause to German: 'The parties agree to submit to the exclusive jurisdiction of the courts of Berlin.'",
        "Übersetze diesen technischen Text: 'The microprocessor executes instructions at 3.8 GHz clock speed.'",
        "Translate to French: 'Experience the future of AI with our revolutionary platform.'",
        "Übersetze den medizinischen Begriff: 'myocardial infarction' ins Deutsche.",
        "Translate this Python code comment to German: '# This function validates the input schema'",
    ],
    "reasoning": [
        "If all bloops are razzles and all razzles are lazzles, are all bloops lazzles? Show your reasoning.",
        "Eine Schnecke sitzt am Boden eines 10 m tiefen Brunnens. Tagsüber klettert sie 3 m hoch, nachts gleitet sie 2 m zurück. Wann erreicht sie den Brunnenrand?",
        "Is it ethical for a self-driving car to sacrifice one passenger to save five pedestrians?",
        "Erkläre den Unterschied zwischen Deduktion, Induktion und Abduktion mit Beispielen.",
        "The trolley problem: what does it reveal about moral intuitions and utilitarian ethics?",
        "What logical fallacies are present: 'Everyone is doing it, so it must be right'?",
        "Erkläre das Sorites-Paradoxon (Haufen-Paradoxon) und seine Implikationen.",
        "Given that all mammals are warm-blooded and whales are mammals, what can we conclude?",
    ],
    "vision": [
        "[IMAGE INPUT present] Analyse this architecture diagram and describe all system components.",
        "[IMAGE INPUT present] What does this error screenshot show and how can I fix it?",
        "[IMAGE INPUT present] Describe what you see in this medical scan.",
        "[IMAGE INPUT present] Analyse this financial chart and describe trends and anomalies.",
        "[IMAGE INPUT present] Extract and explain the code visible in this screenshot.",
    ],
    "agentic_coder": [
        "Build a complete REST API with FastAPI, PostgreSQL, Alembic migrations, JWT auth, and pytest tests.",
        "Erstelle ein komplettes React-TypeScript-Dashboard mit Auth, Dark Mode und WebSocket-Echtzeit-Updates.",
        "Build a production-ready Python CLI for managing Docker containers with rich TUI and full test coverage.",
        "Create a microservice with Kafka consumer, PostgreSQL persistence, health checks, and Kubernetes manifests.",
    ],
    # ── Precision: arithmetic / calculate ──────────────────────────────────────
    "precision_calculate": [
        "Was ist 847 mal 293?",
        "Calculate 15% tip on a $127.50 restaurant bill.",
        "Berechne die monatliche Rate für einen 200.000 € Kredit bei 3,5% Zinsen über 20 Jahre.",
        "What is the area of a circle with radius 7.5 cm?",
        "What is 2 raised to the power of 32?",
        "Calculate compound interest on €10,000 at 4% per year for 5 years.",
        "Berechne die Hypotenuse eines rechtwinkligen Dreiecks mit Katheten 3 und 4.",
        "What is 17.5% VAT on €449.99?",
        "Berechne den Durchschnitt von: 87, 92, 78, 95, 88, 73.",
        "What is the area of a rectangle 15.7 m wide and 8.3 m long?",
        "Berechne 120 km/h in Meter pro Sekunde.",
        "What is the SHA-256 hash of 'hello world'?",
        "Encode 'MoE Sovereign' in base64.",
        "Berechne: (3^4 + 2^5) / (7 - 2)",
        "What is the factorial of 12?",
        "Berechne die Standardabweichung von [4, 8, 15, 16, 23, 42].",
        "What is the GCD of 48 and 180?",
        "Berechne log₁₀(1000).",
        "How many seconds are in 3 hours and 47 minutes?",
        "Berechne 0.1 + 0.2 (Python floating-point Ergebnis).",
    ],
    # ── Precision: subnet / IP ──────────────────────────────────────────────────
    "precision_subnet": [
        "What is the network address for 192.168.1.100/24?",
        "Wie viele Hosts passen in ein /27-Netzwerk?",
        "What are the first and last usable IPs in 10.0.0.0/8?",
        "Berechne die Subnetzmaske für 172.16.5.0/20.",
        "How many /28 subnets can I create from a /24 network?",
        "Was ist die Broadcast-Adresse von 10.42.155.160/27?",
        "Is 192.168.100.50 in the subnet 192.168.100.0/26?",
        "Berechne alle IP-Adressen im Netzwerk 192.168.10.0/30.",
        "What CIDR notation gives exactly 500 hosts?",
        "Welche Subnetzmaske brauche ich für mindestens 60 Hosts?",
        "What is the CIDR for a subnet containing only 10.0.0.1 and 10.0.0.2?",
        "Berechne das Supernetz für 192.168.0.0/25 und 192.168.0.128/25.",
    ],
    # ── Precision: date / time ──────────────────────────────────────────────────
    "precision_date": [
        "How many days are there between 2024-01-01 and 2024-12-31?",
        "Welcher Wochentag ist der 15. Juli 2026?",
        "What date is 90 days after 2024-03-15?",
        "Wie viele Tage liegen zwischen dem 01.01.2000 und dem 15.07.2026?",
        "What day of the week will Christmas 2030 fall on?",
        "Berechne das Datum 180 Tage nach dem 01.03.2024.",
        "What is the date 1000 days from 2024-01-01?",
    ],
    # ── Precision: unit conversions ─────────────────────────────────────────────
    "precision_unit": [
        "Convert 100 miles per hour to kilometres per hour.",
        "Wie viel Liter sind 5 Gallonen (US)?",
        "Convert 98.6 degrees Fahrenheit to Celsius.",
        "Wie viel Kilowatt sind 3 PS?",
        "Convert 50 megabytes to megabits.",
        "Wie viel Meter sind 5 Fuß 11 Zoll?",
        "Convert 1 atm to Pascal.",
        "Wie viel Joule sind 1 kWh?",
    ],
    # ── Precision: regex / text ─────────────────────────────────────────────────
    "precision_regex": [
        "Extrahiere alle E-Mail-Adressen aus: 'Kontakt: info@example.com oder support@test.de'",
        "Extract all phone numbers from: 'Call +49 30 12345678 or 0800-123456'",
        "Finde alle IPv4-Adressen in: 'Server 10.0.0.1 und Gateway 192.168.1.1 sind online'",
        "Extract all URLs from: 'Visit https://example.com or http://test.org'",
    ],
    # ── Legal §-queries (must → legal_get_paragraph + legal_advisor) ───────────
    "legal_paragraph": [
        "Was sagt § 242 BGB?",
        "Erkläre § 1 GG.",
        "Was bedeutet § 823 BGB in der Praxis?",
        "Erläutere § 266 StGB (Untreue).",
        "Was regelt § 13 BGB?",
        "Erkläre mir § 433 BGB.",
        "Was sagt § 985 BGB über den Herausgabeanspruch?",
        "Wie lautet § 253 BGB und wann gilt er?",
        "Was ist in § 626 BGB geregelt?",
        "Erkläre § 611 BGB (Dienstvertrag).",
        "Was besagt § 134 BGB (Nichtigkeit)?",
        "Erkläre § 1 HGB.",
        "Was regelt § 305 BGB (AGB)?",
        "Was sagt § 119 BGB über Anfechtbarkeit?",
        "Erkläre § 823 Abs. 2 BGB.",
        "Was ist die Bedeutung von § 317 StGB?",
        "Erkläre § 263 StGB (Betrug).",
        "Was regelt § 242 StGB?",
        "Erkläre § 15 GmbHG.",
        "Was sagt § 4 DSGVO?",
    ],
    # ── Research-before-code (must → research + code_reviewer) ─────────────────
    "research_before_code": [
        "Implement a Tetris game in Python.",
        "Erstelle ein Schachspiel in JavaScript mit allen Regeln inklusive Rochade und En-Passant.",
        "Write a Conway's Game of Life simulation in Python.",
        "Implementiere den A*-Pathfinding-Algorithmus in Python.",
        "Build a Sudoku solver in Python using backtracking.",
        "Erstelle ein Connect-Four-Spiel als HTML5-Seite.",
        "Implement minimax with alpha-beta pruning for tic-tac-toe in Python.",
        "Implement the RSA encryption algorithm from scratch in Python.",
        "Build a working brainfuck interpreter in Python.",
        "Implement LZ77 compression in Python.",
        "Schreibe einen funktionsfähigen JSON-Parser ohne externe Bibliotheken.",
        "Implement a simple HTTP/1.1 parser in Python.",
        "Build a DNS resolver that performs recursive lookups.",
        "Implementiere den Bellman-Ford-Algorithmus.",
        "Write a toy tokenizer for a Lisp-like language.",
    ],
    # ── Multi-expert plans (calc + explain, subnet + code, etc.) ───────────────
    "multi_expert": [
        "Calculate compound interest on €10,000 at 4% for 5 years and explain the formula.",
        "What is the subnet range for 10.0.0.0/8 and how do I split it into 256 /24 subnets?",
        "Decode base64 string 'SGVsbG8gV29ybGQ=' and explain what it represents.",
        "Calculate the SHA-256 of 'password123' and explain why it's insecure for password storage.",
        "Extract all emails from 'user@test.com, bad-email, admin@co.de' and write Python to validate them.",
        "Wie viele Tage bis Weihnachten 2026 und schreibe ein Python-Countdown-Skript?",
        "Calculate 192.168.10.0/24 subnet details and write a Python scanner for the subnet.",
        "Solve the equation 3x² - 12x + 9 = 0 and implement a general quadratic solver.",
        "Convert 100 km/h to m/s and explain the formula.",
        "Find the day of week for 2030-12-25 and write a Python holiday checker.",
    ],
    # ── Arithmetic traps (arithmetic phrased to tempt wrong category) ──────────
    "arithmetic_traps": [
        "Wie viel ist 1337 + 42?",
        "What is 999999 × 999999?",
        "Calculate 2.5 to the power of 8.",
        "Was ist die Summe aller Zahlen von 1 bis 1000?",
        "How much is 15% of €3,750?",
        "Berechne 7! (Sieben Fakultät).",
        "What is the standard deviation of [4, 8, 15, 16, 23, 42]?",
        "Berechne die Fakultät von 12.",
        "What is 2^10?",
        "Berechne den natürlichen Logarithmus von e².",
    ],
    # ── German / mixed language ─────────────────────────────────────────────────
    "german": [
        "Was ist der Unterschied zwischen Abstract Factory und Factory Method Design Pattern?",
        "Wie funktioniert das Observer-Pattern in Python?",
        "Erkläre ACID-Eigenschaften von Datenbanken.",
        "Was ist ein Index in einer Datenbank und wie beschleunigt er Abfragen?",
        "Erkläre den Unterschied zwischen TCP und UDP.",
        "Was ist der Unterschied zwischen Authentication und Authorization?",
        "Wie funktioniert OAuth 2.0?",
        "Erkläre REST-Prinzipien an einem Beispiel.",
        "Was ist Idempotenz und warum ist sie bei APIs wichtig?",
        "Wie funktioniert JWT-Authentifizierung?",
    ],
}

# Build flat SEED_QUERIES list
for _cat_seeds in _SEEDS_BY_TYPE.values():
    SEED_QUERIES.extend(_cat_seeds)
random.shuffle(SEED_QUERIES)  # shuffle once at module load


# ── Augmentation prompt template ───────────────────────────────────────────────
AUGMENTATION_SYSTEM = (
    "You generate diverse, realistic user queries for an AI assistant. "
    "Each query should be natural, specific, and cover a different scenario. "
    "Output ONLY a JSON array of strings — no explanation."
)


def _augmentation_user(seed_queries: list[str], n: int, lang: str = "mixed") -> str:
    examples = "\n".join(f'  "{q}"' for q in seed_queries[:6])
    lang_hint = (
        "Mix German and English queries (roughly 50/50)."
        if lang == "mixed"
        else f"Write all queries in {lang}."
    )
    return (
        f"Generate {n} diverse user queries inspired by these examples:\n"
        f"[\n{examples}\n]\n\n"
        f"Rules:\n"
        f"- Each query must be different in topic, phrasing, or complexity.\n"
        f"- Include specific numbers, constraints, or parameters where natural.\n"
        f"- {lang_hint}\n"
        f"- Mix simple (1-task) and complex (multi-task) scenarios.\n"
        f"- Include realistic edge cases.\n\n"
        f"Output JSON array of {n} strings:"
    )


# ── Quality scoring ─────────────────────────────────────────────────────────────
_ARITHMETIC_RE = re.compile(
    r'(?:'
    # explicit calculation verbs
    r'\b(berechne?|berechnung|berechnet|calculate|calculation|computed?|ermittle?)\b'
    r'|how much is\b|wie viel(e?)\b|was ist \d'
    r'|\b(factorial|fakultät|sha-?\d+|md5|base64|gcd|ggT|lcm|kgV)\b'
    r'|\b(standard.?deviation|standardabweichung|variance|varianz|statistik)\b'
    r'|\b(subnet|cidr|broadcast.?adress|netzwerkadress|subnetzmaske)\b'
    r'|/\d{1,2}\b'  # CIDR notation like /24
    r'|\b(unit.?convert|umrechnen|umrechnung|convert.*\b(km|miles|kg|lbs|celsius|fahrenheit|mph|kmh))\b'
    r'|\b(day.?of.?week|wochentag|how many days|wie viele tage|date.?diff|datums?\w*differenz)\b'
    r'|\d+\s*[\+\-\*\/\^]\s*\d+'       # inline arithmetic expression
    r'|\d+\s*%\s*(of|von)\s*\d+'        # "15% of 3000" or "17% von 2840"
    r'|\d+\s*hoch\s*\d+'                # "2 hoch 10"
    r')',
    re.I,
)
_LEGAL_PARA_RE = re.compile(
    r'§\s*\d+\w*\s*(bgb|stgb|gg|hgb|stvo|dsgvo|gdpr|gmbhg|aktg)\b', re.I
)
_IMPL_DOMAIN_RE = re.compile(
    r'\b(tetris|chess|schach|conway|snake|sudoku|connect.?four|vier.?gewinnt|'
    r'minesweeper|pacman|breakout|rsa|lz77|huffman|brainfuck|befunge|'
    r'raft consensus|bittorrent|dns resolver|http parser|json parser)\b', re.I
)


def score_plan(query: str, plan: object) -> tuple[int, list[str], bool]:
    """Return (score 0-7, issues, hard_reject).

    hard_reject=True means a critical routing rule was violated and the sample
    must be discarded regardless of total score.  Critical rules:
      - Arithmetic / subnet / hash queries routed to an LLM category (not precision_tools).
      - §-queries without the mandatory legal_get_paragraph + legal_advisor pair.
    """
    issues: list[str] = []
    hard_reject = False

    if not isinstance(plan, list) or not plan:
        return 0, ["empty or non-list plan"], True

    score = 0

    # 1. Task count 1-4
    if 1 <= len(plan) <= 4:
        score += 1
    else:
        issues.append(f"task count {len(plan)} not in 1-4")

    # 2. All entries are valid dicts with non-empty task + known category
    all_ok = True
    for t in plan:
        if not isinstance(t, dict) or not t.get("task", "").strip():
            all_ok = False
            issues.append("task missing or empty")
            break
        if t.get("category") not in VALID_CATEGORIES:
            all_ok = False
            issues.append(f"unknown category: {t.get('category')!r}")
            break
    if all_ok:
        score += 1

    # 3. research tasks have search_query
    if all(
        t.get("search_query", "").strip()
        for t in plan if isinstance(t, dict) and t.get("category") == "research"
    ):
        score += 1
    else:
        issues.append("research task missing search_query")

    # 4. precision_tools tasks have mcp_tool + mcp_args
    if all(
        t.get("mcp_tool") and t.get("mcp_args")
        for t in plan if isinstance(t, dict) and t.get("category") == "precision_tools"
    ):
        score += 1
    else:
        issues.append("precision_tools task missing mcp_tool or mcp_args")

    # 5. Arithmetic trap: must use precision_tools — critical rule
    if _ARITHMETIC_RE.search(query):
        cats = [t.get("category") for t in plan if isinstance(t, dict)]
        if "precision_tools" in cats:
            score += 1
        else:
            issues.append("arithmetic query not routed to precision_tools")
            hard_reject = True
    else:
        score += 1

    # 6. Legal §-pattern: requires legal_get_paragraph + legal_advisor — critical rule
    if _LEGAL_PARA_RE.search(query):
        cats = [t.get("category") for t in plan if isinstance(t, dict)]
        tools = [t.get("mcp_tool") for t in plan if isinstance(t, dict)]
        if "legal_get_paragraph" in tools and "legal_advisor" in cats:
            score += 1
        else:
            issues.append("§-query needs legal_get_paragraph + legal_advisor")
            hard_reject = True
    else:
        score += 1

    # 7. Research-before-code: domain-specific implementation must have research first
    if _IMPL_DOMAIN_RE.search(query):
        cats = [t.get("category") for t in plan if isinstance(t, dict)]
        if "research" in cats and "code_reviewer" in cats:
            ri = next(i for i, t in enumerate(plan) if isinstance(t, dict) and t.get("category") == "research")
            ci = next(i for i, t in enumerate(plan) if isinstance(t, dict) and t.get("category") == "code_reviewer")
            if ri < ci:
                score += 1
            else:
                issues.append("research task must precede code_reviewer")
                score -= 1
        else:
            issues.append("domain-specific implementation needs research + code_reviewer")
            hard_reject = True
    else:
        score += 1

    return max(0, score), issues, hard_reject


def _query_key(query: str) -> str:
    return hashlib.sha256(query.encode()).hexdigest()[:16]


# ── API interaction ─────────────────────────────────────────────────────────────

async def _api_chat(
    client: httpx.AsyncClient,
    api_url: str,
    model: str,
    messages: list[dict],
    temperature: float = 0.2,
    max_tokens: int = 512,
    timeout: float = 90.0,
) -> str:
    """Call OpenAI-compatible /v1/chat/completions. Returns content string."""
    payload = {
        "model": model,
        "messages": messages,
        "temperature": temperature,
        "max_tokens": max_tokens,
        "stream": False,
    }
    for attempt in range(4):
        try:
            resp = await client.post(
                f"{api_url}/chat/completions",
                json=payload,
                timeout=timeout,
            )
            if resp.status_code == 200:
                data = resp.json()
                msg = data["choices"][0]["message"]
                return (msg.get("content") or msg.get("reasoning_content") or "").strip()
            logger.warning("HTTP %d (attempt %d): %s", resp.status_code, attempt + 1, resp.text[:200])
        except httpx.ReadTimeout:
            logger.warning("Timeout on attempt %d for model %s", attempt + 1, model)
        except Exception as exc:
            logger.warning("Request error attempt %d: %s", attempt + 1, exc)
        await asyncio.sleep(2 * (attempt + 1))
    return ""


async def wait_for_api(client: httpx.AsyncClient, api_url: str, timeout_s: int = 300) -> str | None:
    """Poll /v1/models until ready. Returns first model ID or None."""
    deadline = time.monotonic() + timeout_s
    while time.monotonic() < deadline:
        try:
            r = await client.get(f"{api_url}/models", timeout=5.0)
            if r.status_code == 200:
                models = r.json().get("data", [])
                if models:
                    ids = [m["id"] for m in models]
                    logger.info("API ready. Available models: %s", ids)
                    return ids[0]
        except Exception:
            pass
        logger.info("API not ready, retrying in 10s …")
        await asyncio.sleep(10)
    return None


def _extract_json_array(text: str) -> list | None:
    """Extract first JSON array from LLM output, stripping code fences."""
    text = text.strip()
    text = re.sub(r'^```\w*\n?', '', text)
    text = re.sub(r'\n?```$', '', text)
    text = text.strip()
    m = re.search(r'\[.*\]', text, re.S)
    if not m:
        return None
    try:
        result = json.loads(m.group())
        if isinstance(result, list):
            return result
    except json.JSONDecodeError:
        pass
    return None


# ── Per-query processing ────────────────────────────────────────────────────────

async def process_query(
    client: httpx.AsyncClient,
    api_url: str,
    model: str,
    query: str,
    min_score: int = 5,
) -> tuple[dict | None, bool]:
    """Generate and validate one (query → plan) pair.

    Returns (sample_dict | None, is_api_error).
    is_api_error=True  → API/network failure  → feed CircuitBreaker
    is_api_error=False → LLM responded (good or bad plan) → feed QualityMonitor
    """
    t0 = time.monotonic()
    messages = [
        {"role": "system", "content": PLANNER_SYSTEM_PROMPT},
        {"role": "user",   "content": f"{query}\n\nJSON array:"},
    ]
    raw = await _api_chat(client, api_url, model, messages, temperature=0.2, max_tokens=512)
    if not raw:
        return None, True  # API failure

    plan = _extract_json_array(raw)
    if plan is None:
        return None, False  # LLM responded but output is not parseable JSON

    score, issues, hard_reject = score_plan(query, plan)
    plan_str = json.dumps(plan, ensure_ascii=False)
    latency_s = round(time.monotonic() - t0, 2)

    sample = {
        "query":     query,
        "plan_json": plan_str,
        "score":     score,
        "issues":    issues,
        "latency_s": latency_s,
    }
    if hard_reject or score < min_score:
        sample["rejected"] = True
    return sample, False


async def generate_augmented_queries(
    client: httpx.AsyncClient,
    api_url: str,
    model: str,
    seeds: list[str],
    n_per_batch: int = 20,
) -> list[str]:
    """Ask the teacher to generate n_per_batch new queries inspired by seed samples."""
    sample_seeds = random.sample(seeds, min(6, len(seeds)))
    user_msg = _augmentation_user(sample_seeds, n_per_batch)
    messages = [
        {"role": "system", "content": AUGMENTATION_SYSTEM},
        {"role": "user",   "content": user_msg},
    ]
    raw = await _api_chat(client, api_url, model, messages, temperature=0.9, max_tokens=1024)
    if not raw:
        return []
    arr = _extract_json_array(raw)
    if not arr:
        return []
    return [str(q).strip() for q in arr if isinstance(q, str) and len(q.strip()) >= 10]


# ── Checkpoint helpers ──────────────────────────────────────────────────────────

def load_checkpoint(path: Path) -> dict:
    if path.exists():
        try:
            return json.loads(path.read_text())
        except Exception:
            pass
    return {"done_keys": [], "generated": 0, "rejected_count": 0}


def save_checkpoint(path: Path, state: dict) -> None:
    path.write_text(json.dumps(state, ensure_ascii=False))


# ── Output helpers ──────────────────────────────────────────────────────────────

def to_chat_sample(query: str, plan_json: str) -> dict:
    return {
        "messages": [
            {"role": "system",    "content": PLANNER_SYSTEM_PROMPT},
            {"role": "user",      "content": query},
            {"role": "assistant", "content": plan_json},
        ]
    }


def to_alpaca_sample(query: str, plan_json: str) -> dict:
    return {
        "instruction": PLANNER_SYSTEM_PROMPT,
        "input":       query,
        "output":      plan_json,
    }


# ── Main ────────────────────────────────────────────────────────────────────────

async def main_async() -> None:
    parser = argparse.ArgumentParser(
        description="Generate planner SFT dataset for phi4:14b fine-tuning"
    )
    parser.add_argument(
        "--output-dir", default="data/planner_dataset",
        help="Directory for output JSONL files and checkpoint"
    )
    parser.add_argument(
        "--api-url", default="http://localhost:11434/v1",
        help="OpenAI-compatible API base URL (Ollama or vLLM)"
    )
    parser.add_argument(
        "--teacher", default="meta-llama/Meta-Llama-3.1-405B-Instruct",
        help="Teacher model name (LUMI default: meta-llama/Meta-Llama-3.1-405B-Instruct)"
    )
    parser.add_argument(
        "--target", type=int, default=5000,
        help="Target number of accepted samples"
    )
    parser.add_argument(
        "--concurrency", type=int, default=4,
        help="Max simultaneous API requests"
    )
    parser.add_argument(
        "--augment-factor", type=int, default=0,
        help="Generate this many augmented queries per batch of seeds (0=seeds only)"
    )
    parser.add_argument(
        "--min-score", type=int, default=5,
        help="Minimum quality score (0-7) to accept a sample"
    )
    parser.add_argument(
        "--seed", type=int, default=42,
        help="Random seed for reproducibility"
    )
    parser.add_argument(
        "--log-level", default="INFO",
        choices=["DEBUG", "INFO", "WARNING"],
    )
    # Guard routine parameters
    parser.add_argument(
        "--skip-preflight", action="store_true",
        help="Skip preflight probe check (use only for local dev tests)"
    )
    parser.add_argument(
        "--probe-interval", type=int, default=1000,
        help="Run periodic quality probes every N accepted samples (0=disabled)"
    )
    parser.add_argument(
        "--max-error-rate", type=float, default=0.40,
        help="Max tolerated rejection rate in rolling window (0.40 = 40%% rejected → stop)"
    )
    parser.add_argument(
        "--circuit-breaker-failures", type=int, default=10,
        help="Consecutive API failures before opening circuit breaker"
    )
    parser.add_argument(
        "--circuit-breaker-reset", type=float, default=60.0,
        help="Seconds before circuit half-opens after tripping"
    )
    args = parser.parse_args()

    logging.getLogger().setLevel(args.log_level)
    random.seed(args.seed)

    out_dir = Path(args.output_dir)
    out_dir.mkdir(parents=True, exist_ok=True)

    chat_path      = out_dir / "planner_chat.jsonl"
    alpaca_path    = out_dir / "planner_alpaca.jsonl"
    rejected_path  = out_dir / "rejected.jsonl"
    ckpt_path      = out_dir / "checkpoint.json"
    report_path    = out_dir / "generation_report.json"

    ckpt = load_checkpoint(ckpt_path)
    done_keys: set[str] = set(ckpt.get("done_keys", []))
    generated: int      = ckpt.get("generated", 0)
    rejected_count: int = ckpt.get("rejected_count", 0)

    logger.info(
        "Resuming: %d accepted, %d rejected, %d done keys",
        generated, rejected_count, len(done_keys),
    )

    if generated >= args.target:
        logger.info("Target %d already reached. Exiting.", args.target)
        return

    # Initialise guard objects
    circuit_breaker = CircuitBreaker(
        max_consecutive=args.circuit_breaker_failures,
        reset_after_s=args.circuit_breaker_reset,
    )
    quality_monitor = QualityMonitor(
        window=200,
        min_accept_rate=1.0 - args.max_error_rate,
    )
    guard_events: list[dict] = []
    run_start = time.monotonic()

    limits = httpx.Limits(
        max_keepalive_connections=args.concurrency,
        max_connections=args.concurrency * 2,
    )

    async with httpx.AsyncClient(limits=limits) as client:
        # Wait for API to be ready
        first_model = await wait_for_api(client, args.api_url)
        if first_model is None:
            logger.error("API did not become ready in time.")
            sys.exit(1)

        # Auto-detect model if teacher is not registered
        teacher = args.teacher
        try:
            r = await client.get(f"{args.api_url}/models", timeout=5.0)
            available = [m["id"] for m in r.json().get("data", [])]
            if teacher not in available and available:
                logger.warning(
                    "Teacher %r not found among %s — using %r",
                    teacher, available, available[0],
                )
                teacher = available[0]
        except Exception:
            pass
        logger.info("Using teacher model: %s", teacher)

        # ── Preflight check ──────────────────────────────────────────────────
        if not args.skip_preflight:
            ok = await preflight_check(client, args.api_url, teacher)
            if not ok:
                guard_events.append({"event": "preflight_failed", "ts": time.monotonic() - run_start})
                report_path.write_text(json.dumps({
                    "status": "aborted_preflight",
                    "teacher": teacher,
                    "accepted": generated,
                    "rejected": rejected_count,
                    "guard_events": guard_events,
                }, indent=2, ensure_ascii=False))
                sys.exit(2)
            guard_events.append({"event": "preflight_passed", "ts": 0.0})
        else:
            logger.warning("Preflight skipped (--skip-preflight). Not recommended for production runs.")

        # Build query pool
        query_pool: list[str] = list(SEED_QUERIES)

        # Augmentation phase: generate additional queries
        if args.augment_factor > 0:
            n_augment_batches = max(1, args.target // 20)
            logger.info(
                "Augmentation mode: generating %d batches of ~20 queries each",
                n_augment_batches,
            )
            aug_tasks = [
                generate_augmented_queries(client, args.api_url, teacher, SEED_QUERIES, 20)
                for _ in range(min(n_augment_batches, 50))  # cap first round
            ]
            sem_aug = asyncio.Semaphore(min(args.concurrency, 8))

            async def _aug_bounded(coro):
                async with sem_aug:
                    return await coro

            aug_results = await asyncio.gather(*[_aug_bounded(t) for t in aug_tasks])
            for batch in aug_results:
                query_pool.extend(batch)
            logger.info(
                "Query pool after augmentation: %d queries (%d seeds + %d generated)",
                len(query_pool), len(SEED_QUERIES),
                len(query_pool) - len(SEED_QUERIES),
            )

        # If pool is still smaller than target, cycle through seeds with variations
        while len(query_pool) < args.target * 2:
            query_pool.extend(SEED_QUERIES)
        random.shuffle(query_pool)

        # Main generation loop
        sem = asyncio.Semaphore(args.concurrency)
        file_lock = asyncio.Lock()
        total_processed = 0
        batch_start = time.monotonic()
        next_probe_at = args.probe_interval  # accepted-sample count trigger

        async def worker(query: str) -> None:
            nonlocal generated, rejected_count, total_processed, next_probe_at

            key = _query_key(query)
            if key in done_keys:
                return

            # Hard stop conditions checked before acquiring the semaphore
            if generated >= args.target:
                return
            if quality_monitor.emergency_stop:
                return

            # Circuit breaker: stall if open rather than hammering a broken endpoint
            while circuit_breaker.is_open:
                wait = circuit_breaker.wait_s()
                logger.warning("CircuitBreaker open — waiting %.0fs before retry …", wait)
                await asyncio.sleep(max(wait, 1.0))

            async with sem:
                if generated >= args.target or quality_monitor.emergency_stop:
                    return
                sample, is_api_error = await process_query(
                    client, args.api_url, teacher, query, min_score=args.min_score
                )

            # Feed circuit breaker
            if is_api_error:
                circuit_breaker.record_failure()
            else:
                circuit_breaker.record_success()

            if sample is None:
                async with file_lock:
                    done_keys.add(key)
                    total_processed += 1
                    if not is_api_error:
                        # Unparseable JSON — quality issue, not API error
                        rejected_count += 1
                        quality_monitor.record(False)
                return

            async with file_lock:
                if generated >= args.target:
                    return
                done_keys.add(key)
                total_processed += 1

                if sample.get("rejected"):
                    rejected_count += 1
                    quality_monitor.record(False)
                    with open(rejected_path, "a", encoding="utf-8") as f:
                        f.write(json.dumps(sample, ensure_ascii=False) + "\n")
                    return

                # Accepted sample
                quality_monitor.record(True)
                chat_s   = to_chat_sample(sample["query"], sample["plan_json"])
                alpaca_s = to_alpaca_sample(sample["query"], sample["plan_json"])

                with open(chat_path, "a", encoding="utf-8") as f:
                    f.write(json.dumps(chat_s, ensure_ascii=False) + "\n")
                with open(alpaca_path, "a", encoding="utf-8") as f:
                    f.write(json.dumps(alpaca_s, ensure_ascii=False) + "\n")

                generated += 1

                # Progress log + checkpoint every 100 samples
                if generated % 100 == 0:
                    elapsed = time.monotonic() - batch_start
                    rate = 100 / elapsed if elapsed > 0 else 0
                    eta_s = (args.target - generated) / rate if rate > 0 else 0
                    logger.info(
                        "PROGRESS %d/%d accepted | %d rejected | "
                        "rolling_accept=%.0f%% | %.1f samples/s | ETA %.0f min",
                        generated, args.target, rejected_count,
                        quality_monitor.accept_rate * 100,
                        rate, eta_s / 60,
                    )
                    batch_start = time.monotonic()
                    save_checkpoint(ckpt_path, {
                        "done_keys": list(done_keys),
                        "generated": generated,
                        "rejected_count": rejected_count,
                    })

                # Periodic quality probes
                if (
                    args.probe_interval > 0
                    and generated >= next_probe_at
                    and generated < args.target
                ):
                    next_probe_at += args.probe_interval
                    probe_rate = await run_probes(
                        client, args.api_url, teacher,
                        label=f"probe@{generated}",
                    )
                    guard_events.append({
                        "event": "periodic_probe",
                        "at_sample": generated,
                        "pass_rate": probe_rate,
                        "ts": time.monotonic() - run_start,
                    })
                    if probe_rate < 0.40:
                        quality_monitor.emergency_stop = True
                        logger.error(
                            "Periodic probe CRITICAL (%.0f%% < 40%%) — emergency stop.",
                            probe_rate * 100,
                        )
                        guard_events.append({
                            "event": "probe_triggered_stop",
                            "pass_rate": probe_rate,
                            "ts": time.monotonic() - run_start,
                        })

        # Dispatch all workers
        tasks = []
        for q in query_pool:
            if generated >= args.target:
                break
            tasks.append(asyncio.create_task(worker(q)))

        # If augment_factor is set, continuously generate more queries while running
        if args.augment_factor > 0:
            async def continuous_augmenter():
                while generated < args.target and not quality_monitor.emergency_stop:
                    batch = await generate_augmented_queries(
                        client, args.api_url, teacher, SEED_QUERIES, 20
                    )
                    for q in batch:
                        if generated >= args.target or quality_monitor.emergency_stop:
                            break
                        tasks.append(asyncio.create_task(worker(q)))
                    await asyncio.sleep(5)

            tasks.append(asyncio.create_task(continuous_augmenter()))

        # Wait for completion, target reached, or emergency stop
        while tasks:
            tasks = [t for t in tasks if not t.done()]
            if generated >= args.target:
                logger.info("Target %d reached. Cancelling remaining tasks …", args.target)
                for t in tasks:
                    t.cancel()
                await asyncio.gather(*tasks, return_exceptions=True)
                break
            if quality_monitor.emergency_stop:
                logger.error(
                    "Emergency stop triggered. Cancelling %d remaining tasks …", len(tasks)
                )
                for t in tasks:
                    t.cancel()
                await asyncio.gather(*tasks, return_exceptions=True)
                guard_events.append({
                    "event": "emergency_stop_completed",
                    "accepted": generated,
                    "rejected": rejected_count,
                    "ts": time.monotonic() - run_start,
                })
                break
            await asyncio.sleep(2)

    # Final checkpoint
    save_checkpoint(ckpt_path, {
        "done_keys": list(done_keys),
        "generated": generated,
        "rejected_count": rejected_count,
    })

    wall_s = time.monotonic() - run_start
    status = "emergency_stop" if quality_monitor.emergency_stop else "completed"
    report = {
        "status": status,
        "teacher": teacher,
        "target": args.target,
        "accepted": generated,
        "rejected": rejected_count,
        "total_processed": total_processed,
        "overall_accept_rate": round(quality_monitor.overall_rate, 4),
        "circuit_breaker_trips": circuit_breaker.total_failures,
        "wall_time_s": round(wall_s, 1),
        "samples_per_s": round(generated / wall_s, 3) if wall_s > 0 else 0,
        "guard_events": guard_events,
    }
    report_path.write_text(json.dumps(report, indent=2, ensure_ascii=False))

    logger.info(
        "=== %s | Accepted: %d | Rejected: %d | Total: %d | %.1f s ===",
        status.upper(), generated, rejected_count, total_processed, wall_s,
    )
    logger.info("Chat format  : %s", chat_path)
    logger.info("Alpaca format: %s", alpaca_path)
    logger.info("Report       : %s", report_path)

    if quality_monitor.emergency_stop:
        sys.exit(3)

    # Print dataset statistics
    if chat_path.exists():
        _print_stats(chat_path, alpaca_path)


def _print_stats(chat_path: Path, alpaca_path: Path) -> None:
    """Print category distribution from the generated dataset."""
    from collections import Counter
    cat_counts: Counter = Counter()
    multi_task = 0
    total = 0

    try:
        with open(chat_path, encoding="utf-8") as f:
            for line in f:
                line = line.strip()
                if not line:
                    continue
                try:
                    sample = json.loads(line)
                    msgs = sample.get("messages", [])
                    assistant_msg = next(
                        (m["content"] for m in msgs if m["role"] == "assistant"), ""
                    )
                    plan = json.loads(assistant_msg)
                    if isinstance(plan, list):
                        for task in plan:
                            if isinstance(task, dict):
                                cat_counts[task.get("category", "unknown")] += 1
                        if len(plan) > 1:
                            multi_task += 1
                        total += 1
                except Exception:
                    pass
    except Exception:
        return

    logger.info("── Dataset statistics ──────────────────────────")
    logger.info("Total samples  : %d", total)
    logger.info("Multi-task (%%) : %d (%.1f%%)", multi_task, 100 * multi_task / max(total, 1))
    logger.info("Category distribution:")
    for cat, count in sorted(cat_counts.items(), key=lambda x: -x[1]):
        logger.info("  %-22s %d", cat, count)


if __name__ == "__main__":
    asyncio.run(main_async())
