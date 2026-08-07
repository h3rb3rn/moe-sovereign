# Betriebshandbuch: Eurisko Heuristic Breeder

Dieses Betriebshandbuch beschreibt die theoretischen Grundlagen, die mathematische Funktionsweise, die Systemintegration und die Abläufe des **Eurisko Heuristic Breeder** in der **MoE Sovereign**-Infrastruktur.

---

## 1. Theoretischer Hintergrund: Eurisko & Selbstreferenzialität (1983)

Douglas Lenats System **Eurisko** (1983) führte das Konzept der **heuristischen Selbstreferenzialität (Heuristic Self-Reference)** ein: Heuristiken wurden nicht starr programmiert, sondern als Datenobjekte dargestellt, wodurch Eurisko in der Lage war, *seine eigenen Heuristiken zu verändern*, zu bewerten und neue Heuristiken zu generieren.

### Das Problem moderner Router
Statische Routing-Vorlagen (z. B. feste VRAM-Schwellenwerte, fixe Zuweisungen oder feste Context-Budgets) degradieren bei sich ändernden Systemlasten oder neuen Modellen.

### Umsetzung in MoE Sovereign
Der Eurisko Heuristic Breeder kann Gating- und Routing-Templates anhand
bestätigten Feedbacks optimieren. Weil ein Lauf aktive Routing-Policies
versioniert und ersetzen kann, ist der periodische Scheduler in der
Produktionskonfiguration bewusst **standardmäßig deaktiviert**:
1.  **Heuristik-Pool:** Heuristiken werden als ausführbare Mutationsobjekte mit Gewichten verwaltet.
2.  **Feeback-Kopplung:** Die Gewichte passen sich basierend auf dem realen Benutzer-Feedback (`user_rating`) an.
3.  **Selbst-Zucht (Crossover):** Bei hoher Performance werden neue Heuristiken durch Kombination erfolgreicher Eltern-Heuristiken erzeugt und dem Pool hinzugefügt.

---

## 2. Mathematische Funktionsweise & Vererbung

Der Optimierungs-Loop (`scripts/eurisko_template_optimizer.py`) arbeitet nach evolutionären Prinzipien:

### 2.1. Roulette-Wheel-Selektion
Um eine Mutation auf ein mangelhaftes Template anzuwenden, wählt der Breeder eine Heuristik $h_i$ aus dem Pool mit einer Wahrscheinlichkeit aus, die proportional zu ihrem aktuellen Gewicht $w_i$ ist:

$$P(h_i) = \frac{w_i}{\sum_{j=1}^M w_j}$$

*   **Gewichtsanpassung (Feedback-Loop):** Nach jedem Optimierungsdurchlauf wertet das System das Benutzer-Feedback (`user_rating` $\in [1, 5]$) aus dem `dynamic_template_feedback_log` aus.
    *   **Erfolg (Rating $\ge 4$):** Das Gewicht der angewandten Heuristik wird belohnt:
        $$w_{\text{neu}} = w_{\text{alt}} \times 1.15$$
    *   **Fehlschlag (Rating $< 3$):** Das Gewicht wird bestraft:
        $$w_{\text{neu}} = \max(0.1, w_{\text{alt}} \times 0.85)$$

### 2.2. Heuristische Vererbung (Crossover Zucht)
Erreicht eine Heuristik im Pool ein Gewicht von $> 1.5$ (hervorragende historische Performance), kreuzt der Breeder die beiden am besten bewerteten Heuristiken ($h_A$ und $h_B$) und erzeugt eine neue Kombinationsheuristik:

$$h_{\text{child}} = h_A \circ h_B$$

Diese neue Heuristik (`bred_A_and_B`) führt nacheinander die Mutationen beider Elternteile aus. Sie wird dauerhaft im Pool unter `/app/data/eurisko_heuristics.json` gespeichert.

---

## 3. Architektur & Datenfluss

Der Breeder läuft als isolierter, zeitlich begrenzter Subprozess des
`moe-maintenance`-Dienstes oder als explizit gestarteter Einmallauf:

```
                  [PostgreSQL: dynamic_template_feedback_log]
                                       │
                      (Liest schlechtes Feedback < 3)
                                       │
                                       ▼
                         [Heuristic Breeder (Eurisko)]
              ┌──────────────────────────────────────────────────┐
              │ 1. Passt Gewichte basierend auf Logs an          │
              │ 2. Führt Crossover-Zucht durch (Gewichte > 1.5)  │
              │ 3. Wählt Mutator via Roulette-Wheel aus          │
              │ 4. Mutiert das defekte Template                  │
              └────────────────────────┬─────────────────────────┘
                                       │
                         (Erzeugt optimiertes Template)
                                       │
                                       ▼
                  [PostgreSQL: admin_expert_templates]
                      (Inaktiviert das alte Template)
                                       │
                                       ▼
                       [ChromaDB: moe_template_cache]
                       (Leitet Cache auf neues Template um)
```

### 3.1. Die Standardheuristiken
*   **`context_scaling_1.5x`:** Skaliert das VRAM-Kontextfenster der Experten und des Planners/Judges um den Faktor $1.5$ nach oben (reduziert Context Overflow-Fehler).
*   **`enable_context_features`:** Schaltet RAG-Erweiterungen (GraphRAG, Web-Suche) aktiv hinzu, um Informationslücken zu schließen.
*   **`enable_vsa_habe`:** Schaltet das HABE-VSA-Hintergrundwissen hinzu, um implizite Kontexteigenschaften einzubetten.

---

## 4. Betriebsabläufe & Aufruf

### 4.1. Die Heuristik-Pool-Datei
Die gelernten Gewichte und erzeugten Zucht-Heuristiken werden serialisiert in `/app/data/eurisko_heuristics.json` abgelegt:
```json
{
  "context_scaling_1.5x": {
    "name": "context_scaling_1.5x",
    "weight": 1.15,
    "metadata": {
      "description": "Scale context window by 1.5x",
      "type": "context"
    }
  },
  "bred_enable_vsa_habe_and_context_scaling_1.5x": {
    "name": "bred_enable_vsa_habe_and_context_scaling_1.5x",
    "weight": 1.0,
    "metadata": {
      "bred_from": ["enable_vsa_habe", "context_scaling_1.5x"],
      "description": "Bred combination of enable_vsa_habe and context_scaling_1.5x"
    }
  }
}
```

### 4.2. Manueller Testaufruf
Du kannst die Optimierungs-Schleife jederzeit manuell anstoßen:
```bash
docker compose run --rm --no-deps moe-maintenance \
  python3 scripts/eurisko_template_optimizer.py
```

Der Prozess initialisiert die Datenbank explizit, schreibt neue
Template-Versionen und inaktiviert gegebenenfalls die Vorgänger. Vor einem
manuellen Lauf müssen daher Datenbank-Backup, Feedbackqualität und erwartete
Mutationen geprüft werden.

### 4.3. Kontrollierter Scheduler

```dotenv
EURISKO_SCHEDULER_ENABLED=0
EURISKO_INTERVAL_SECONDS=21600
EURISKO_TIMEOUT_SECONDS=1800
```

Der Scheduler protokolliert Exit-Code, Laufzeit und das Ende der Ausgabe in
`/app/logs/maintenance-status.json`. Erst nach einem beobachteten Einmallauf
und fachlicher Freigabe sollte `EURISKO_SCHEDULER_ENABLED=1` gesetzt werden.

### 4.4. Test-Suite ausführen
Das evolutionäre System ist über `tests/test_habe_and_advice.py` vollautomatisch unit-getestet.
```bash
python3 -m pytest tests/test_habe_and_advice.py -k "eurisko"
```
