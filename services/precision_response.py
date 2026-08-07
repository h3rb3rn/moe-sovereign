"""Deterministic rendering and final evidence binding for precision facts."""

from __future__ import annotations

import re
from decimal import Decimal
from typing import Any, Mapping

from services.pipeline.contracts import (
    _numbered_query_items,
    canonical_json_hash,
    precision_args_match,
)


_SLOT_PATTERN = re.compile(r"\[\[MOE_PRECISION:[^\]]+\]\]")
_GERMAN_MARKERS = re.compile(
    r"\b(?:berechne|bestimme|ermittle|welcher|wochentag|rechne|wandle|"
    r"konvertiere|validiere|größte|gemeinsame|teiler|ggt)\b",
    re.I,
)


def _format_number(value: Any) -> str:
    """Render JSON numeric facts without binary-float presentation noise."""
    if isinstance(value, bool) or not isinstance(value, (int, float, Decimal)):
        raise ValueError("precision_numeric_fact_invalid")
    number = Decimal(str(value))
    rendered = format(number, "f")
    if "." in rendered:
        rendered = rendered.rstrip("0").rstrip(".")
    return rendered or "0"


def _is_german(input_query: str, facts: Mapping[str, Any]) -> bool:
    locale = str(facts.get("locale") or "")
    if locale:
        return locale == "de"
    return bool(_GERMAN_MARKERS.search(input_query or ""))


def render_precision_evidence(
    evidence: Mapping[str, Any],
    input_query: str,
) -> str:
    """Render one schema-validated evidence record using fixed templates."""
    if evidence.get("status") != "completed":
        raise ValueError("precision_evidence_not_completed")
    facts = evidence.get("facts")
    if not isinstance(facts, Mapping):
        raise ValueError("precision_facts_missing")
    tool = str(evidence.get("tool") or "")
    german = _is_german(input_query, facts)

    if tool == "gcd_lcm":
        a = _format_number(facts.get("a"))
        b = _format_number(facts.get("b"))
        operation = str(facts.get("operation") or "")
        if operation == "gcd":
            value = _format_number(facts.get("gcd"))
            return (
                f"Der größte gemeinsame Teiler von {a} und {b} ist {value}."
                if german
                else f"The greatest common divisor of {a} and {b} is {value}."
            )
        if operation == "lcm":
            value = _format_number(facts.get("lcm"))
            return (
                f"Das kleinste gemeinsame Vielfache von {a} und {b} ist {value}."
                if german
                else f"The least common multiple of {a} and {b} is {value}."
            )
        if operation == "both":
            gcd_value = _format_number(facts.get("gcd"))
            lcm_value = _format_number(facts.get("lcm"))
            return (
                f"Für {a} und {b} gilt: GGT = {gcd_value}, KGV = {lcm_value}."
                if german
                else f"For {a} and {b}: GCD = {gcd_value}, LCM = {lcm_value}."
            )
        raise ValueError("precision_gcd_operation_invalid")

    if tool == "unit_convert":
        value = _format_number(facts.get("value"))
        converted = _format_number(facts.get("converted_value"))
        from_unit = str(facts.get("from_unit") or "")
        to_unit = str(facts.get("to_unit") or "")
        if not from_unit or not to_unit:
            raise ValueError("precision_unit_missing")
        return (
            f"{value} {from_unit} entsprechen {converted} {to_unit}."
            if german
            else f"{value} {from_unit} equals {converted} {to_unit}."
        )

    if tool == "calendar_facts":
        date_value = str(facts.get("date") or "")
        weekday = str(facts.get("weekday_name") or "")
        if not date_value or not weekday:
            raise ValueError("precision_calendar_fact_missing")
        return (
            f"Der {date_value} ist ein {weekday}."
            if german
            else f"{date_value} is a {weekday}."
        )

    if tool == "time_facts":
        utc_instant = str(facts.get("utc_instant") or "")
        timezone_name = str(facts.get("timezone") or "")
        local_datetime = str(facts.get("local_datetime") or "")
        offset = str(facts.get("utc_offset") or "")
        abbreviation = str(facts.get("timezone_abbreviation") or "")
        weekday = str(facts.get("weekday_name") or "")
        if not all((utc_instant, timezone_name, local_datetime, offset, weekday)):
            raise ValueError("precision_time_fact_missing")
        suffix = f", {abbreviation}" if abbreviation else ""
        return (
            f"Der Zeitpunkt {utc_instant} entspricht in {timezone_name} "
            f"{local_datetime} (UTC{offset}{suffix}); Wochentag: {weekday}."
            if german
            else f"The instant {utc_instant} corresponds to {local_datetime} in "
            f"{timezone_name} (UTC{offset}{suffix}); weekday: {weekday}."
        )

    if tool == "timezone_convert":
        input_local = str(facts.get("input_local_datetime") or "")
        from_timezone = str(facts.get("from_timezone") or "")
        to_timezone = str(facts.get("to_timezone") or "")
        source_offset = str(facts.get("source_utc_offset") or "")
        target_datetime = str(facts.get("target_datetime") or "")
        target_offset = str(facts.get("target_utc_offset") or "")
        fold = facts.get("fold")
        if not all((input_local, from_timezone, to_timezone, source_offset,
                    target_datetime, target_offset)) or fold not in (0, 1):
            raise ValueError("precision_timezone_fact_missing")
        fold_text = f", Fold {fold}" if facts.get("ambiguous") else ""
        return (
            f"{input_local} in {from_timezone} (UTC{source_offset}{fold_text}) "
            f"entspricht {target_datetime} in {to_timezone} (UTC{target_offset})."
            if german
            else f"{input_local} in {from_timezone} (UTC{source_offset}{fold_text}) "
            f"corresponds to {target_datetime} in {to_timezone} (UTC{target_offset})."
        )

    if tool == "decimal_finance":
        operation = str(facts.get("operation") or "")
        result = str(facts.get("result") or "")
        currency = str(facts.get("currency") or "")
        scale = facts.get("scale")
        rounding = str(facts.get("rounding") or "")
        if not operation or not result or not currency or not isinstance(scale, int) or not rounding:
            raise ValueError("precision_decimal_finance_fact_missing")
        return (
            f"Das Ergebnis der Decimal-Operation {operation} ist {result} {currency} "
            f"(Scale {scale}, Rundung {rounding})."
            if german
            else f"The result of Decimal operation {operation} is {result} {currency} "
            f"(scale {scale}, rounding {rounding})."
        )

    if tool == "exact_probability":
        operation = str(facts.get("operation") or "")
        fraction = str(facts.get("fraction") or "")
        decimal_value = facts.get("decimal")
        if not operation or not re.fullmatch(r"-?\d+/\d+", fraction):
            raise ValueError("precision_probability_fact_missing")
        decimal_suffix = (
            f"; Dezimalprojektion: {decimal_value}"
            if german and decimal_value is not None
            else f"; decimal projection: {decimal_value}"
            if decimal_value is not None
            else ""
        )
        return (
            f"Das exakte Ergebnis für {operation} ist {fraction}{decimal_suffix}."
            if german
            else f"The exact result for {operation} is {fraction}{decimal_suffix}."
        )

    if tool == "structured_validate":
        valid = facts.get("valid")
        format_name = str(facts.get("format") or "").upper()
        payload_hash = str(facts.get("payload_hash") or "")
        errors = facts.get("errors")
        warnings = facts.get("warnings")
        if not isinstance(valid, bool) or not format_name or not re.fullmatch(
            r"[0-9a-f]{64}", payload_hash
        ) or not isinstance(errors, list) or not isinstance(warnings, list):
            raise ValueError("precision_structured_validation_fact_missing")
        state = "gültig" if valid else "ungültig"
        state_en = "valid" if valid else "invalid"
        detail = ""
        if errors:
            first = errors[0] if isinstance(errors[0], Mapping) else {}
            code = str(first.get("code") or "validation_error")
            line = first.get("line")
            position = f", Zeile {line}" if german and line else f", line {line}" if line else ""
            detail = f"; erster Fehler: {code}{position}" if german else f"; first error: {code}{position}"
        warning_text = (
            f"; Warnungen: {len(warnings)}" if german and warnings
            else f"; warnings: {len(warnings)}" if warnings
            else ""
        )
        return (
            f"Die {format_name}-Struktur ist {state} (SHA-256 {payload_hash}){detail}{warning_text}."
            if german
            else f"The {format_name} structure is {state_en} (SHA-256 {payload_hash}){detail}{warning_text}."
        )

    raise ValueError(f"precision_renderer_unsupported:{tool}")


def _binding_material(slots: list[dict[str, Any]]) -> list[dict[str, Any]]:
    return [
        {
            "ordinal": slot.get("ordinal"),
            "source_item": slot.get("source_item"),
            "task_id": slot.get("task_id"),
            "tool": slot.get("tool"),
            "marker": slot.get("marker"),
            "rendered": slot.get("rendered"),
            "contract_hash": slot.get("contract_hash"),
            "result_hash": slot.get("result_hash"),
        }
        for slot in slots
    ]


def precision_binding_hash(slots: list[dict[str, Any]]) -> str:
    """Hash the complete ordered slot-to-evidence mapping."""
    return canonical_json_hash(_binding_material(slots)) if slots else ""


def build_precision_fact_slots(
    state_: Mapping[str, Any],
) -> tuple[list[dict[str, Any]], str, list[str]]:
    """Resolve required intent→task→evidence records into opaque fact slots."""
    required = [
        item
        for item in (state_.get("required_precision_intents") or [])
        if isinstance(item, Mapping)
    ]
    if not required:
        return [], "", []
    plan = [item for item in (state_.get("plan") or []) if isinstance(item, Mapping)]
    current_iteration = int(state_.get("agentic_iteration") or 0)
    evidence = [
        item
        for item in (state_.get("mcp_evidence") or [])
        if isinstance(item, Mapping)
        and int(item.get("iteration") or 0) == current_iteration
    ]
    snapshots = state_.get("precision_contract_snapshot") or {}
    used_task_ids: set[str] = set()
    slots: list[dict[str, Any]] = []
    errors: list[str] = []

    for ordinal, intent in enumerate(required):
        tool = str(intent.get("tool") or "")
        schema = snapshots.get(tool) if isinstance(snapshots, Mapping) else None
        expected_args = intent.get("args") or {}
        matching_tasks = [
            task
            for task in plan
            if str(task.get("id") or "") not in used_task_ids
            and task.get("category") == "precision_tools"
            and task.get("mcp_tool") == tool
            and isinstance(schema, Mapping)
            and precision_args_match(task.get("mcp_args"), expected_args, schema)
        ]
        if not matching_tasks:
            errors.append(f"precision_binding_task_missing:{tool}")
            continue
        task = matching_tasks[0]
        task_id = str(task.get("id") or "")
        used_task_ids.add(task_id)
        matches = [
            item
            for item in evidence
            if str(item.get("task_id") or "") == task_id
            and item.get("status") == "completed"
        ]
        if len(matches) != 1:
            suffix = "missing" if not matches else "duplicate"
            errors.append(f"precision_binding_evidence_{suffix}:{task_id or tool}")
            continue
        item = matches[0]
        try:
            rendered = render_precision_evidence(item, str(state_.get("input") or ""))
        except ValueError as exc:
            errors.append(str(exc))
            continue
        marker_seed = {
            "ordinal": ordinal,
            "task_id": task_id,
            "contract_hash": item.get("contract_hash"),
            "result_hash": item.get("result_hash"),
        }
        marker = f"[[MOE_PRECISION:{ordinal:02d}:{canonical_json_hash(marker_seed)[:20]}]]"
        slots.append(
            {
                "ordinal": ordinal,
                "source_item": intent.get("source_item"),
                "task_id": task_id,
                "tool": tool,
                "marker": marker,
                "rendered": rendered,
                # Retained for a final duplicate-claim check, but never
                # exposed in the value-free marker projection.
                "facts": dict(item.get("facts") or {}),
                "contract_hash": item.get("contract_hash"),
                "result_hash": item.get("result_hash"),
            }
        )

    if errors or len(slots) != len(required):
        return slots, "", errors or ["precision_binding_incomplete"]
    lines = []
    for slot in slots:
        task = next(
            (item for item in plan if str(item.get("id") or "") == slot["task_id"]),
            {},
        )
        label = (
            f"input item {int(slot['source_item']) + 1}"
            if slot.get("source_item") is not None
            else f"precision result {int(slot['ordinal']) + 1}"
        )
        instruction = " ".join(str(task.get("task") or "").split())[:500]
        lines.append(f"- {label} ({instruction}): {slot['marker']}")
    projection = (
        "VERIFIED PRECISION FACT SLOTS:\n"
        "Each opaque marker below represents the complete verified answer for "
        "its labelled request. Preserve every marker byte-for-byte exactly once, "
        "in the listed order. Put each marker alone on its own line, with no "
        "prefix, suffix, numbering or punctuation on that line. Never calculate, "
        "expand, translate, duplicate or restate its value; a deterministic "
        "post-critic binder will replace the complete line.\n"
        + "\n".join(lines)
    )
    return slots, projection, []


def render_direct_precision_response(slots: list[dict[str, Any]]) -> str:
    """Compose one or more already rendered evidence records without an LLM."""
    ordered = sorted(slots, key=lambda item: int(item.get("ordinal") or 0))
    if not ordered:
        raise ValueError("precision_direct_render_missing")
    if len(ordered) == 1:
        return str(ordered[0]["rendered"])
    return "\n".join(
        f"{index}. {slot['rendered']}" for index, slot in enumerate(ordered, 1)
    )


_EXPERT_HEADER_PATTERN = re.compile(r"^\s*\[[^\]]+\]:\s*", re.S)
_EXPERT_CATEGORY_PATTERN = re.compile(r"/\s*([\w-]+)\]:", re.I)
_EXPERT_METADATA_LINE_PATTERN = re.compile(
    r"^\s*(?:#\s*)?(?:CORE_FINDING|CONFIDENCE|GAPS|REFERRAL)\s*:.*$",
    re.I | re.M,
)


def _mixed_expert_body(raw_result: str) -> str:
    """Extract only the user-facing body from one structured expert result."""
    body = _EXPERT_HEADER_PATTERN.sub("", raw_result or "", count=1).strip()
    details = re.search(r"(?:^|\n)\s*DETAILS\s*:\s*\n?(.*)\Z", body, re.I | re.S)
    if details:
        body = details.group(1).strip()
    else:
        body = _EXPERT_METADATA_LINE_PATTERN.sub("", body).strip()
    return body


def compose_mixed_precision_candidate(
    state_: Mapping[str, Any],
    expert_results: list[str],
) -> tuple[str, str, str] | None:
    """Compose a narrow mixed result without exposing facts to a merger LLM.

    The proof is intentionally strict: the input must be a complete numbered
    request with exactly one non-precision item and one matching, non-safety
    expert result. Every other item must already have an ordered precision
    slot. The returned candidate still contains opaque markers; binding remains
    the only operation allowed to reveal their typed values.
    """
    slots = [
        item
        for item in (state_.get("precision_fact_slots") or [])
        if isinstance(item, dict)
    ]
    if not slots or len(expert_results) != 1:
        return None
    if state_.get("precision_binding_errors"):
        return None
    numbered_items = _numbered_query_items(str(state_.get("input") or ""))
    if not numbered_items or len(numbered_items) != len(slots) + 1:
        return None
    source_items = [slot.get("source_item") for slot in slots]
    if any(not isinstance(index, int) for index in source_items):
        return None
    precision_indexes = {int(index) for index in source_items}
    if len(precision_indexes) != len(slots):
        return None
    missing_indexes = set(range(len(numbered_items))) - precision_indexes
    if len(missing_indexes) != 1:
        return None
    non_precision_index = missing_indexes.pop()

    plan = [item for item in (state_.get("plan") or []) if isinstance(item, Mapping)]
    non_precision_tasks = [
        item for item in plan if item.get("category") != "precision_tools"
    ]
    if len(non_precision_tasks) != 1:
        return None
    task = non_precision_tasks[0]
    category = str(task.get("category") or "").lower()
    if not category or category in {"medical_consult", "legal_advisor"}:
        return None
    normalized_task = " ".join(str(task.get("task") or "").split())
    normalized_item = " ".join(numbered_items[non_precision_index].split())
    if normalized_task != normalized_item:
        # The scoped critic may see only the exact non-precision item. A task
        # containing the full mixed input would reopen the precision channel.
        return None
    raw_result = str(expert_results[0] or "")
    result_category = _EXPERT_CATEGORY_PATTERN.search(raw_result)
    if result_category and result_category.group(1).lower() != category:
        return None
    # Custom expert templates may intentionally replace the default structured
    # format and omit an explicit CONFIDENCE line. Use the same central parser
    # as trust/routing: a substantive tagless answer is medium and therefore
    # eligible only with the mandatory scoped critic; short/error/explicit-low
    # results remain excluded.
    from parsing import _parse_expert_confidence

    if _parse_expert_confidence(raw_result) == "low":
        return None
    expert_body = _mixed_expert_body(raw_result)
    if not expert_body or _SLOT_PATTERN.search(expert_body):
        return None
    if _has_unbound_precision_claim(expert_body, slots):
        return None

    slot_by_source = {int(slot["source_item"]): slot for slot in slots}
    parts: list[str] = []
    for item_index in range(len(numbered_items)):
        if item_index == non_precision_index:
            parts.append(expert_body)
        else:
            marker = str(slot_by_source[item_index].get("marker") or "")
            if not marker:
                return None
            parts.append(marker)
    return "\n\n".join(parts), expert_body, str(task.get("task") or numbered_items[non_precision_index])


_CALENDAR_CLAIM_PATTERN = re.compile(
    r"\b(?:montag|dienstag|mittwoch|donnerstag|freitag|samstag|sonntag|"
    r"monday|tuesday|wednesday|thursday|friday|saturday|sunday|"
    r"wochentag|weekday)\b|\b\d{4}-\d{2}-\d{2}\b",
    re.I,
)
_TIME_CLAIM_PATTERN = re.compile(
    r"\b\d{4}-\d{2}-\d{2}T\d{2}:\d{2}(?::\d{2})?"
    r"(?:Z|[+-]\d{2}:\d{2})?\b|"
    r"\b\d{1,2}:\d{2}(?::\d{2})?\b|"
    r"\b\d{1,2}\s*(?:uhr|a\.?m\.?|p\.?m\.?)\b",
    re.I,
)
_GCD_CLAIM_PATTERN = re.compile(
    r"\b(?:ggt|gcd|kgv|lcm|grö(?:ß|ss)te[nr]?\s+gemeinsame[nr]?\s+teiler|"
    r"greatest\s+common\s+divisor|least\s+common\s+multiple)\b",
    re.I,
)
_UNIT_CLAIM_PATTERN = re.compile(
    r"\b(?:km\s*/\s*h|kmh|m\s*/\s*s)\b",
    re.I,
)
_FINANCE_CLAIM_PATTERN = re.compile(
    r"\b(?:eur|usd|gbp|chf|decimal|rundung|rounding|finanz|finance)\b",
    re.I,
)
_PROBABILITY_CLAIM_PATTERN = re.compile(
    r"\b(?:wahrscheinlichkeit|probability|kombination|combination|permutation)\b|"
    r"(?<!\d)-?\d+\s*/\s*\d+(?!\d)",
    re.I,
)
_STRUCTURED_CLAIM_PATTERN = re.compile(
    r"\b(?:json|yaml|xml|csv)\b.{0,80}\b(?:valid|invalid|gültig|ungültig)\b|"
    r"\b(?:valid|invalid|gültig|ungültig)\b.{0,80}\b(?:json|yaml|xml|csv)\b",
    re.I | re.S,
)


def _has_unbound_precision_claim(
    final_response: str,
    slots: list[dict[str, Any]],
) -> bool:
    """Reject a second model-authored precision answer outside its slot."""
    prose = final_response
    for slot in slots:
        prose = prose.replace(str(slot.get("marker") or ""), "")
    for slot in slots:
        tool = str(slot.get("tool") or "")
        facts = slot.get("facts") or {}
        if tool in {"time_facts", "timezone_convert"}:
            if _TIME_CLAIM_PATTERN.search(prose):
                return True
        elif tool == "calendar_facts":
            if _CALENDAR_CLAIM_PATTERN.search(prose):
                return True
        elif tool == "gcd_lcm":
            value = (
                facts.get("gcd")
                if facts.get("operation") == "gcd"
                else facts.get("lcm")
            )
            if _GCD_CLAIM_PATTERN.search(prose) or (
                value is not None
                and re.search(
                    rf"(?<![\w.]){re.escape(str(value))}(?![\w.])", prose
                )
            ):
                return True
        elif tool == "unit_convert":
            value = facts.get("converted_value")
            if _UNIT_CLAIM_PATTERN.search(prose) or (
                value is not None
                and re.search(
                    rf"(?<![\w.]){re.escape(_format_number(value))}(?![\w.])",
                    prose,
                )
            ):
                return True
        elif tool == "decimal_finance":
            result = str(facts.get("result") or "")
            currency = str(facts.get("currency") or "")
            if _FINANCE_CLAIM_PATTERN.search(prose) or (
                result and currency
                and result in prose
                and re.search(rf"\b{re.escape(currency)}\b", prose)
            ):
                return True
        elif tool == "exact_probability":
            if _PROBABILITY_CLAIM_PATTERN.search(prose):
                return True
        elif tool == "structured_validate":
            if _STRUCTURED_CLAIM_PATTERN.search(prose):
                return True
    return False


def bind_precision_response(state_: Mapping[str, Any]) -> dict[str, Any]:
    """Bind the post-critic response to typed evidence, or fail closed."""
    required = state_.get("required_precision_intents") or []
    if not required:
        return {
            "precision_binding_status": "not_required",
            "precision_binding_errors": [],
            "precision_binding_hash": "",
            "precision_bound_response_hash": "",
        }
    slots = [
        item
        for item in (state_.get("precision_fact_slots") or [])
        if isinstance(item, dict)
    ]
    errors = list(state_.get("precision_binding_errors") or [])
    if errors or len(slots) != len(required):
        return {
            "final_response": "",
            "precision_binding_status": "failed",
            "precision_binding_errors": errors or ["precision_binding_incomplete"],
            "precision_binding_hash": precision_binding_hash(slots),
            "precision_bound_response_hash": "",
        }

    final_response = str(state_.get("final_response") or "")
    if state_.get("precision_direct"):
        expected = str(state_.get("precision_rendered_response") or "")
        if not expected or final_response != expected:
            return {
                "final_response": "",
                "precision_binding_status": "failed",
                "precision_binding_errors": ["precision_direct_response_mismatch"],
                "precision_binding_hash": precision_binding_hash(slots),
                "precision_bound_response_hash": "",
            }
        bound = expected
    else:
        expected_markers = [str(slot.get("marker") or "") for slot in slots]
        found_markers = _SLOT_PATTERN.findall(final_response)
        unknown = [marker for marker in found_markers if marker not in expected_markers]
        if unknown:
            errors.append("precision_binding_unknown_slot")
        for marker in expected_markers:
            count = final_response.count(marker)
            if count == 0:
                errors.append("precision_binding_slot_missing")
            elif count != 1:
                errors.append("precision_binding_slot_duplicate")
            elif not any(line.strip() == marker for line in final_response.splitlines()):
                # The renderer emits a complete semantic statement.  Requiring
                # an isolated line prevents a model from wrapping that sentence
                # in a conflicting unit/date/value context (or producing text
                # such as "the result is <complete sentence>").
                errors.append("precision_binding_slot_context")
        positions = [final_response.find(marker) for marker in expected_markers]
        if all(position >= 0 for position in positions) and positions != sorted(positions):
            errors.append("precision_binding_slot_order")
        if _has_unbound_precision_claim(final_response, slots):
            errors.append("precision_binding_unbound_restatement")
        if errors:
            return {
                "final_response": "",
                "precision_binding_status": "failed",
                "precision_binding_errors": list(dict.fromkeys(errors)),
                "precision_binding_hash": precision_binding_hash(slots),
                "precision_bound_response_hash": "",
            }
        bound = final_response
        for slot in slots:
            bound = bound.replace(str(slot["marker"]), str(slot["rendered"]), 1)
        if _SLOT_PATTERN.search(bound):
            return {
                "final_response": "",
                "precision_binding_status": "failed",
                "precision_binding_errors": ["precision_binding_unresolved_slot"],
                "precision_binding_hash": precision_binding_hash(slots),
                "precision_bound_response_hash": "",
            }

    return {
        "final_response": bound,
        "precision_binding_status": "bound",
        "precision_binding_errors": [],
        "precision_binding_hash": precision_binding_hash(slots),
        "precision_bound_response_hash": canonical_json_hash(bound),
    }
