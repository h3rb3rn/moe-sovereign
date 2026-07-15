"""
services/kpi_system.py — Hierarchisches KPI-System nach Gregg/FMEA-Konzept.

3-stufiges Schema (analog Johannes Gregg, ProSYS QM-Kennzahlengenerator, 2018):

  KSP (Sekundärprozess-Kennzahl) — Blattebene, gemessen aus routing_telemetry
    KSP1  Tool-Call-Erfolgsrate
    KSP2  Format-Konformität (kein Judge-Refinement nötig)
    KSP3  Timeout-Freiheit
    KSP4  Self-Score-Qualität
    KSP5  User-Rating-Zufriedenheit
    KSP6  SLA-Einhaltung (< 30 s)
    KSP7  Cache-Nutzungsrate
    KSP8  Expert-Abdeckung
    KSP9  Keine-Korrektur-Rate

  KPP (Primärprozess-Kennzahl) — Mittelwert zugehöriger KSPs
    KPP1  Modell-Zuverlässigkeit  = (KSP1 + KSP2 + KSP3) / 3
    KPP2  Antwort-Qualität        = (KSP4 + KSP5) / 2
    KPP3  Infrastruktur-Effizienz = (KSP6 + KSP7) / 2
    KPP4  Pipeline-Gesundheit     = (KSP8 + KSP9) / 2

  KHP (Hilfsprozess-Kennzahl) — Verdichtung
    KHP1  System-Gesamtqualität    = (KPP1 + KPP2 + KPP3 + KPP4) / 4
    KHP2  Risiko-/Chancen-Bewertung = KHP1 × Gewichtungsfaktor

Einheitsformel (nach Gregg): KQ = (A − B) / A × 100 %
  Hoher Wert = gut. Einheitliche Lesart über alle Metriken.

Traffic-Light-Schwellen (ISO 9001-konform, nach Gregg):
  ≥ 90 %  Grün   — Chance
  60–89 % Gelb   — Handlungsbedarf
  < 60 %  Rot    — Unverzüglicher Handlungsbedarf

RPZ-Tabelle (FMEA-Methode, Selda Sevik 2016):
  RPZ = A × B × E  (je Faktor 1–10, hoher Wert = dringlichere Maßnahme)
    A = Auftretenswahrscheinlichkeit
    B = Bedeutung/Schwere
    E = Entdeckungswahrscheinlichkeit (10 = kaum erkennbar)
"""

from __future__ import annotations

import logging
from dataclasses import dataclass, field
from typing import Optional

logger = logging.getLogger("moe.kpi")

# ---------------------------------------------------------------------------
# Konfiguration
# ---------------------------------------------------------------------------

_TRAFFIC_GREEN  = 90.0   # ≥ 90 % Chance
_TRAFFIC_YELLOW = 60.0   # 60–89 % Handlungsbedarf; < 60 % sofortiger Bedarf

_WINDOW_MAP = {
    "1d":  "1 day",
    "7d":  "7 days",
    "30d": "30 days",
    "90d": "90 days",
}

# SLA-Schwelle für schnelle Antworten
_SLA_FAST_MS   = 30_000   # 30 s
_TIMEOUT_MS    = 300_000  # 5 min

# Gewichtungsfaktoren für KHP2 (Risiko-/Chancen-Bewertung)
# Analogie zur ABC-Analyse nach Gregg:
#   Kritisch = höchste Workloads/Nutzer → strengerer Faktor → niedrigerer Score bei gleicher Qualität
_COMPLEXITY_WEIGHT = {
    "complex":  0.7,   # A-Klasse: hohe Bedeutung
    "moderate": 0.8,   # B-Klasse
    "simple":   0.9,   # C-Klasse
}
_DEFAULT_WEIGHT = 0.85


# ---------------------------------------------------------------------------
# RPZ-Tabelle: Bekannte Fehlermodi
# ---------------------------------------------------------------------------

@dataclass
class RpzEntry:
    name: str
    description: str
    a: int        # Auftretenswahrscheinlichkeit 1–10
    b: int        # Bedeutung/Schwere 1–10
    e: int        # Entdeckungswahrscheinlichkeit 1–10 (10=kaum erkennbar)
    status: str   # Unbearbeitet | In Bearbeitung | Abgeschlossen | Überwacht | Verworfen
    rpz: int = field(init=False)

    def __post_init__(self) -> None:
        self.rpz = self.a * self.b * self.e


KNOWN_FAILURE_MODES: list[RpzEntry] = [
    RpzEntry(
        name="Tool-Call leere Argumente",
        description="Modell generiert Tool-Call ohne Arguments-Payload (leeres JSON-Objekt)",
        a=5, b=8, e=4,
        status="Abgeschlossen",   # Text-Mode Tool-Calling behebt dies
    ),
    RpzEntry(
        name="SSE Stream Drop (NAT-Timeout)",
        description="SSE-Verbindung bricht bei langen Generierungen über heterogene Netzwerke ab",
        a=3, b=9, e=3,
        status="Abgeschlossen",   # 10s-Ping-Mechanismus implementiert
    ),
    RpzEntry(
        name="Ollama Format-Fehler (kein gültiges JSON)",
        description="Modell gibt Prosa statt strukturiertes JSON trotz format-Erzwingung",
        a=6, b=7, e=5,
        status="Überwacht",
    ),
    RpzEntry(
        name="Modell-Timeout (> 5 min)",
        description="Inferenz überschreitet Timeout-Schwelle, Anfrage bricht ab",
        a=3, b=8, e=6,
        status="Überwacht",
    ),
    RpzEntry(
        name="Expert VRAM-Ausfall",
        description="GPU-Speicher reicht nicht für Modell, Expert-Call schlägt fehl",
        a=4, b=7, e=4,
        status="Überwacht",
    ),
    RpzEntry(
        name="Context-Length-Überschreitung",
        description="Konversationshistorie übersteigt num_ctx, Token werden abgeschnitten",
        a=5, b=6, e=7,
        status="In Bearbeitung",
    ),
    RpzEntry(
        name="Fehlender Tool-Call-Parse (bare-args)",
        description="Modell gibt Argumente ohne Tool-Name-Wrapper zurück",
        a=4, b=6, e=5,
        status="Abgeschlossen",   # Fallback-Parser implementiert
    ),
]


# ---------------------------------------------------------------------------
# Datenklassen
# ---------------------------------------------------------------------------

def _traffic(value: Optional[float]) -> str:
    if value is None:
        return "no_data"
    if value >= _TRAFFIC_GREEN:
        return "green"
    if value >= _TRAFFIC_YELLOW:
        return "yellow"
    return "red"


@dataclass
class KspResult:
    key: str
    name: str
    value: Optional[float]    # Prozent, None = keine Daten
    sample_count: int
    traffic: str = field(init=False)

    def __post_init__(self) -> None:
        self.traffic = _traffic(self.value)

    def to_dict(self) -> dict:
        return {
            "key":          self.key,
            "name":         self.name,
            "value_pct":    round(self.value, 2) if self.value is not None else None,
            "sample_count": self.sample_count,
            "traffic":      self.traffic,
        }


@dataclass
class KppResult:
    key: str
    name: str
    ksp_keys: list[str]
    value: Optional[float]
    traffic: str = field(init=False)

    def __post_init__(self) -> None:
        self.traffic = _traffic(self.value)

    def to_dict(self) -> dict:
        return {
            "key":       self.key,
            "name":      self.name,
            "ksp_keys":  self.ksp_keys,
            "value_pct": round(self.value, 2) if self.value is not None else None,
            "traffic":   self.traffic,
        }


@dataclass
class KhpResult:
    key: str
    name: str
    value: Optional[float]
    traffic: str = field(init=False)

    def __post_init__(self) -> None:
        self.traffic = _traffic(self.value)

    def to_dict(self) -> dict:
        return {
            "key":       self.key,
            "name":      self.name,
            "value_pct": round(self.value, 2) if self.value is not None else None,
            "traffic":   self.traffic,
        }


@dataclass
class KpiSnapshot:
    window:      str
    total_rows:  int
    ksps:        dict[str, KspResult]
    kpps:        dict[str, KppResult]
    khp1:        KhpResult
    khp2:        KhpResult
    rpz_entries: list[dict]

    def to_dict(self) -> dict:
        return {
            "window":     self.window,
            "total_rows": self.total_rows,
            "schema": {
                "KSP": {k: v.to_dict() for k, v in self.ksps.items()},
                "KPP": {k: v.to_dict() for k, v in self.kpps.items()},
                "KHP": {
                    "KHP1": self.khp1.to_dict(),
                    "KHP2": self.khp2.to_dict(),
                },
            },
            "rpz_table": self.rpz_entries,
            "thresholds": {
                "green":  f">= {_TRAFFIC_GREEN} %",
                "yellow": f"{_TRAFFIC_YELLOW}–{_TRAFFIC_GREEN} %",
                "red":    f"< {_TRAFFIC_YELLOW} %",
            },
        }


# ---------------------------------------------------------------------------
# SQL
# ---------------------------------------------------------------------------

# Hauptabfrage aus usage_log — vollständig befüllte Spalten für alle Request-Modi.
# Die usage_log enthält alle Requests (native, cc_tool, default/orchestrator).
# routing_telemetry ist nur für den default-Orchestrator-Pfad befüllt (sparse JOIN).
_MAIN_AGGREGATE_SQL = """
SELECT
    COUNT(*)                                                            AS total_requests,

    -- KSP2: Erfolgsrate (status-Feld in usage_log, alle Modi)
    SUM(CASE WHEN ul.status = 'ok' THEN 1 ELSE 0 END)                  AS success_count,

    -- KSP3: Orchestrator-Aktivierungsrate (moe_mode='default' = voller LangGraph-Pfad)
    SUM(CASE WHEN ul.moe_mode = 'default' THEN 1 ELSE 0 END)           AS orchestrator_count,

    -- KSP6: Cache-Nutzungsrate (usage_log.cache_hit ist vollständig befüllt)
    SUM(CASE WHEN ul.cache_hit = TRUE THEN 1 ELSE 0 END)               AS cache_hit_count,

    -- KSP7: Token-Erfolgsrate (total_tokens > 0 = tatsächliche Inferenz stattgefunden)
    SUM(CASE WHEN ul.total_tokens > 0 THEN 1 ELSE 0 END)               AS has_tokens_count,

    -- Routing-Telemetry-Metriken (nur für Orchestrator-Requests befüllt):
    -- KSP4: Self-Score-Qualität
    AVG(CASE WHEN rt.self_score IS NOT NULL
             THEN rt.self_score::FLOAT / 5.0 * 100.0 END)              AS avg_self_score_pct,
    COUNT(rt.self_score)                                                AS self_score_count,

    -- KSP5: User-Rating-Zufriedenheit
    AVG(CASE WHEN rt.user_rating IS NOT NULL
             THEN rt.user_rating::FLOAT / 5.0 * 100.0 END)             AS avg_user_rating_pct,
    COUNT(rt.user_rating)                                               AS user_rating_count,

    -- KSP8: Format-Konformität (kein Judge-Refinement nötig)
    COUNT(rt.judge_refined)                                             AS rt_total,
    SUM(CASE WHEN rt.judge_refined = FALSE OR rt.judge_refined IS NULL
             THEN 1 ELSE 0 END)                                         AS no_judge_count,

    -- KSP9: Expert-Abdeckung (Orchestrator-Requests mit Experten)
    SUM(CASE WHEN rt.fast_path = FALSE
              AND rt.experts_used IS NOT NULL
              AND array_length(rt.experts_used, 1) > 0
             THEN 1 ELSE 0 END)                                         AS expert_covered_count,
    SUM(CASE WHEN rt.fast_path = FALSE THEN 1 ELSE 0 END)              AS non_fast_path_count

FROM usage_log ul
LEFT JOIN routing_telemetry rt ON ul.request_id = rt.response_id
WHERE ul.requested_at::timestamptz >= NOW() - INTERVAL '{interval}'
  AND ul.user_id IS NOT NULL
"""

# Separate Abfrage für Tool-Call-Metriken aus JSONB (KSP1)
_TOOL_CALLS_SQL = """
SELECT
    COUNT(*)                                                    AS total_calls,
    COUNT(*) FILTER (WHERE tc->>'status' = 'error')            AS error_calls
FROM usage_log ul
INNER JOIN routing_telemetry rt ON ul.request_id = rt.response_id,
LATERAL jsonb_array_elements(rt.tool_calls_log) AS tc
WHERE ul.requested_at::timestamptz >= NOW() - INTERVAL '{interval}'
  AND rt.tool_calls_log IS NOT NULL
  AND jsonb_array_length(rt.tool_calls_log) > 0
"""


# ---------------------------------------------------------------------------
# KPI-Berechnung
# ---------------------------------------------------------------------------

def _avg_of(*values: Optional[float]) -> Optional[float]:
    """Mittelwert nur aus vorhandenen (nicht-None) Werten."""
    valid = [v for v in values if v is not None]
    return sum(valid) / len(valid) if valid else None


def _rate(good: Optional[int], total: Optional[int]) -> Optional[float]:
    """Einheitsformel: (A - B) / A × 100 — gibt None wenn keine Daten."""
    if total is None or total == 0:
        return None
    if good is None:
        good = 0
    return max(0.0, min(100.0, good / total * 100.0))


async def compute_kpis(pool, window: str = "7d") -> KpiSnapshot:
    """Berechnet das vollständige KPI-Schema für den angegebenen Zeitfenster.

    Args:
        pool: asyncpg-Verbindungspool (moe_userdb)
        window: Zeitfenster — "1d", "7d", "30d" oder "90d"

    Returns:
        KpiSnapshot mit allen KSPs, KPPs, KHPs und der RPZ-Tabelle.
    """
    interval = _WINDOW_MAP.get(window, "7 days")

    main_sql   = _MAIN_AGGREGATE_SQL.format(interval=interval, timeout=_TIMEOUT_MS, sla_fast=_SLA_FAST_MS)
    tool_sql   = _TOOL_CALLS_SQL.format(interval=interval)

    row = None
    tool_row = None
    try:
        async with pool.connection() as conn:
            cur = await conn.execute(main_sql)
            row = await cur.fetchone()
    except Exception as exc:
        logger.warning("kpi main query failed: %s", exc)

    try:
        async with pool.connection() as conn:
            cur = await conn.execute(tool_sql)
            tool_row = await cur.fetchone()
    except Exception as exc:
        logger.warning("kpi tool_calls query failed: %s", exc)

    # ── Rohdaten auslesen ────────────────────────────────────────────────────
    total              = int(row[0])   if row and row[0]  is not None else 0
    success_count      = int(row[1])   if row and row[1]  is not None else 0
    orchestrator_count = int(row[2])   if row and row[2]  is not None else 0
    cache_hits         = int(row[3])   if row and row[3]  is not None else 0
    has_tokens_count   = int(row[4])   if row and row[4]  is not None else 0
    avg_self_score_pct = float(row[5]) if row and row[5]  is not None else None
    self_score_n       = int(row[6])   if row and row[6]  is not None else 0
    avg_user_rat_pct   = float(row[7]) if row and row[7]  is not None else None
    user_rating_n      = int(row[8])   if row and row[8]  is not None else 0
    rt_total           = int(row[9])   if row and row[9]  is not None else 0
    no_judge_count     = int(row[10])  if row and row[10] is not None else 0
    expert_covered     = int(row[11])  if row and row[11] is not None else 0
    non_fast_path      = int(row[12])  if row and row[12] is not None else 0

    total_tool_calls   = int(tool_row[0]) if tool_row and tool_row[0] is not None else 0
    error_tool_calls   = int(tool_row[1]) if tool_row and tool_row[1] is not None else 0

    # ── KSP-Berechnung ──────────────────────────────────────────────────────
    # KSP1: Tool-Call-Erfolgsrate (aus routing_telemetry JSONB — sparse)
    ksp1_val = _rate(total_tool_calls - error_tool_calls, total_tool_calls)
    # KSP2: Anfrage-Erfolgsrate (usage_log.status = 'ok' — vollständig)
    ksp2_val = _rate(success_count, total)
    # KSP3: Orchestrator-Aktivierungsrate (moe_mode='default' — vollständig)
    ksp3_val = _rate(orchestrator_count, total)
    # KSP4: Self-Score-Qualität (routing_telemetry — sparse, nur Orchestrator-Pfad)
    ksp4_val = avg_self_score_pct
    # KSP5: User-Rating-Zufriedenheit (routing_telemetry — sehr sparse)
    ksp5_val = avg_user_rat_pct
    # KSP6: Cache-Nutzungsrate (usage_log.cache_hit — vollständig)
    ksp6_val = _rate(cache_hits, total)
    # KSP7: Token-Erfolgsrate (total_tokens > 0 — Inferenz fand statt)
    ksp7_val = _rate(has_tokens_count, total)
    # KSP8: Format-Konformität (routing_telemetry.judge_refined — sparse)
    ksp8_val = _rate(no_judge_count, rt_total) if rt_total > 0 else None
    # KSP9: Expert-Abdeckung (routing_telemetry — sparse)
    ksp9_val = _rate(expert_covered, non_fast_path)

    ksps: dict[str, KspResult] = {
        "KSP1": KspResult("KSP1", "Tool-Call-Erfolgsrate",        ksp1_val, total_tool_calls),
        "KSP2": KspResult("KSP2", "Anfrage-Erfolgsrate",          ksp2_val, total),
        "KSP3": KspResult("KSP3", "Orchestrator-Aktivierungsrate",ksp3_val, total),
        "KSP4": KspResult("KSP4", "Self-Score-Qualität",          ksp4_val, self_score_n),
        "KSP5": KspResult("KSP5", "User-Rating-Zufriedenheit",    ksp5_val, user_rating_n),
        "KSP6": KspResult("KSP6", "Cache-Nutzungsrate",           ksp6_val, total),
        "KSP7": KspResult("KSP7", "Token-Erfolgsrate",            ksp7_val, total),
        "KSP8": KspResult("KSP8", "Format-Konformität",           ksp8_val, rt_total),
        "KSP9": KspResult("KSP9", "Expert-Abdeckung",             ksp9_val, non_fast_path),
    }

    # ── KPP-Aggregation ──────────────────────────────────────────────────────
    # KPP1: Anfrage-Zuverlässigkeit — vollständig aus usage_log messbar
    kpp1_val = _avg_of(ksp2_val, ksp7_val)
    # KPP2: Qualitätssignale — aus routing_telemetry (sparse, wächst mit Orchestrator-Nutzung)
    kpp2_val = _avg_of(ksp4_val, ksp5_val)
    # KPP3: Infrastruktur-Effizienz — vollständig aus usage_log messbar
    kpp3_val = _avg_of(ksp3_val, ksp6_val)
    # KPP4: Orchestrator-Pipeline — aus routing_telemetry (sparse)
    kpp4_val = _avg_of(ksp8_val, ksp9_val)

    kpps: dict[str, KppResult] = {
        "KPP1": KppResult("KPP1", "Anfrage-Zuverlässigkeit",    ["KSP2","KSP7"],        kpp1_val),
        "KPP2": KppResult("KPP2", "Qualitätssignale",           ["KSP4","KSP5"],        kpp2_val),
        "KPP3": KppResult("KPP3", "Infrastruktur-Effizienz",    ["KSP3","KSP6"],        kpp3_val),
        "KPP4": KppResult("KPP4", "Orchestrator-Pipeline",      ["KSP8","KSP9"],        kpp4_val),
    }

    # ── KHP-Verdichtung ──────────────────────────────────────────────────────
    khp1_val = _avg_of(kpp1_val, kpp2_val, kpp3_val, kpp4_val)

    # Gewichtungsfaktor: Mittelwert der Komplexitäts-Gewichte (vereinfacht)
    # In Produktion: per-Request Gewicht aus complexity-Verteilung berechnen
    khp2_val: Optional[float] = None
    if khp1_val is not None:
        khp2_val = khp1_val * _DEFAULT_WEIGHT

    khp1 = KhpResult("KHP1", "System-Gesamtqualität",           khp1_val)
    khp2 = KhpResult("KHP2", "Risiko-/Chancen-Bewertung",       khp2_val)

    # ── RPZ-Tabelle ──────────────────────────────────────────────────────────
    rpz_table = sorted(
        [
            {
                "name":        e.name,
                "description": e.description,
                "A":           e.a,
                "B":           e.b,
                "E":           e.e,
                "RPZ":         e.rpz,
                "status":      e.status,
                "priority":    "hoch" if e.rpz >= 200 else "mittel" if e.rpz >= 100 else "niedrig",
            }
            for e in KNOWN_FAILURE_MODES
        ],
        key=lambda x: x["RPZ"],
        reverse=True,
    )

    logger.info(
        "KPI computed [%s]: KHP1=%.1f%% KHP2=%.1f%% rows=%d",
        window,
        khp1_val or 0.0,
        khp2_val or 0.0,
        total,
    )

    return KpiSnapshot(
        window=window,
        total_rows=total,
        ksps=ksps,
        kpps=kpps,
        khp1=khp1,
        khp2=khp2,
        rpz_entries=rpz_table,
    )
