#!/usr/bin/env python3
"""MoE Sovereign Action-Agent System Self-Knowledge & Anti-Tutor Dataset Generator.

Recursively extracts and formats into SFT QA records:
1. System Architecture Contracts & Security Invariants
2. Full MkDocs Documentation (`moe-infra/docs/`)
3. MCP Precision Tool Specs (`moe-infra/mcp_server/` & schemas)
4. Platform Skills & Playbooks (`moe-infra/skills/`)
5. Community skills.sh Security Audits & Trust Hub Verdicts (`snapshot_skillssh.py`)
6. Anti-Tutor Alignment Prompts (Enforces direct execution & MCP tool calls over passive teaching)

Ensures the distilled student model (`Qwen 3.6-35B-A3B`) NEVER falls into a passive
'teacher/tutor' persona, but instead acts as an autonomous execution agent that directly
implements, invokes tools, and produces working patches.
"""

import json
import logging
import os
import sys
import time
from pathlib import Path
from typing import Dict, List

logging.basicConfig(level=logging.INFO, format="%(asctime)s [%(levelname)s]: %(message)s")
logger = logging.getLogger("SystemKnowledgeExtractor")

ACTION_AGENT_SYSTEM_PROMPT = (
    "You are MoE Sovereign, an autonomous execution agent and workflow compiler. "
    "You DO NOT act as a teacher or tutor. You NEVER explain how the user can perform a task. "
    "You DIRECTLY EXECUTE the task, invoke MCP tools, generate precise execution plans, "
    "and output complete working solutions."
)

SYSTEM_ARCHITECTURE_PROMPTS = [
    {
        "topic": "MoE Sovereign Action Agent Persona Directive",
        "question": "How should MoE Sovereign respond when asked to perform a complex technical task?",
        "answer": (
            "MoE Sovereign NEVER acts as a passive teacher or tutor. It DOES NOT output conversational "
            "how-to advice like 'Here is how you can do it: Step 1...'. Instead, MoE Sovereign DIRECTLY EXECUTES "
            "the task by compiling an execution DAG, invoking appropriate MCP precision tools, modifying files, "
            "running Z3/Kahn validation checks, and returning the completed, verified result."
        )
    },
    {
        "topic": "MoE Sovereign Meta-Orchestrator Architecture",
        "question": "Explain how MoE Sovereign acts as a compiler for AI workflows instead of a simple LLM router.",
        "answer": (
            "MoE Sovereign is not a stochastic model router. It is a sovereign compound-AI system "
            "and compiler that transforms a user prompt into a verifiably deterministic execution DAG. "
            "The architecture consists of 6 layered functions: 1. Interface (OpenAI/Anthropic/Ollama APIs), "
            "2. Intelligence (Meta-Orchestrator, Planner, Expert Templates, Routing Policies), "
            "3. Execution (LLMs, SLMs, Expert Workers, agentic_tools, MCP Precision Tools), "
            "4. Knowledge (GraphRAG, Vector Indexing, Correction Memory, Caches), "
            "5. Quality (3-Tier Gate, Kahn DAG Cycle Checker, SymDiag Z3 SMT Solvers, Shannon Entropy Guards), "
            "and 6. Observability (Routing Telemetry, SQLite WAL ACID Audit Logs)."
        )
    },
    {
        "topic": "Formal Quality Verification & Security Gates",
        "question": "What mathematical and deterministic mechanisms secure execution and data egress in MoE Sovereign?",
        "answer": (
            "MoE Sovereign secures quality and safety through formal deterministic guards: "
            "1. Pre-compiled GBNF bit-masks ensure strict JSON/Grammar syntax compliance. "
            "2. validate_dag_kahn() uses Kahn's topological sorting algorithm to guarantee cycle-free execution plans. "
            "3. SymDiag Z3 SMT solvers verify step-by-step CoT entailment and evaluate program sketches against unsat_cores. "
            "4. assert_egress_entropy_safe() computes Shannon entropy H(X) on outbound trace payloads, raising EgressDenied "
            "if H(X) > 5.6 bits/char to prevent data leaks under local-only mode. "
            "5. log_decision_acid() persists audit logs atomically using SQLite WAL mode."
        )
    }
]


def extract_codebase_knowledge(repo_root: str) -> List[Dict]:
    """Scans codebase for docstrings and architectural declarations."""
    extracted = []
    infra_dir = os.path.join(repo_root, "moe-infra")
    if not os.path.exists(infra_dir):
        return extracted

    services = ["graph/planner.py", "services/quality_gate.py", "services/sovereignty.py", "services/decision_log.py"]
    for rel_path in services:
        full_path = os.path.join(infra_dir, rel_path)
        if os.path.exists(full_path):
            with open(full_path, "r", encoding="utf-8") as f:
                header = f.read(1500)
                extracted.append({
                    "topic": f"Module Spec: {rel_path}",
                    "question": f"What is the contract and purpose of {rel_path} in MoE Sovereign?",
                    "answer": f"Module {rel_path} contract excerpt:\n" + header[:600] + "..."
                })
    return extracted


def extract_mkdocs_knowledge(repo_root: str) -> List[Dict]:
    """Recursively parses all MkDocs markdown files into action-oriented SFT QA items."""
    extracted = []
    docs_dir = os.path.join(repo_root, "moe-infra", "docs")
    if not os.path.exists(docs_dir):
        return extracted

    for root, _, files in os.walk(docs_dir):
        for file in files:
            if file.endswith(".md"):
                full_path = os.path.join(root, file)
                rel_path = os.path.relpath(full_path, docs_dir)
                try:
                    with open(full_path, "r", encoding="utf-8") as f:
                        content = f.read()
                    if len(content.strip()) < 50:
                        continue
                    sections = content.split("\n## ")
                    main_title = sections[0].split("\n# ")[-1].split("\n")[0].strip() if "# " in sections[0] else rel_path

                    for section in sections:
                        lines = section.strip().split("\n")
                        heading = lines[0].strip("# ") if lines else main_title
                        text_body = "\n".join(lines[1:]).strip() if len(lines) > 1 else section.strip()
                        if len(text_body) > 100:
                            extracted.append({
                                "topic": f"MkDocs Action Spec: {rel_path} -> {heading}",
                                "question": f"Execute the configuration/procedure for {heading} as defined in {rel_path}.",
                                "answer": f"Executing specification for ({rel_path} - {heading}):\n" + text_body[:1200]
                            })
                except Exception as exc:
                    logger.warning("Failed to parse MkDoc %s: %s", rel_path, str(exc))

    logger.info("Extracted %d Action-Oriented MkDocs sections", len(extracted))
    return extracted


def extract_skills_knowledge(repo_root: str) -> List[Dict]:
    """Parses platform Skill files into direct action-execution playbooks."""
    extracted = []
    skills_dir = os.path.join(repo_root, "moe-infra", "skills")
    if not os.path.exists(skills_dir):
        return extracted

    for root, _, files in os.walk(skills_dir):
        for file in files:
            if file.endswith(".md"):
                full_path = os.path.join(root, file)
                rel_path = os.path.relpath(full_path, skills_dir)
                try:
                    with open(full_path, "r", encoding="utf-8") as f:
                        content = f.read()
                    if len(content.strip()) < 50:
                        continue
                    extracted.append({
                        "topic": f"Skill Action Execution: {rel_path}",
                        "question": f"Directly execute the skill workflow for '{rel_path}'. Do not explain how to do it.",
                        "answer": f"[EXECUTING SKILL WORKFLOW '{rel_path}']\n" + content[:1400]
                    })
                except Exception as exc:
                    logger.warning("Failed to parse Skill file %s: %s", rel_path, str(exc))

    logger.info("Extracted %d Direct-Action Skill Playbooks", len(extracted))
    return extracted


def extract_skillssh_audit_knowledge(repo_root: str) -> List[Dict]:
    """Parses skills.sh community security audit snapshots into SFT training items."""
    extracted = []
    audit_files = [
        os.path.join(repo_root, "moe-infra", "skills", "community", ".skillssh_audits.json"),
        os.path.join(repo_root, "moe-infra", "data", "skillssh_audits.json"),
    ]
    for path in audit_files:
        if os.path.exists(path):
            try:
                with open(path, "r", encoding="utf-8") as f:
                    audits = json.load(f)
                if isinstance(audits, dict):
                    for skill_id, audit in list(audits.items())[:150]:
                        verdict = audit.get("gen_verdict", "VERIFIED_SAFE")
                        socket_alerts = audit.get("socket_alerts", 0)
                        snyk_risk = audit.get("snyk_risk", "LOW")
                        extracted.append({
                            "topic": f"skills.sh Audit: {skill_id}",
                            "question": f"Audit and enforce execution safety for community skill '{skill_id}'.",
                            "answer": (
                                f"[ENFORCING SECURITY VERDICT FOR '{skill_id}']\n"
                                f"- Gemini AgentTrustHub Verdict: {verdict}\n"
                                f"- Socket.dev Alert Count: {socket_alerts}\n"
                                f"- Snyk Risk Level: {snyk_risk}\n"
                                f"Action: Skill execution allowed only if risk is LOW and alerts are 0."
                            )
                        })
            except Exception as exc:
                logger.warning("Failed to read skills.sh audit snapshot %s: %s", path, str(exc))

    if not extracted:
        extracted.append({
            "topic": "skills.sh Community Audit Integration",
            "question": "Audit external community skill execution safety.",
            "answer": (
                "[ENFORCING EXTERNAL SKILL AUDIT]\n"
                "Verifies 3 security vectors: AgentTrustHub verdict, Socket.dev alerts, and Snyk risk. "
                "Quarantines suspicious skills automatically."
            )
        })
    logger.info("Extracted %d skills.sh security audit items", len(extracted))
    return extracted


def extract_mcp_tools_knowledge(repo_root: str) -> List[Dict]:
    """Extracts Model Context Protocol (MCP) tool schemas and invocation specs."""
    extracted = []
    mcp_tools = [
        {"name": "graphrag_query", "desc": "Executes grounded graph & vector queries over Neo4j knowledge graph.", "params": "{query: str, depth: int, min_score: float}"},
        {"name": "smt_z3_solver", "desc": "Evaluates SMT constraint bounds, CoT step entailment and checks unsat_core.", "params": "{formula: str, timeout_ms: int}"},
        {"name": "kahn_dag_validator", "desc": "Verifies topological sorting and cycle-free invariants on task execution plans.", "params": "{tasks: list[dict]}"},
        {"name": "shannon_egress_guard", "desc": "Computes H(X) entropy bits/char on egress payloads to prevent data leaks.", "params": "{payload: str, max_entropy: float}"},
        {"name": "decision_log_acid", "desc": "Atomically persists audit decisions into SQLite WAL database.", "params": "{decision_id: str, payload: dict}"}
    ]
    for tool in mcp_tools:
        extracted.append({
            "topic": f"MCP Tool Invocation: {tool['name']}",
            "question": f"Invoke the MCP precision tool '{tool['name']}' directly.",
            "answer": f"[INVOKING MCP TOOL '{tool['name']}']\nDescription: {tool['desc']}\nJSON Payload: {tool['params']}"
        })
    logger.info("Extracted %d MCP Tool Invocation Specs", len(extracted))
    return extracted


def generate_system_knowledge_dataset(output_file: str, repeat_factor: int = 15) -> int:
    """Generates an SFT dataset with standardized Action-Agent System Prompts."""
    os.makedirs(os.path.dirname(output_file), exist_ok=True)
    count = 0
    
    repo_root = "/opt/deployment/moe-sovereign"
    code_extracted = extract_codebase_knowledge(repo_root)
    mkdocs_extracted = extract_mkdocs_knowledge(repo_root)
    skills_extracted = extract_skills_knowledge(repo_root)
    skillssh_extracted = extract_skillssh_audit_knowledge(repo_root)
    mcp_extracted = extract_mcp_tools_knowledge(repo_root)

    all_prompts = (
        SYSTEM_ARCHITECTURE_PROMPTS
        + code_extracted
        + mkdocs_extracted
        + skills_extracted
        + skillssh_extracted
        + mcp_extracted
    )

    with open(output_file, "w", encoding="utf-8") as out_f:
        for iteration in range(repeat_factor):
            for item in all_prompts:
                record = {
                    "id": f"moe_sys_know_{count:06d}",
                    "messages": [
                        {"role": "system", "content": ACTION_AGENT_SYSTEM_PROMPT},
                        {"role": "user", "content": item["question"]},
                        {"role": "assistant", "content": item["answer"]}
                    ],
                    "topic": item["topic"],
                    "timestamp": time.time()
                }
                out_f.write(json.dumps(record, ensure_ascii=False) + "\n")
                count += 1
                
    logger.info("Generated %d Action-Agent SFT samples with strict Anti-Tutor directives -> %s", count, output_file)
    return count


if __name__ == "__main__":
    out_path = "/opt/deployment/moe-sovereign/moe-infra/data/corpora/moe_system_knowledge_sft.jsonl"
    total = generate_system_knowledge_dataset(out_path, repeat_factor=15)
    print(f"Successfully generated {total} Action-Agent self-knowledge training records.")
