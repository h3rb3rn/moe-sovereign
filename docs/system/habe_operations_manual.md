# Betriebshandbuch: Holographic Ambient Background Engine (HABE) 2.0

Dieses Betriebshandbuch beschreibt die theoretischen Grundlagen, die mathematische Funktionsweise, die administrative Konfiguration und die Systemintegration der **Holographic Ambient Background Engine (HABE) 2.0** in der **MoE Sovereign**-Infrastruktur.

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

### 2.2. HABE 2.0: Hierarchische Graphen-Strukturen
Das HABE 2.0-Upgrade erweitert die flache Triplett-Kompression um hierarchische Wissensgraphen (Bäume). Ein Eltern-Knoten bindet seine Kind-Subgraphen und deren Relation rekursiv in sich ein:

$$\mathbf{v}_{\text{parent\_subgraph}} = \text{bundle}\left(\mathbf{v}_{\text{parent}}, \text{bind}(\mathbf{v}_{\text{child\_subgraph}} \circledast \mathbf{v}_{\text{relation}}, \mathbf{v}_{\text{parent}})\right)$$

*   **Vorteil:** Verschachtelte Strukturen (z. B. eine Applikation mit untergeordneten Datenbanken, welche wiederum auf spezifischen Servern laufen) können als ein einziger Vektor abgebildet werden.
*   **Abfrage (Recursive Unbinding):** Um die Substrukturen abzufragen, wird der hierarchische Vektor stufenweise entbunden:
    $$\mathbf{v}_{\text{child\_subgraph}} \approx \text{unbind}(\mathbf{v}_{\text{parent\_subgraph}}, \mathbf{v}_{\text{parent}} \circledast \mathbf{v}_{\text{relation}})$$
    Der resultierende verrauschte Vektor wird erneut über den Cleanup-Mechanismus mit dem Vokabular abgeglichen.

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

## 4. Virtual Prefix Attention Modulation & Systemintegration

HABE 2.0 schleust das VSA-Hintergrundwissen nicht als Klartext in den Context Window des LLMs ein, sondern überbrückt die Lücke im latenten Vektorraum:

1.  **VSA-Export:** Der kompilierte, normalisierte HAV-Vektor wird in eine liste von 2048 Gleitkommazahlen exportiert.
2.  **API-Einspeisung:** Das Backend (`services/inference.py` / `graph/expert.py`) fängt Anfragen ab, wenn `enable_habe=True` im aktiven Template gesetzt ist, und übergibt die Embeddings über die Inferenzoptionen:
    *   **OpenAI-kompatibel:** Übergeben in `extra_body.options.habe_prefix_embedding`
    *   **Native Ollama:** Übergeben als `options.habe_prefix_embedding` im Request-Payload.
3.  **Inferenz-Wirkung:** Das lokale Sprachmodell (z. B. Qwen oder Llama) nutzt diese Embeddings im Attention-Mechanismus als virtuelles Prefix. Es "fühlt" den topologischen Wissenshintergrund, ohne Kontext-Token zu belegen.

### 4.1. API-Datenfluss

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
[graph/expert.py / main.py] ──(Wenn True)──> [vsa_background.py (Hierarchische Kompilierung)]
                                                            │
                                                     (Exportiert Embeddings)
                                                            │
                                                            ▼
                                              [LLM Attention Engine (Local)]
```

---

## 5. Betriebsabläufe: Der Rebuild-Cronjob

Da die VSA-Operationen rein algebraisch sind, benötigt das „Nachtrainieren“ des Hintergrunds kein Deep Learning. Ein stündlicher oder täglicher Cronjob führt die Vektor-Kompilierung auf CPU-Ebene durch.

### 5.1. Ablauf des Cronjobs (`scripts/cron_habe_rebuild.py`)
1.  **Abfrage:** Extrahiert alle aktiven Wissens-Tripel aus Neo4j.
2.  **Hierarchischer Zusammenbau:** Ordnet verschachtelte Entitäten in hierarchische Baumstrukturen und kompiliert sie zu einem einzigen HAV.
3.  **Export:** Speichert den Summenvektor als Binärdatei unter `models/habe_vector.bin` und aktualisiert das Vokabular-Mapping in `models/habe_vocab.json`.

---

## 6. Infrastruktur & Partitionierung der Rechenleistung

Das MoE Sovereign Cluster teilt die anfallenden Lasten mathematisch und architektonisch streng auf:

*   **LUMI-G (SFT/DPO-Training):** Exklusiv für das rechenintensive Training des Sovereign-Orchestrator-Modells (SFT/DPO) auf Basis von 10M+ Token.
*   **Node04-RTX (Interaktive Inferenz):** Führt das Gesamtsystem, den Planner/Judge und die schnellen Experten-Modelle in Echtzeit aus.
*   **Gigabyte HPC K80 (Wissenschaftliches FP64-Rechenwerk):** Führt deterministische mathematische Python-Tools abseits von LLMs aus.
*   **VSA / HABE (CPU):** Berechnet die hierarchische Wissenskompression lokal in Sekundenbruchteilen auf CPU-Ebene des RTX-Nodes.
