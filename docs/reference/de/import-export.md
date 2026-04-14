# Import & Export

Experten-Vorlagen, Claude Code Profile und **Wissensgraph-Bundles** koennen als JSON-Dateien exportiert und auf anderen Instanzen importiert werden. Die Funktion ist im Admin-Backend und im User-Portal verfuegbar.

## Uebersicht

| Was | Admin | User | Dateiname |
|-----|-------|------|-----------|
| Experten-Vorlagen | `/templates` | `/user/templates` | `expert_templates.json` |
| Claude Code Profile | `/profiles` | `/user/cc-profiles` | `cc_profiles.json` |
| **Wissensgraph** | `/knowledge` | — | `moe-knowledge-DATUM.json` |

---

## Wissensgraph Export / Import

!!! info "Community-Wissens-Bundles"
    MoE Sovereign kann gelerntes Wissen aus dem Neo4j-Wissensgraphen als
    JSON-LD-Bundles exportieren. Diese Bundles koennen mit anderen MoE-Instanzen
    oder der Community geteilt werden — **kollektive Intelligenz** ueber Deployments hinweg.

### Was wird exportiert?

| Komponente | Beschreibung | Sensibel? |
|-----------|-------------|:---------:|
| **Entities** | Benannte Konzepte (z.B. "JavaScript", "SyntaxError") | Nein |
| **Relationen** | Wissens-Tripel (z.B. duplicate_const → CAUSES → SyntaxError) | Potenziell |
| **Synthesen** | Uebergreifende Erkenntnisse die mehrere Entities verbinden | Nein |

### Datenschutz & Semantic-Leakage-Schutz

Die Export-Pipeline wendet drei Schutzschichten an:

1. **Metadaten-Bereinigung**: `tenant_id`, `source_model`, Zeitstempel werden standardmaessig entfernt
2. **Regex-Hardfilter**: Erkennt und entfernt Entities/Relationen mit:
    - Passwoertern, API-Keys, Zugangsdaten
    - IP-Adressen, E-Mail-Adressen
    - Infrastruktur-Hinweisen (`prod_`, `staging_`, `internal_`)
    - Kunden-/Mandantennamen
3. **Sensitive Relationstypen**: Relationen wie `HAS_PASSWORD`, `HAS_CREDENTIAL`, `AUTHENTICATES_WITH` werden immer ausgeschlossen

Die Export-Statistiken enthalten einen `scrubbed`-Zaehler der anzeigt wie viele Eintraege entfernt wurden.

### Export-API

```
GET /graph/knowledge/export
    ?domains=technical_support,code_reviewer   # kommasepariert (optional)
    &min_trust=0.3                              # Mindest-Vertrauenswert (Standard: 0.3)
    &strip_sensitive=true                       # PII entfernen (Standard: true)
    &include_syntheses=true                     # Synthesen einschliessen (Standard: true)
```

**Admin-UI**: Admin-Backend → Knowledge → Export Bundle

### Bundle-Format (JSON-LD)

```json
{
  "@context": "https://moe-sovereign.org/knowledge/v1",
  "format_version": "1.0",
  "exported_at": "2026-04-12T20:00:00Z",
  "filters": {"domains": ["code_reviewer"], "min_trust": 0.3},
  "stats": {"entities": 728, "relations": 2341, "syntheses": 69, "scrubbed": 97},
  "entities": [
    {"name": "SyntaxError", "type": "Error", "source": "extracted", "domain": "code_reviewer"}
  ],
  "relations": [
    {
      "subject": "duplicate_const_declaration",
      "predicate": "CAUSES",
      "object": "SyntaxError",
      "confidence": 0.9,
      "trust_score": 0.85,
      "verified": true
    }
  ]
}
```

### Import-API

```
POST /graph/knowledge/import
Content-Type: application/json

{
  "bundle": { ... },
  "source_tag": "community_import",
  "trust_floor": 0.5,
  "dry_run": false
}
```

### Import-Sicherheitsmechanismen

| Schutz | Beschreibung |
|--------|-------------|
| **Entity MERGE** | Entities werden nach Name zusammengefuehrt — keine Duplikate |
| **Trust-Deckelung** | Importierte Relationen werden auf `trust_floor` (Standard 0.5) begrenzt — ueberschreiben nie lokal verifizierte Fakten |
| **Widerspruchserkennung** | Widerspricht ein importiertes Tripel einer bestehenden hochvertrauten Relation (z.B. A-[TREATS]->B vs. importiert A-[CAUSES]->B), wird der Import **uebersprungen** und protokolliert |
| **Quellennachverfolgung** | Alle importierten Daten werden mit `source: "community_import"` markiert |

### Import-Antwort

```json
{
  "status": "ok",
  "dry_run": false,
  "entities_created": 42,
  "entities_skipped": 686,
  "relations_created": 1205,
  "relations_skipped": 136,
  "syntheses_created": 69,
  "contradictions": [
    {
      "imported": "(Medikament_X)-[TREATS]->(Zustand_Y)",
      "conflicts_with": "(Medikament_X)-[CONTRAINDICATES]->(Zustand_Y)",
      "existing_trust": 0.8
    }
  ],
  "errors": []
}
```

---

## Vorlagen & Profile Export/Import

### Export-Ablauf

```
/templates → "Export" Button → GET /api/expert-templates/export → Download
/profiles  → "Export" Button → GET /api/profiles/export → Download
```

### Import-Modi

| Modus | Verhalten bei Duplikaten |
|-------|------------------------|
| `merge` | Bestehende Eintraege werden **uebersprungen** |
| `replace` | Bestehende Eintraege werden **ueberschrieben** |

### Validierungsregeln

| Pruefung | Fehlerverhalten |
|----------|----------------|
| `type` passt zum Endpunkt | HTTP 400 |
| `version` == `"1.0"` | HTTP 400 |
| `items` ist ein Array | HTTP 400 |
| `name` ist leer | Eintrag wird uebersprungen |
| Duplikat + Modus `merge` | Eintrag wird uebersprungen |
| Duplikat + Modus `replace` | Eintrag wird ueberschrieben |
