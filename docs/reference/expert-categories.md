# Expert Categories & Models

Detailed description of all 12 expert categories with tool injection and model recommendations.

## general — General Knowledge

**Tier:** T2
**Use:** General knowledge questions, definitions, explanations, summaries

**Tool injection:** none

**Typical models:**
- T2: `gemma3:27b`, `qwen3.5:35b`, `llama3.3:70b`

**System prompt focus:** Balanced, informative answers; no subject-area bias.

---

## math — Mathematics

**Tier:** T1 → T2 (at CONFIDENCE < 0.65)
**Use:** Calculations, algebraic equations, statistics, geometry

**Tool injection:**
- `calculate` – safe arithmetic evaluation
- `solve_equation` – SymPy-based equation solver
- `prime_factorize` – prime factorization
- `statistics_calc` – mean, median, standard deviation

**Typical models:**
- T1: `phi4:14b`
- T2: `qwq:32b` (reasoning specialist)

**Note:** MCP tools are injected for exact calculations before the expert responds.

---

## technical_support — IT & DevOps

**Tier:** T1 → T2
**Use:** System administration, Docker, Kubernetes, Linux, networking, DevOps

**Tool injection:**
- `subnet_calc` – CIDR/netmask analysis
- `regex_extract` – pattern matching in log files

**Typical models:**
- T1: `deepseek-coder-v2:16b`
- T2: `devstral:24b`

---

## code_reviewer — Code Analysis

**Tier:** T2
**Use:** Code review, security analysis, refactoring recommendations, bug hunting

**Tool injection:**
- `json_query` – structural analysis of configuration files
- `regex_extract` – pattern matching in code

**Typical models:**
- T2: `devstral:24b`, `qwen3-coder:30b`

**Note:** OWASP Top 10 as mandatory checklist in the system prompt.

---

## creative_writer — Creative Writing

**Tier:** T2
**Use:** Text creation, marketing copy, storytelling, blog articles, emails

**Tool injection:** none

**Typical models:**
- T2: `gemma3:27b`, `qwen3.5:35b`

---

## medical_consult — Medicine

**Tier:** T1 → T2
**Use:** Medical information, symptom explanations, medication info

**Tool injection:** none

**Typical models:**
- T1: `phi4:14b`
- T2: `gemma3:27b`

**Note:** Critic node checks medical claims for accuracy;
mandatory note recommending professional medical consultation.

!!! warning "Disclaimer"
    Medical information does not replace professional medical advice.

---

## legal_advisor — Law

**Tier:** T2
**Use:** German law, BGB, StGB, HGB, contract law, criminal law

**Tool injection:**
- `legal_search_laws` – search for laws
- `legal_get_paragraph` – retrieve paragraph text (exact text)
- `legal_fulltext_search` – full-text search in statutory texts

**Typical models:**
- T2: `magistral:24b` (law specialist), `command-r:35b`

**Note:** Critic node checks for correct paragraph citations;
MCP tools provide exact statutory texts.

!!! warning "Disclaimer"
    Legal information does not replace professional legal advice.

---

## translation — Translation

**Tier:** T2
**Use:** Professional translations DE↔EN↔FR↔ES↔IT

**Tool injection:** none

**Typical models:**
- T2: `translategemma:27b`, `qwen3.5:35b`

---

## data_analyst — Data Analysis

**Tier:** T1
**Use:** Statistics, pandas code, SQL queries, data visualization

**Tool injection:**
- `statistics_calc` – statistical metrics
- `json_query` – dataset analysis

**Typical models:**
- T1: `phi4:14b`

**Note:** Fast-path preferred, as requests are often clearly structured.

---

## science — Natural Science

**Tier:** T2
**Use:** Chemistry, biology, physics, astronomy, climate research

**Tool injection:** none

**Typical models:**
- T2: `gemma3:27b`

---

## reasoning — Logic & Analysis

**Tier:** T1 → T2
**Use:** Complex logic problems, strategic analysis, argumentation structure

**Tool injection:** none

**Typical models:**
- T1: `phi4:14b`
- T2: `deepseek-r1:32b` (reasoning chain specialist)

**Note:** Thinking node is always activated for `complex` queries.

---

## vision — Image Analysis

**Tier:** T2
**Use:** Image analysis, screenshot evaluation, document recognition, OCR support

**Tool injection:** none

**Typical models:**
- T2: Multimodal models (e.g. `gemma3:27b`, `llava:34b`)

**Note:** Base64-encoded images are embedded directly in the request.

```python
# Image request example
import base64

with open("screenshot.png", "rb") as f:
    b64 = base64.b64encode(f.read()).decode()

payload = {
    "model": "moe-orchestrator",
    "messages": [{
        "role": "user",
        "content": [
            {"type": "text", "text": "Describe this image"},
            {"type": "image_url", "image_url": {"url": f"data:image/png;base64,{b64}"}}
        ]
    }]
}
```
