"""
Tool Injector — injects context-dependent tool definitions into expert system prompts.

Mapping: expert category → tool set description.
Called by _get_expert_prompt() in main.py.
Zero-manual-config: new categories with unknown mappings receive no tool block.
"""

from __future__ import annotations
import os
from typing import Optional

# Path to expert system prompt files
_EXPERTS_PATH = os.getenv("EXPERTS_PATH", "/opt/moe-sovereign/prompts/systemprompt")

# Tool definitions per expert category
# Format: compact prose block appended to the system prompt
_TOOL_BLOCKS: dict[str, str] = {

    "code_reviewer": (
        "\nAvailable Tools:\n"
        "• Filesystem: Read/write files in the project directory via path.\n"
        "• SQLite: Run read queries on the project database.\n"
        "• MCP code_review: For static analysis (if mcp_result is present in context).\n"
        "Use tool results from context when available — do not invent your own outputs."
    ),

    "technical_support": (
        "\nAvailable Tools:\n"
        "• Shell commands: Exact, copy-pastable command lines with correct flags.\n"
        "• MCP precision: subnet_calc, hash_text, regex_extract for deterministic ops.\n"
        "• Docker: sudo docker / sudo docker compose for container management.\n"
        "Check whether MCP results are present in context — use them directly."
    ),

    "math": (
        "\nAvailable Tools:\n"
        "• MCP precision: calculate, solve_equation, statistics_calc, unit_convert.\n"
        "• MCP precision: prime_factorize, gcd_lcm, roman_numeral.\n"
        "MANDATORY: If MCP result is present in context → adopt exactly, do not recalculate.\n"
        "If no MCP result: show complete solution steps with LaTeX."
    ),

    "legal_advisor": (
        "\nAvailable Tools:\n"
        "• MCP legal_get_paragraph: Retrieves exact legal text from gesetze-im-internet.de.\n"
        "• MCP legal_search_laws: Searches laws by keyword.\n"
        "• MCP legal_get_law_overview: Shows all paragraphs of a law.\n"
        "If legal_result is in context: use exact legal text, do NOT hallucinate wording."
    ),

    "data_analyst": (
        "\nAvailable Tools:\n"
        "• Python pandas/numpy/matplotlib: Generate executable code block.\n"
        "• MCP statistics_calc: For descriptive statistics (mean, std, quartiles).\n"
        "• MCP json_query: For JSONPath queries on structured data.\n"
        "Check whether MCP results are present in context."
    ),

    "medical_consult": (
        "\nAvailable Tools:\n"
        "• No direct tool access — base on S3/AWMF/WHO guidelines.\n"
        "• Prioritize web research results in context (web_research) when available.\n"
        "Do not hallucinate dosages or protocol numbers without citing a source."
    ),

    "science": (
        "\nAvailable Tools:\n"
        "• MCP calculate: Physical/chemical calculations.\n"
        "• MCP unit_convert: SI unit conversions.\n"
        "• Use web research results in context for current research findings."
    ),

    "reasoning": (
        "\nAvailable Tools:\n"
        "• No tool access — reasoning is based exclusively on provided context.\n"
        "• Use MCP and graph results in context as fact anchors."
    ),

    "vision": (
        "\nAvailable Tools:\n"
        "• Image input: Multimodal model — analyze directly.\n"
        "• MCP text_analyze: For statistical text analysis from transcribed content.\n"
        "• MCP regex_extract: For structured extraction from image text."
    ),

    "general": (
        "\nAvailable Tools:\n"
        "• MCP precision tools in context (mcp_result): adopt directly when present.\n"
        "• Web research results (web_research): use as primary fact source."
    ),

    "agentic_coder": (
        "\nAvailable Code Navigation Tools (MANDATORY — do NOT read entire files!):\n"
        "• repo_map(path, max_depth=3): Skeleton view (paths + classes/functions). ALWAYS first.\n"
        "• read_file_chunked(file_path, start_line, end_line): Paginated reading (max. 50 lines/chunk).\n"
        "• lsp_query(file_path, action, symbol): Signatures, references, completions (.py only).\n"
        "Workflow: repo_map → identify relevant files → read_file_chunked for details → lsp_query for symbols.\n"
        "Use MCP results (repo_map_result, chunk_result) from context directly."
    ),

    # creative_writer, translation: no tool injection (purely linguistic tasks)
}


def get_tool_block(category: str) -> str:
    """Returns the tool block for an expert category.
    Returns '' if no mapping is defined (not an error)."""
    return _TOOL_BLOCKS.get(category, "")


def inject_tools(base_prompt: str, category: str) -> str:
    """Appends the tool block to the base prompt. Idempotent: checks if block is already present."""
    block = get_tool_block(category)
    if not block:
        return base_prompt
    # Prevent double injection
    if "Available Tools:" in base_prompt:
        return base_prompt
    return base_prompt + block
