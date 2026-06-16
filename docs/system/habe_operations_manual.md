# Betriebshandbuch: Holographic Ambient Background Engine (HABE)

Dieses Betriebshandbuch beschreibt die theoretischen Grundlagen, die mathematische Funktionsweise, die administrative Konfiguration und die Systemintegration der **Holographic Ambient Background Engine (HABE)** in der **MoE Sovereign**-Infrastruktur.

---

## 1. Theoretischer Hintergrund: Dreyfus-Hintergrundsimulation

Die HABE-Architektur simuliert Hubert Dreyfus' Konzept des **„unbewussten Hintergrundwissens“ (1965: Alchemy and Artificial Intelligence)**. Dreyfus argumentierte, dass menschliche Intelligenz auf einem impliziten, nicht-regelbasierten Hintergrund beruht, der unsere Wahrnehmung moduliert, ohne explizit als Faktenkette abgerufen zu werden.

In MoE Sovereign wird dieses unbewusste Hintergrundwissen simuliert, indem wir die gesamte Wissensbasis (GraphRAG-Tripel und Cache-Historien) in einen einzigen kontinuierlichen Vektor – den **Holographic Ambient Vector (HAV)** – komprimieren. Dieser Vektor moduliert den Aufmerksamkeitsprozess (Attention Space) und den KV-Cache der ausführenden SLMs, anstatt expliziten Kontext via Prompt-Erweiterung einzuschleusen.

---

## 2. Mathematische Grundlagen: Vector Symbolic Architectures (VSA)

HABE nutzt eine dichte Vektor-VSA-Implementierung (spezifisch **Holographic Reduced Representations - HRR**) der Dimension $D = 2048$. Dies ermöglicht die verlustfreie algebraische Speicherung von strukturierten Relationen (Subjekt-Prädikat-Objekt-Tripel) in einem einzigen Vektor konstanter Größe.

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

## 4. GUI & Konfiguration im Admin-Portal

Die Steuerung des HABE-Hintergrunds erfolgt nahtlos über die Administrationsoberfläche der Experten-Templates:

### 4.1. Aktivierung in Templates
*   **Neues Template:** Im Erstellungs-Modal befindet sich unter den **Pipeline Toggles** die Option `HABE (VSA)`.
*   **Template bearbeiten:** Der Schalter `HABE (VSA)` kann für bestehende Templates ein- und ausgeschaltet werden.

```
[ ] Cache (ChromaDB)    [x] GraphRAG (Neo4j)    [x] HABE (VSA)
```

### 4.2. API-Datenfluss

```
[Admin UI HTML] ──(checked)──> [app.py /api/expert-templates]
                                      │
                                (Sichert config_json)
                                      │
                                      ▼
[inference.py / routing.py] <── [PostgreSQL / SQLite]
            │
      (Liest enable_habe)
            │
            ▼
[graph/expert.py / main.py] ──(Wenn True)──> [vsa_background.py (Injektion)]
```

---

## 5. Betriebsabläufe: Der Rebuild-Cronjob

Da die VSA-Operationen rein algebraisch sind, benötigt das „Nachtrainieren“ des Hintergrunds kein Deep Learning. Ein stündlicher oder täglicher Cronjob führt die Vektor-Kompilierung auf CPU-Ebene durch.

### 5.1. Ablauf des Cronjobs (`scripts/cron_habe_rebuild.py`)
1.  **Abfrage:** Extrahiert alle aktiven Wissens-Tripel aus Neo4j (`MATCH (s)-[r]->(o) RETURN s.name, type(r), o.name`).
2.  **Bindung:** Konvertiert jedes Tripel in ein gebundenes VSA-Vektorkonstrukt ($\mathbf{v}_{\text{subj}} \circledast \mathbf{v}_{\text{pred}} \circledast \mathbf{v}_{\text{obj}}$).
3.  **Vergessenskurve (Decay):** Wendet einen zeitbasierten Dämpfungsfaktor an, um veraltetes Wissen langsam verblassen zu lassen.
4.  **Export:** Speichert den normalisierten Summenvektor als Binärdatei unter `models/habe_vector.bin` und aktualisiert das Vokabular-Mapping in `models/habe_vocab.json`.

---

## 6. Infrastruktur & Partitionierung der Rechenleistung

Das MoE Sovereign Cluster teilt die anfallenden Lasten mathematisch und architektonisch streng auf:

```
                  ┌─────────────────────────────────┐
                  │      LUMI-G Supercomputer       │
                  │   18.000 GPU-Stunden (AMD)      │
                  │  - Deep Learning (SFT/DPO/CPT)  │
                  └────────────────┬────────────────┘
                                   │
                     (Exportiert trainiertes Model)
                                   │
                                   ▼
┌──────────────────────────────────────────────────────────────────────┐
│                      Lokaler Cluster-Verbund                         │
│                                                                      │
│ ┌────────────────────────┐ ┌────────────────────────┐ ┌────────────┐ │
│ │       Node04-RTX       │ │  Gigabyte HPC G431-MM0 │ │ HABE (VSA) │ │
│ │     (113 GB VRAM)      │ │   (14x Tesla K80)      │ │  (Local)   │ │
│ │ - LLM Echtzeit-Inferenz│ │ - Deterministisches    │ │ - Algebra- │ │
│ │ - Planner / Judge      │ │   Float64 Rechenwerk   │ │   Hintergr.│ │
│ │   Ausführung           │ │ - Standby-LLM-Inferenz │ │   auf CPU  │ │
│ └────────────────────────┘ └────────────────────────┘ └────────────┘ │
└──────────────────────────────────────────────────────────────────────┘
```

1.  **LUMI-G (SFT/DPO-Training):** Exklusiv für das rechenintensive Training des Sovereign-Orchestrator-Modells (SFT/DPO) auf Basis von 10M+ Token.
2.  **Node04-RTX (Interaktive Inferenz):** Führt das Gesamtsystem, den Planner/Judge und die schnellen Experten-Modelle in Echtzeit aus.
3.  **Gigabyte HPC K80 (Wissenschaftliches FP64-Rechenwerk):** Führt deterministische mathematische Python-Tools abseits von LLMs aus. Läuft nur bei Bedarf und nutzt `ollama37` als Standby-Inferenz, falls der Hauptpool überlastet ist.
4.  **VSA / HABE (CPU):** Berechnet die assoziative Wissenskompression lokal in Sekundenbruchteilen auf CPU-Ebene des RTX-Nodes.
