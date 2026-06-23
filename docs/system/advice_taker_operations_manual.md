# Betriebshandbuch: John McCarthy's Advice-Taker Rule-Engine

Dieses Betriebshandbuch beschreibt die theoretischen Grundlagen, die mathematische Funktionsweise, die administrative Konfiguration und die Systemintegration der **Advice-Taker Rule-Engine** in der **MoE Sovereign**-Infrastruktur.

---

## 1. Theoretischer Hintergrund: John McCarthys „Advice Taker“ (1958)

John McCarthy schlug 1958 in seinem bahnbrechenden Papier *„Programs with Common Sense“* den **Advice Taker** vor – ein System, das durch Sätze (Ratschläge/Advice) in einer formalen Sprache programmiert wird und logisch über Aktionen deduzieren kann, **ohne dass der Quellcode der Engine modifiziert werden muss**.

### Das Problem moderner Agenten
Änderungen an Unternehmensrichtlinien, Werkzeugzuweisungen oder Prioritäten erfordern bei modernen LLM-Agenten meist Codeänderungen oder fehleranfällige RAG-Prompt-Injektionen. Es fehlt eine deterministische logische Schranke, die Administrative Ratschläge verarbeitet.

### Umsetzung in MoE Sovereign
Die Advice-Taker Rule-Engine in MoE Sovereign stellt diese deterministische Inferenzebene bereit:
1.  **Regeldefinition:** Regeln werden deklarativ als Datenstrukturen definiert (JSON).
2.  **Inferenz & Enforce:** Vor jeder Planerstellung (Planner Node) wertet die Rule-Engine die Benutzeranfrage gegen die aktiven Regeln aus und modifiziert den resultierenden Plan direkt.

---

## 2. Mathematische Funktionsweise & Inferenz

Die Engine (`services/advice_store.py`) nutzt zwei primäre Mechanismen zur Muster- und Absichtsabgleichung:

### 2.1. Semantisches 3-Gram-Matching (Jaccard-Koeffizient)
Um Scopes abzugleichen, ohne ein schweres lokales LLM oder ein externes Embeddings-Modell zu bemühen, führt die Engine ein zeichenbasiertes 3-Gram-Matching durch. Der Jaccard-Koeffizient vergleicht die Menge der 3-Grams der Query ($G_{\text{query}}$) mit der Menge der 3-Grams des Regel-Scopes ($G_{\text{scope}}$):

$$J(G_{\text{query}}, G_{\text{scope}}) = \frac{|G_{\text{query}} \cap G_{\text{scope}}|}{|G_{\text{query}} \cup G_{\text{scope}}|}$$

*   **Schwellwert:** Ein Koeffizient von $\ge 0.3$ triggert die Regel.
*   **Vorteil:** Hohe Robustheit gegenüber Rechtschreibfehlern, Deklinationsformen und syntaktischen Abweichungen bei 0 ms Inferenzlatenz.

### 2.2. Deklarative Regex-Parameter-Extraktion
Regeln können ein Wörterbuch von `parameter_extractors` definieren. Jedes Element ordnet einem Parameternamen ein reguläres Ausdrücke-Muster zu.
*   **Ablauf:** Erkennt die Inferenz-Engine einen Treffer, extrahiert sie die entsprechenden Substrings über die Regex-Gruppen aus der Benutzer-Query.
*   **Task-Generierung:** Die extrahierten Werte werden dynamisch in das `mcp_args`-Objekt des zugeordneten MPC-Werkzeugs injiziert.

---

## 3. Architektur & Datenfluss

Der Advice-Taker fängt Anfragen am Anfang der LangGraph-Pipeline ab:

```
[Benutzeranfrage] ───► [routing.py / planner.py]
                             │
                             ▼
                 [services/advice_store.py]
                 ┌────────────────────────────────────────────────────────┐
                 │ 1. get_active_advice()                                 │
                 │    - Prüft exakte Patterns                             │
                 │    - Berechnet semantisches Jaccard-Matching (>= 0.3)  │
                 │ 2. enforce_advice_rules()                              │
                 │    - Extrahiert Argumente über parameter_extractors    │
                 │    - Generiert prioritäre Initial-Tasks                │
                 └────────────────────────┬───────────────────────────────┘
                                          │
                               (Injektiert Tasks)
                                          │
                                          ▼
                       [Modifizierter LangGraph-Ausführungsplan]
                                          │
                                          ▼
                         [Ausführung auf Expert-Cluster]
```

### 3.1. Regel-Schema (`declarative_advice.json`)
Die Regeln werden unter `/app/data/declarative_advice.json` gespeichert. Beispiel-Struktur:
```json
[
  {
    "id": "advice_subnet_calc",
    "rule": "Use subnet_calc tool for CIDR/IP masks.",
    "category_scope": "subnetting_helper",
    "pattern": "\\b\\d{1,3}\\.\\d{1,3}\\.\\d{1,3}\\.\\d{1,3}/\\d{1,2}\\b",
    "category": "precision_tools",
    "mcp_tool": "subnet_calc",
    "default_task_description": "Calculate subnet info",
    "enabled": true,
    "parameter_extractors": {
      "cidr": "(\\b\\d{1,3}\\.\\d{1,3}\\.\\d{1,3}\\.\\d{1,3}/\\d{1,2}\\b)"
    }
  }
]
```

---

## 4. Administration & Verifizierung

### 4.1. Manuelles Anlegen über Python
Regeln können über das Backend direkt über API-Methoden registriert werden:
```python
from services.advice_store import add_advice_rule
add_advice_rule(
    rule_text="Run optimization on target database.",
    category_scope="db_tuning",
    pattern=r"optimize db",
    category="db_tuning",
    mcp_tool="tune_database",
    parameter_extractors={
        "db_name": r"db:\s*([a-zA-Z0-9_-]+)"
    }
)
```

### 4.2. Test-Suite ausführen
Die Rule-Engine wird über die Testsuite in `tests/test_habe_and_advice.py` vollständig abgedeckt. Führe den Test mit folgendem Befehl aus:
```bash
python3 -m pytest tests/test_habe_and_advice.py -k "advice"
```
