# Betriebshandbuch: Holographic Ambient Background Engine (HABE)

Dieses Betriebshandbuch beschreibt die theoretischen Grundlagen, die
mathematische Funktionsweise, die administrative Konfiguration und die
implementierte Systemintegration der **Holographic Ambient Background Engine
(HABE)** in der **MoE Sovereign**-Infrastruktur.

---

## 1. Theoretischer Hintergrund: Dreyfus-Hintergrundsimulation

Die HABE-Architektur simuliert Hubert Dreyfus' Konzept des **„unbewussten Hintergrundwissens“ (1965: Alchemy and Artificial Intelligence)**. Dreyfus argumentierte, dass menschliche Intelligenz auf einem impliziten, nicht-regelbasierten Hintergrund beruht, der unsere Wahrnehmung moduliert, ohne explizit als Faktenkette abgerufen zu werden.

In MoE Sovereign werden aktive GraphRAG-Tripel in einen kontinuierlichen
Vektor – den **Holographic Ambient Vector (HAV)** – kompiliert. In der
gegenwärtig produktiven Integration bewertet HABE damit abgerufene
GraphRAG-Zeilen, filtert bzw. ordnet sie neu und liefert die ausgewählten
Fakten als nachvollziehbaren Textkontext an die Synthese. Eine direkte
Attention- oder KV-Cache-Injektion ist nicht Teil des produktiven
Inferenzvertrags.

---

## 2. Mathematische Grundlagen: Vector Symbolic Architectures (VSA)

HABE nutzt eine dichte Vektor-VSA-Implementierung (spezifisch
**Holographic Reduced Representations - HRR**) der Dimension $D = 2048$.
Die Repräsentation ist eine verlustbehaftete Superposition strukturierter
Relationen; ihre Ähnlichkeitsscores dienen als Retrievalsignal und ersetzen
keinen belegbaren GraphRAG-Fakt.

### 2.1. Grundlegende Operationen
1.  **Erzeugung (Generation):** Jedes Symbol (z. B. `subj:Therapie`, `pred:behandelt`, `obj:Migräne`) wird als dichte, normalisierte Zufallsvariable initialisiert:
    $$\mathbf{v} \sim \mathcal{N}\left(0, \frac{1}{D}\right), \quad \|\mathbf{v}\|_2 = 1$$
2.  **Bindung (Binding $\circledast$):** Assoziation von Rollen und Werten über zirkuläre Faltung (Circular Convolution). Sie wird hocheffizient im Frequenzbereich via FFT berechnet:
    $$\mathbf{x} \circledast \mathbf{y} = \mathcal{F}^{-1}\Big(\mathcal{F}(\mathbf{x}) \odot \mathcal{F}(\mathbf{y})\Big)$$
    Die Bindung ist kommutativ, assoziativ und bewahrt die Vektordimension $D$.
3.  **Entbindung (Unbinding $\circledast^{-1}$):** Zurückgewinnung eines Symbols unter Nutzung des inversen Vektors (Involution $\mathbf{y}^\dagger$ bei zirkulärer Faltung):
    $$\mathbf{y}^\dagger = \text{roll}(\mathbf{y}[::-1], 1), \quad \mathbf{x} \approx (\mathbf{x} \circledast \mathbf{y}) \circledast \mathbf{y}^\dagger$$
4.  **Bündelung (Bundling / Superposition $\oplus$):** Aggregation mehrerer Relationen durch Vektoraddition und anschließende Normalisierung:
    $$\mathbf{S} = \text{Normalize}\left(\sum_{i=1}^N \mathbf{T}_i\right)$$

### 2.2. Forschungsnotiz: hierarchische Graphen-Strukturen

Eine rekursive Bindung ganzer Subgraphen wäre eine mögliche spätere
Erweiterung:

$$\mathbf{v}_{\text{parent\_subgraph}} = \text{bundle}\left(\mathbf{v}_{\text{parent}}, \text{bind}(\mathbf{v}_{\text{child\_subgraph}} \circledast \mathbf{v}_{\text{relation}}, \mathbf{v}_{\text{parent}})\right)$$

Diese Formel ist **keine aktuelle Produktionsfähigkeit**. Der implementierte
Rebuild kompiliert flache Subjekt-Prädikat-Objekt-Tripel; die früher isoliert
vorhandenen, aber nicht aufgerufenen Hierarchie- und
Virtual-Prefix-Hilfsfunktionen wurden entfernt. Vor einer späteren Einführung
wären ein realer GraphRAG-Aufrufer, Qualitätsmetriken und ein End-to-End-Test
erforderlich.

Eine mögliche Abfrage würde den Vektor stufenweise entbinden:
    $$\mathbf{v}_{\text{child\_subgraph}} \approx \text{unbind}(\mathbf{v}_{\text{parent\_subgraph}}, \mathbf{v}_{\text{parent}} \circledast \mathbf{v}_{\text{relation}})$$
Der resultierende verrauschte Vektor müsste anschließend über den
Cleanup-Mechanismus mit dem Vokabular abgeglichen werden.

---

## 3. Dynamische Schwellwert-Kalibrierung (Noise Management)

Beim Bündeln von $N$ Tripeln verhält sich die Superposition $\mathbf{S}$ wie ein verrauschtes Speichermedium. Beim Entbinden einer Relation entsteht ein Rauschteppich (Cross-Talk Noise). 

### 3.1. Das mathematische Problem
Die Standardabweichung des Rauschens ($\sigma$) wächst mit der Anzahl der gebündelten Fakten $N$ relativ zur Dimension $D$:
$$\sigma \approx \sqrt{\frac{N - 1}{D}}$$

Ein statischer Schwellwert (z. B. $\theta = 0.25$) scheitert in der Praxis:
*   Bei $N < 10$ ist er zu hoch (entgeht gültigen Treffern).
*   Bei $N > 150$ liegt er unter dem Rauschteppich (führt zu False Positives/Halluzinationen).

### 3.2. Implementierte Kalibrierungslogik
Der Schwellwert $\theta$ wird bei jedem Abruf dynamisch an die Anzahl der gebündelten Elemente angepasst:

$$\theta(N) = C \cdot \sqrt{\frac{N}{D}}$$

Der Skalierungsfaktor $C$ ist auf $3.0$ vordefiniert, was statistisch $99.9\%$ des mathematischen Rauschens blockiert. Alternativ führt das System beim Kompilieren des Hintergrunds eine empirische Kalibrierung mit Dummy-Abfragen durch, um den Rauschpegel dynamisch einzumessen.

---

## 4. Retrieval-Modulation und Systemintegration

Der implementierte Datenpfad ist:

1. **VSA-Export:** Der Rebuild schreibt den normalisierten HAV als NumPy-Datei
   `models/habe_vector.npy` und das stabile Symbolvokabular als
   `models/habe_vocab.json`.
2. **GraphRAG-Abruf:** `graph/tool_nodes.py` lädt beide Artefakte, extrahiert
   Konzepte aus der Anfrage und bewertet die bereits abgerufenen
   GraphRAG-Zeilen.
3. **Begrenzte Modulation:** Nur Zeilen oberhalb des dynamischen
   Rauschschwellwerts werden priorisiert; bei fehlenden oder ungültigen
   Artefakten bleibt der normale GraphRAG-Kontext erhalten.
4. **Attribution:** Die tatsächlich an die Synthese gelieferten Entitäten
   werden nach der Antwort als Retrieval-Hit oder -Miss zurückgeschrieben.

Die frühere Hilfsfunktion zur Übergabe von `habe_prefix_embedding` ist
bewusst ein No-op. Eine latente Prefix-Integration wäre
inferenzbackend-spezifische Forschung und muss vor einer Produktivbehauptung
implementiert und gegen den jeweiligen Serververtrag getestet werden.

### 4.1. API-Datenfluss

```
[Admin UI HTML] ──(checked)──> [app.py /api/expert-templates]
                                      │
                                (Sichert config_json)
                                      │
                                      ▼
[routing.py] <────────────── [PostgreSQL]
            │
      (Liest enable_habe)
            │
            ▼
[graph/tool_nodes.py] ──(Wenn True)──> [habe_vector.npy + habe_vocab.json]
            │
      (Bewertet GraphRAG-Zeilen)
            │
            ▼
[Synthese-Prompt + Retrieval-Attribution]
```

---

## 5. Betriebsabläufe: Der Rebuild-Cronjob

Da die VSA-Operationen algebraisch sind, benötigt der Rebuild kein
Gewichtstraining. Der Dienst `moe-maintenance` führt ihn standardmäßig
täglich als isolierten Subprozess mit Timeout und Statusdatei aus.

### 5.1. Ablauf des Cronjobs (`scripts/cron_habe_rebuild.py`)
1.  **Abfrage:** Extrahiert alle aktiven Wissens-Tripel aus Neo4j.
2.  **Hierarchischer Zusammenbau:** Ordnet verschachtelte Entitäten in hierarchische Baumstrukturen und kompiliert sie zu einem einzigen HAV.
3. **Sicherer Export:** Schreibt Vektor und Vokabular zunächst in temporäre
   Dateien und veröffentlicht sie atomar als `models/habe_vector.npy` und
   `models/habe_vocab.json`.
4. **Fehlerverhalten:** Liefert Neo4j keine Tripel, bleibt der letzte gültige
   Snapshot unangetastet und der Job endet fehlerhaft. Synthetische
   Bootstrap-Tripel sind nur mit `HABE_ALLOW_BOOTSTRAP=1` für Entwicklung
   erlaubt.

### 5.2. Scheduler-Konfiguration

```dotenv
HABE_SCHEDULER_ENABLED=1
HABE_REBUILD_INTERVAL_SECONDS=86400
HABE_REBUILD_TIMEOUT_SECONDS=1800
HABE_ALLOW_BOOTSTRAP=0
```

Der letzte Exit-Code, die Laufzeit und das Ende der Jobausgabe stehen in
`/app/logs/maintenance-status.json`.

---

## 6. Infrastruktur & Partitionierung der Rechenleistung

Das MoE Sovereign Cluster teilt die anfallenden Lasten mathematisch und architektonisch streng auf:

*   **LUMI-G (SFT/DPO-Training):** Exklusiv für das rechenintensive Training des Sovereign-Orchestrator-Modells (SFT/DPO) auf Basis von 10M+ Token.
*   **Node04-RTX (Interaktive Inferenz):** Führt das Gesamtsystem, den Planner/Judge und die schnellen Experten-Modelle in Echtzeit aus.
*   **Gigabyte HPC K80 (Wissenschaftliches FP64-Rechenwerk):** Führt deterministische mathematische Python-Tools abseits von LLMs aus.
*   **VSA / HABE (CPU):** Berechnet die hierarchische Wissenskompression lokal in Sekundenbruchteilen auf CPU-Ebene des RTX-Nodes.
