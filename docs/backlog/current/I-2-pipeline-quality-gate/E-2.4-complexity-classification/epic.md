# E-2.4 Adaptive Komplexitätssteuerung (Cynefin)

Level: Epic
Status: Open

Parent Initiative: I-2 Pipeline Quality Gate Stack (`../initiative.md`)

## Epic Goal

Automatische, deterministisch-heuristische Klassifikation der Anfrage-Komplexität
zur Laufzeit. Das Ergebnis steuert Default-Autonomie-Level und `max_iterations`
ohne separaten LLM-Call.

## Capability / Proof Package

Cynefin-Klassifikation aus Planner-Signalen (keine separaten LLM-Calls):
- `clear`: 1 Subtask, 1 Domain, kein Constitution-Treffer → `auto`
- `complicated`: 2–4 Subtasks, ≤2 Domains, kein Trust-Score-Warning → `auto`
- `complex`: ≥5 Subtasks oder ≥3 Domains oder Trust-Score `proceed-with-assumption` → `supervised`
- `chaotic`: Constitution-`block`-Treffer oder Trust-Score `block` → `supervised` + sofortiger Gate

`supervised`-Modus: Caller bekommt Zwischenbericht mit Plan-Summary und
Bestätigung bevor Expert-Kosten anfallen. API-Parameter `autonomy_override`
erlaubt In-Session-Wechsel.

## Scope

- `services/complexity.py` (neu) — Klassifikations-Heuristik
- `configs/complexity-thresholds.yaml` (neu) — Schwellen konfigurierbar
- Erweiterung `pipeline/state.py` um `complexity_class`, `autonomy_level`
- Integration in `graph/planner.py` nach Plan-Finalisierung
- Erweiterung API-Schema um `autonomy_override`-Parameter

## Dependencies / Preconditions

- E-2.2 abgeschlossen (Trust-Score als Komplexitätssignal)

## Success Semantics

- Anfrage mit ≥5 Subtasks → `complexity_class: complex`, `autonomy_level: supervised` in State
- `supervised`-Modus sendet Zwischenbericht an Caller vor Expert-Dispatch
- Constitution-`block` → `complexity_class: chaotic`, sofortiger Gate

## Failure Semantics

- Klassifikation wirft Exception → fail-open, Default `complicated`, `auto`

## Quality Constraints

- Klassifikation deterministisch, kein LLM-Call, &lt;5ms
- Fail-open

## Exit Criteria

- `pytest tests/ -q` grün
- Anfrage mit 6 Subtasks produziert nachweislich `complex`-Klassifikation

## Source Material

- Wiki `re-loop-interaktionsmodi.md`: `/opt/deployment/adSCAILE_Framework/adSCAILE_shared_tools/adscaile_wiki-main/wiki/re-loop-interaktionsmodi.md`
