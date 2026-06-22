# Betriebshandbuch: Dynamic System Prompt Generation

Dieses Betriebshandbuch beschreibt die theoretischen Grundlagen, die Architektur, die Systemintegration und die Abläufe der **Dynamischen System-Prompt-Generierung (Dynamic System Prompts)** in der **MoE Sovereign**-Infrastruktur.

---

## 1. Theoretischer Hintergrund & Motivation

In einem komplexen Mixture of Experts (MoE) System hängt die Qualität der Antworten entscheidend von der Präzision der System-Prompts ab. Statische System-Prompts für den Planner, den Judge und die einzelnen Experten schränken die Flexibilität ein und führen bei feineren Nuancen einer Anfrage (z. B. Sprachpräferenz, Formatvorgaben, Komplexitätsgrad) zu suboptimalen Ergebnissen.

Die **Dynamische System-Prompt-Generierung** löst dieses Problem, indem sie für jeden eingehenden Benutzer-Prompt maßgeschneiderte System-Prompts erzeugt für:
1.  **Den Planner:** Spezifische Anweisungen zur Zerlegung der exakten Anfrage und zur Delegation an Experten.
2.  **Den Judge:** Prompt-spezifische Kriterien zur Synthese und Widerspruchsbereinigung (Paraconsistent Logic).
3.  **Die aktiven Experten:** Maßgeschneiderte Rollen- und Aufgabenbeschreibungen basierend auf der konkreten Anfrage.

---

## 2. Architektur & Dual-Path-Design

Um die Latenz im interaktiven Routing-Pfad zu minimieren, wurde ein **Dual-Path-Design** implementiert:

```
                          ┌───────────────────────────┐
                          │    Eingehender Prompt     │
                          └─────────────┬─────────────┘
                                        │
                         (Prüfe Env-Variable Enabled?)
                                        │
                 ┌──────────────────────┴──────────────────────┐
                 ▼ [True]                                      ▼ [False / Fehler]
    ┌───────────────────────────┐                ┌───────────────────────────┐
    │    LLM-basierter Pfad     │                │   Strukturierter Pfad     │
    │  (Meta-Prompter Call)     │                │  (Keyword-Interpolation)  │
    │  ~150ms Latenz (Offline)  │                │     0ms Latenz (Online)   │
    └────────────┬──────────────┘                └─────────────┬─────────────┘
                 │                                             │
                 └──────────────────────┬──────────────────────┘
                                        ▼
                         ┌───────────────────────────┐
                         │   Optimal_Template_JSON   │
                         │(Planner, Judge, Experts)  │
                         └───────────────────────────┘
```

### 2.1. LLM-basierter Pfad (Meta-Prompter)
*   **Steuerung:** Aktiviert über die Umgebungsvariable `DYNAMIC_SYSTEM_PROMPTS_LLM_ENABLED=true`.
*   **Modell:** Nutzt die konfigurierte `planner_llm` für einen asynchronen Meta-Prompter-Call.
*   **Aufgabe:** Das LLM erhält den Benutzer-Prompt und die Liste der aktiven Experten und generiert ein strukturiertes JSON-Objekt mit maßgeschneiderten Prompts.
*   **Einsatzbereich:** Offline-Datengenerierung (SFT-Training) oder hochpräzise Setups ohne strikte Latenzvorgaben.

### 2.2. Strukturierter Pfad (Zero-Latency Fallback)
*   **Steuerung:** Standardmäßig aktiv oder als automatischer Fallback bei LLM-Fehlern.
*   **Logik:** Analysiert den Prompt auf spezifische Keywords (z. B. Sprachindikatoren wie "Deutsch", "English" oder Strukturhinweise wie "step by step").
*   **Aufgabe:** Führt eine regelbasierte Keyword-Interpolation durch, um Sprach- und Struktur-Direktiven in die vordefinierten Experten- und Orchestrator-Personas einzubetten.
*   **Einsatzbereich:** Live-Routing im Produktivbetrieb (0 ms zusätzliche Latenz).

---

## 3. Datenfluss & Implementierung

Die Kernlogik ist in [services/dynamic_router.py](file:///opt/deployment/moe-sovereign/moe-infra/services/dynamic_router.py) integriert und läuft im Rahmen von `get_dynamic_template()` ab:

1.  **ONNX Klassifizierung:** Der Router prognostiziert die aktiven Experten und die Komplexität.
2.  **Prompt-Generierung:** `_generate_prompt_specific_prompts(prompt, active_experts)` entscheidet anhand des Umgebungszustands über den Pfad (LLM vs. strukturiert) und liefert die System-Prompts zurück.
3.  **Template-Kompilierung:** Die generierten Prompts werden in das `template_config`-Objekt unter `"planner_prompt"`, `"judge_prompt"` und `"experts[exp]["system_prompt"]"` eingetragen.
4.  **Datenbank & Cache:** Das fertige Template wird in PostgreSQL registriert und für zukünftige identische/ähnliche Anfragen in ChromaDB gepuffert.

---

## 4. Synthetische Datengenerierung (SFT Pipeline)

Für das Training des Sovereign-Orchestrator-Modells auf LUMI-G wird ein Trainingsdatensatz bestehend aus Paaren von `(Prompt, Optimal_Template_JSON)` benötigt.

Das Generierungsskript [scripts/dataset_generator.py](file:///opt/deployment/moe-sovereign/moe-infra/scripts/dataset_generator.py) wurde dahingehend erweitert:
1.  **Seed-Prompts:** Für bestehende historische Prompts wird die Struktur automatisch über den Zero-Latency-Pfad generiert und mit Default-Modellkonfigurationen vervollständigt.
2.  **Synthetische Varianten:** Bei der Generierung neuer Prompts wird das Datengenerator-Modell angewiesen, eine Liste von Objekten auszugeben, die direkt den fertigen Prompt sowie das dazugehörige `optimal_template` enthalten.
3.  **Validierung:** Das Skript überprüft und korrigiert unvollständige LLM-Ausgaben automatisch vor dem Schreiben in `synthetic_router_dataset.json`.

---

## 5. Administration & Konfiguration

### 5.1. Umgebungsvariablen in `.env`
*   `DYNAMIC_SYSTEM_PROMPTS_LLM_ENABLED`: Schaltet die LLM-basierte Generierung im Live-System ein/aus (`true` oder `false`).

### 5.2. Testen und Verifizieren
Die Funktionalität kann über die integrierte Testsuite validiert werden:
```bash
python3 -m pytest tests/test_dynamic_router.py
```

*   `test_generate_fallback_structured_prompts`: Verifiziert die korrekte Extraktion von Sprach- und Schritt-Direktiven im Zero-Latency-Pfad.
*   `test_generate_prompt_specific_prompts_llm`: Simuliert den Meta-Prompter-Aufruf und prüft die korrekte Strukturierung und Integration.
