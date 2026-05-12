"""
prompts.py — Static prompt text constants for the orchestrator.

These are immutable string templates appended to LLM prompts at runtime.
Per-template overrides (planner_prompt, judge_prompt) live in expert_templates.json.
"""


# Instruction appended to the merger_node system prompt to trigger synthesis persistence.
# The LLM is asked to append a tagged JSON block only for genuinely novel insights.
SYNTHESIS_PERSISTENCE_INSTRUCTION = (
    "\n\nSYNTHESIS PERSISTENCE: If your response contains a novel multi-source comparison, "
    "logical inference, or non-trivial synthesis (not a simple factual lookup), append "
    "exactly ONE block at the very end of your response:\n"
    "<SYNTHESIS_INSIGHT>\n"
    '{"summary": "<one concise sentence>", '
    '"entities": ["entity1", "entity2"], '
    '"insight_type": "comparison|synthesis|inference"}\n'
    "</SYNTHESIS_INSIGHT>\n"
    "Omit this block entirely for direct factual answers or simple retrievals."
)


# Instruction appended to merger prompt to trigger inline source attribution.
# Tags factual claims derived from the knowledge graph for provenance tracking.
PROVENANCE_INSTRUCTION = (
    "\n\nSOURCE ATTRIBUTION: When your answer includes a factual claim that comes "
    "directly from the Knowledge Graph section above, mark it with [REF:entity_name] "
    "immediately after the claim. Use the exact entity name from the graph context. "
    "Only tag claims derived from the graph — do not tag general knowledge or web results. "
    "Keep tags minimal (max 5 per response)."
)


# Default role instruction for the Planner LLM.
# Can be overridden per Expert Template via planner_prompt field.
DEFAULT_PLANNER_ROLE = (
    "You are the orchestrator of a Mixture-of-Experts system.\n"
    "Decompose the following request into 1–4 subtasks.\n\n"
    "Mandatorily extract all numerical constraints and technical parameters from the request "
    "(e.g. model sizes, MTU values, protocol overheads, chemical doses, bitrates). "
    "Integrate these as IMMUTABLE_CONSTANTS directly into each subtask description for the experts, "
    "so experts cannot hallucinate default values."
)


# ─── Routing detection regexes ───────────────────────────────────────────────
# Used by _build_filtered_tool_desc (in main.py) to pick MCP tool groups
# and by graph_rag_node to detect public-fact queries.

import re as _re

_RESEARCH_DETECT = _re.compile(
    r'\b(paper|article|study|studies|journal|published|author|researcher|professor|'
    r'arxiv|doi|isbn|pubchem|orcid|database|dataset|classification|compound|'
    r'species|genus|wikipedia|museum|collection|archive|standard|transcript|'
    r'video|episode|season|channel|github|issue|repo)\b', _re.I,
)
_LEGAL_DETECT = _re.compile(
    r'\b(§+\s*\d+|bgh|bverfg|bfh|bsg|bgh|hgb|bdb|stvo|dsgvo|gdpr|'
    r'vertrag|gesetz|recht|klage|straf|gmbh|ag\b|ug\b|insolvenz|'
    r'law|legal|statute|regulation|compliance|contract|court)\b', _re.I,
)
_DATA_DETECT = _re.compile(
    r'\b(berechne?|calculate|compute|average|median|stdev|hash|base64|'
    r'regex|cidr|subnet|subnet|ip.address|convert|unit|statistic|prozent|percent)\b', _re.I,
)
_FILE_DETECT = _re.compile(
    r'\b(attachment|datei|file|upload|image|foto|bild|pdf|spreadsheet|csv|graph|ontology)\b', _re.I,
)


# ─── Semantic-router prototype queries ───────────────────────────────────────
# Stored once at startup in ChromaDB by _seed_task_type_prototypes.
# New categories from EXPERT_MODELS are automatically included alongside these.
_ROUTE_PROTOTYPES: dict[str, list[str]] = {
    "math": [
        "Calculate the integral of x² dx",
        "What is the solution to 3x + 5 = 20?",
        "Calculate the square root of 144",
        "What is 15% of 280?",
        "Solve the quadratic equation x²-4x+3=0",
    ],
    "code_reviewer": [
        "Check this Python code for bugs",
        "What is wrong with this JavaScript function?",
        "Optimize this SQL query",
        "Refactor this C++ code",
        "Find security vulnerabilities in this PHP script",
    ],
    "technical_support": [
        "How do I install Docker on Ubuntu?",
        "Configure Nginx as a reverse proxy",
        "Explain how Kubernetes works",
        "How do I set up an SSL certificate?",
        "What is the difference between TCP and UDP?",
    ],
    "medical_consult": [
        "What are the side effects of ibuprofen?",
        "What are the symptoms of a heart attack?",
        "How is type 2 diabetes treated?",
        "Interactions between metformin and aspirin",
        "What does elevated blood pressure mean?",
    ],
    "legal_advisor": [
        "What does §242 BGB regulate?",
        "How does the right of termination work in Germany?",
        "What are my rights as a tenant under tenancy law?",
        "Explain the GDPR principles",
        "What is a restraining order?",
    ],
    "creative_writer": [
        "Write a short story about a robot",
        "Write a poem about autumn",
        "Create a creative product description text",
        "Write a dialogue script for a scene",
    ],
    "research": [
        "Research the latest developments in quantum computing",
        "Summarize the current state of AI research",
        "What are the latest climate research findings?",
        "Analyze the economic situation in Germany 2024",
    ],
    "precision_tools": [
        "Calculate the SHA256 hash of 'hello world'",
        "Convert 100 km/h to m/s",
        "What is the difference in days between 01/01/2020 and 07/15/2024?",
        "Which subnets does 192.168.1.0/24 contain?",
        "Extract all email addresses from this text",
    ],
    "general": [
        "What is the capital of France?",
        "Explain the concept of the theory of relativity to me",
        "How did the universe originate?",
        "What is the difference between AI and ML?",
    ],
}


# ─── Confidence format snippets ──────────────────────────────────────────────
# Appended to expert system prompts by _conf_format_for_mode() in services/helpers.py.
_CONF_FORMAT_DEFAULT = (
    "\n\nAlways structure your answer EXACTLY in this format:\n"
    "CORE_FINDING: [1-2 sentence main statement]\n"
    "CONFIDENCE: high | medium | low\n"
    "  high = established expert knowledge, clear source situation\n"
    "  medium = domain knowledge available, exceptions or nuances possible\n"
    "  low = data gaps, outdated knowledge, genuine uncertainty\n"
    "GAPS: [open sub-questions for other experts | none]\n"
    "REFERRAL: [expert category if handoff needed | —]\n"
    "DETAILS:\n"
    "[full answer here]"
)
_CONF_FORMAT_CODE = (
    "\n\nInsert a comment as the very first line:\n"
    "# CONFIDENCE: high | medium | low\n"
    "Then ONLY source code."
)
_CONF_FORMAT_CONCISE = (
    "\n\nBegin with: CONFIDENCE: high | medium | low — then your brief answer."
)


# ─── Default expert system prompts ───────────────────────────────────────────
# Last-resort fallback when no template system prompt and no CUSTOM_EXPERT_PROMPTS
# entry exists for the category. Priority: template._system_prompt >
# _CUSTOM_EXPERT_PROMPTS[cat] > DEFAULT_EXPERT_PROMPTS[cat] > DEFAULT_EXPERT_PROMPTS["general"]
DEFAULT_EXPERT_PROMPTS: dict[str, str] = {
    "general": (
        "You are a versatile, fact-based expert. "
        "Answer precisely, in a structured manner. "
        "Stick to verifiable facts."
    ),
    "math": (
        "You are a mathematics and physics expert. "
        "Always show the complete solution steps. "
        "Use LaTeX notation for formulas. "
        "Verify your result by back-substitution."
    ),
    "technical_support": (
        "You are an experienced IT engineer and DevOps specialist. "
        "Answer with concrete, executable solution steps. "
        "Name relevant commands, configurations and error codes."
    ),
    "creative_writer": (
        "You are a creative author and copywriter. "
        "Write vividly, originally and with stylistic confidence. "
        "Adapt tone and register to the context."
    ),
    "code_reviewer": (
        "You are a senior software engineer focused on code quality and security. "
        "Identify bugs, security vulnerabilities, performance issues and improvement potential. "
        "Return concrete, improved code and explain why."
    ),
    "medical_consult": (
        "You are an experienced physician. "
        "Provide well-founded, objective medical information based on current guidelines. "
        "Always clearly emphasize that consulting a doctor is essential."
    ),
    "legal_advisor": (
        "You are an experienced lawyer specializing in German law. "
        "Explain the legal situation clearly, in a structured manner, with reference to relevant laws (§§). "
        "Point out the necessity of individual legal advice."
    ),
    "translation": (
        "You are a professional translator with native-level proficiency in German, English, French and Spanish. "
        "Translate precisely, idiomatically and faithfully to context. "
        "Preserve the tone, register and technical terminology of the original. "
        "Note cultural particularities when relevant."
    ),
    "reasoning": (
        "You are an analytical thinker specialized in complex multi-step problems. "
        "Decompose problems into explicit sub-steps, show your chain of thought. "
        "Explicitly name assumptions, uncertainties and alternative interpretations. "
        "Arrive at a clear, well-reasoned conclusion."
    ),
    "vision": (
        "You are a vision AI expert for image and document analysis. "
        "Describe content, context and relevant details systematically and in a structured manner. "
        "For text in images: transcribe completely and verbatim. "
        "For diagrams/charts: extract data points and explain the message. "
        "For screenshots: identify UI elements, errors and states precisely."
    ),
    "data_analyst": (
        "You are a data science and data analysis expert. "
        "Analyze data structures, patterns and relationships with statistical precision. "
        "Write Python code (pandas, numpy, matplotlib/seaborn) when visualization or transformation is requested. "
        "Interpret results and name statistical limitations."
    ),
    "science": (
        "You are a natural scientist with expertise in chemistry, biology, physics and environmental sciences. "
        "Explain concepts precisely based on current research and recognized theories. "
        "Distinguish established knowledge from active research areas. "
        "Use correct technical terminology; explain foreign terms at first occurrence."
    ),
    "agentic_coder": (
        "You are a context manager for code tasks on systems with limited VRAM. "
        "ABSOLUTE RULE: NEVER read entire files. Context window: 4096–8192 tokens. "
        "Mandatory workflow: 1) repo_map → overview, 2) read_file_chunked → targeted max. 50 lines, "
        "3) lsp_query → signatures/references. Plan first, then read minimally. "
        "Answer with code and line numbers, no filler text."
    ),
    "research": (
        "You are a thorough research analyst. "
        "Synthesize information from multiple perspectives, cite sources when available, "
        "and clearly distinguish between established facts and emerging findings."
    ),
    "precision_tools": (
        "You are a precision computation assistant. "
        "Execute requested calculations, conversions, or data transformations exactly. "
        "Show your work and validate the output before presenting results."
    ),
}
