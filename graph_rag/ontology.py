"""
Base ontology for the MoE Knowledge Graph.
Covers the four main expert domains:
Medical, Legal, Technical, Science/Math
"""

from typing import List, Dict, Any

# ─── ENTITIES ────────────────────────────────────────────────────────────────

_ENTITIES: List[Dict[str, Any]] = [

    # ── MEDICINE ─────────────────────────────────────────────────────────────
    {"name": "Medikament",         "type": "Medical_Concept",  "aliases": ["Arzneimittel", "Pharmazeutikum", "Drug", "Medication"]},
    {"name": "Krankheit",          "type": "Medical_Concept",  "aliases": ["Erkrankung", "Leiden", "Disease", "Illness"]},
    {"name": "Symptom",            "type": "Medical_Concept",  "aliases": ["Beschwerden", "Anzeichen", "Symptome"]},
    {"name": "Behandlung",         "type": "Medical_Concept",  "aliases": ["Therapie", "Therapiemaßnahme", "Treatment"]},
    {"name": "Diagnose",           "type": "Medical_Concept",  "aliases": ["Befund", "Beurteilung", "Diagnosis"]},
    {"name": "Nebenwirkung",       "type": "Medical_Concept",  "aliases": ["Unerwünschte Wirkung", "Adverse Effect"]},
    {"name": "Wechselwirkung",     "type": "Medical_Concept",  "aliases": ["Interaktion", "Drug Interaction"]},

    # Medikamentenklassen
    {"name": "NSAID",              "type": "Drug_Class",       "aliases": ["Nicht-steroidales Antirheumatikum", "Schmerzmittel"]},
    {"name": "Analgetikum",        "type": "Drug_Class",       "aliases": ["Schmerzmittel", "Painkiller"]},
    {"name": "Antibiotikum",       "type": "Drug_Class",       "aliases": ["Antiinfektivum", "Antibiotic"]},
    {"name": "Antihypertensivum",  "type": "Drug_Class",       "aliases": ["Blutdruckmittel", "Antihypertensive"]},
    {"name": "Antidiabetikum",     "type": "Drug_Class",       "aliases": ["Diabetesmittel", "Antidiabetic"]},
    {"name": "Antikoagulans",      "type": "Drug_Class",       "aliases": ["Blutverdünner", "Anticoagulant"]},

    # Konkrete Medikamente
    {"name": "Ibuprofen",          "type": "Drug",             "aliases": ["Ibumetin", "Nurofen", "Advil"]},
    {"name": "Paracetamol",        "type": "Drug",             "aliases": ["Acetaminophen", "Tylenol", "Ben-u-ron"]},
    {"name": "Aspirin",            "type": "Drug",             "aliases": ["Acetylsalicylsäure", "ASS", "ASA"]},
    {"name": "Amoxicillin",        "type": "Drug",             "aliases": ["Amoxypen", "Clamoxyl"]},
    {"name": "Metformin",          "type": "Drug",             "aliases": ["Glucophage", "Diabesin"]},
    {"name": "Warfarin",           "type": "Drug",             "aliases": ["Marcumar", "Coumadin"]},
    {"name": "Metoprolol",         "type": "Drug",             "aliases": ["Lopressor", "Beloc"]},

    # Krankheiten
    {"name": "Diabetes mellitus",  "type": "Disease",          "aliases": ["Diabetes", "Zuckerkrankheit", "DM"]},
    {"name": "Hypertonie",         "type": "Disease",          "aliases": ["Bluthochdruck", "Arterielle Hypertonie"]},
    {"name": "Herzinfarkt",        "type": "Disease",          "aliases": ["Myokardinfarkt", "Heart Attack"]},
    {"name": "Schlaganfall",       "type": "Disease",          "aliases": ["Apoplex", "Stroke", "Hirninfarkt"]},
    {"name": "COVID-19",           "type": "Disease",          "aliases": ["Corona", "SARS-CoV-2", "Coronavirus"]},
    {"name": "Grippe",             "type": "Disease",          "aliases": ["Influenza", "Flu"]},
    {"name": "Erkältung",          "type": "Disease",          "aliases": ["Rhinitis", "Common Cold"]},

    # Symptome
    {"name": "Schmerz",            "type": "Symptom",          "aliases": ["Schmerzen", "Pain"]},
    {"name": "Kopfschmerzen",      "type": "Symptom",          "aliases": ["Kopfschmerz", "Cephalgie", "Headache"]},
    {"name": "Fieber",             "type": "Symptom",          "aliases": ["Hyperthermie", "Fever"]},
    {"name": "Entzündung",         "type": "Symptom",          "aliases": ["Inflammation", "Entzündungsreaktion"]},

    # Körpersysteme / Anatomie
    {"name": "Herz-Kreislauf-System", "type": "Anatomy",       "aliases": ["Kardiovaskuläres System", "Cardiovascular System"]},
    {"name": "Nervensystem",          "type": "Anatomy",       "aliases": ["Zentralnervensystem", "ZNS", "Nervous System"]},
    {"name": "Immunsystem",           "type": "Anatomy",       "aliases": ["Abwehrsystem", "Immune System"]},
    {"name": "Leber",                 "type": "Anatomy",       "aliases": ["Hepar", "Liver"]},
    {"name": "Niere",                 "type": "Anatomy",       "aliases": ["Ren", "Kidney"]},

    # ── LAW (German law) ─────────────────────────────────────────────────────
    {"name": "Recht",              "type": "Legal_Concept",    "aliases": ["Rechtsgebiet", "Law", "Legislation"]},
    {"name": "Gesetz",             "type": "Legal_Concept",    "aliases": ["Rechtsnorm", "Statute", "Norm"]},
    {"name": "Verordnung",         "type": "Legal_Concept",    "aliases": ["VO", "Regulation", "Erlass"]},
    {"name": "Grundrecht",         "type": "Legal_Concept",    "aliases": ["Verfassungsrecht", "Fundamental Right"]},
    {"name": "Privatrecht",        "type": "Legal_Concept",    "aliases": ["Zivilrecht", "Civil Law"]},
    {"name": "Öffentliches Recht", "type": "Legal_Concept",    "aliases": ["Staatsrecht", "Public Law"]},
    {"name": "Strafrecht",         "type": "Legal_Concept",    "aliases": ["Kriminalrecht", "Criminal Law"]},
    {"name": "Arbeitsrecht",       "type": "Legal_Concept",    "aliases": ["Arbeitnehmerrecht", "Employment Law"]},
    {"name": "Datenschutzrecht",   "type": "Legal_Concept",    "aliases": ["Informationelle Selbstbestimmung", "Data Protection Law"]},
    {"name": "Urheberrecht",       "type": "Legal_Concept",    "aliases": ["Copyright", "Intellectual Property"]},
    {"name": "Vertragsrecht",      "type": "Legal_Concept",    "aliases": ["Schuldrecht", "Contract Law"]},
    {"name": "Mietrecht",          "type": "Legal_Concept",    "aliases": ["Wohnraummietrecht", "Tenancy Law"]},

    # Gesetze & Codes
    {"name": "GG",                 "type": "Law",              "aliases": ["Grundgesetz", "Verfassung", "Basic Law"]},
    {"name": "BGB",                "type": "Law",              "aliases": ["Bürgerliches Gesetzbuch", "Civil Code"]},
    {"name": "StGB",               "type": "Law",              "aliases": ["Strafgesetzbuch", "Criminal Code"]},
    {"name": "DSGVO",              "type": "Law",              "aliases": ["Datenschutz-Grundverordnung", "GDPR", "General Data Protection Regulation"]},
    {"name": "UrhG",               "type": "Law",              "aliases": ["Urheberrechtsgesetz", "Copyright Act"]},
    {"name": "ArbSchG",            "type": "Law",              "aliases": ["Arbeitsschutzgesetz", "Occupational Safety Act"]},
    {"name": "HGB",                "type": "Law",              "aliases": ["Handelsgesetzbuch", "Commercial Code"]},
    {"name": "GmbHG",              "type": "Law",              "aliases": ["GmbH-Gesetz", "GmbH Act"]},

    # Grundrechte (GG)
    {"name": "Meinungsfreiheit",   "type": "Right",            "aliases": ["Redefreiheit", "Art. 5 GG", "Freedom of Expression"]},
    {"name": "Datenschutz",        "type": "Right",            "aliases": ["Recht auf informationelle Selbstbestimmung", "Privacy"]},
    {"name": "Eigentumsrecht",     "type": "Right",            "aliases": ["Eigentum", "Art. 14 GG", "Property Rights"]},
    {"name": "Menschenwürde",      "type": "Right",            "aliases": ["Art. 1 GG", "Human Dignity"]},
    {"name": "Gleichheitsgrundsatz","type": "Right",           "aliases": ["Gleichbehandlung", "Art. 3 GG", "Equality"]},

    # ── COMPUTER SCIENCE & TECHNOLOGY ────────────────────────────────────────
    {"name": "Programmiersprache", "type": "Tech_Concept",     "aliases": ["Sprache", "Coding Language", "Programming Language"]},
    {"name": "Framework",          "type": "Tech_Concept",     "aliases": ["Bibliothek", "Library", "Toolkit"]},
    {"name": "Datenbank",          "type": "Tech_Concept",     "aliases": ["Datenbankystem", "Database", "DBMS"]},
    {"name": "API",                "type": "Tech_Concept",     "aliases": ["Schnittstelle", "Application Programming Interface"]},
    {"name": "Algorithmus",        "type": "Tech_Concept",     "aliases": ["Algorithm", "Verfahren"]},
    {"name": "Container",          "type": "Tech_Concept",     "aliases": ["Docker Container", "Container Image"]},

    # Sprachen
    {"name": "Python",             "type": "Language",         "aliases": ["Python 3", "py"]},
    {"name": "JavaScript",         "type": "Language",         "aliases": ["JS", "ECMAScript", "Node.js"]},
    {"name": "TypeScript",         "type": "Language",         "aliases": ["TS"]},
    {"name": "Rust",               "type": "Language",         "aliases": ["Rust lang"]},
    {"name": "Go",                 "type": "Language",         "aliases": ["Golang", "Go lang"]},
    {"name": "Java",               "type": "Language",         "aliases": ["JVM", "Java SE"]},
    {"name": "C++",                "type": "Language",         "aliases": ["CPP", "C Plus Plus"]},
    {"name": "SQL",                "type": "Language",         "aliases": ["Structured Query Language", "Query Language"]},

    # Frameworks
    {"name": "FastAPI",            "type": "Framework",        "aliases": ["Fast API"]},
    {"name": "Django",             "type": "Framework",        "aliases": ["Django Framework", "Django REST"]},
    {"name": "React",              "type": "Framework",        "aliases": ["React.js", "ReactJS"]},
    {"name": "LangChain",          "type": "Framework",        "aliases": ["LangChain AI"]},
    {"name": "LangGraph",          "type": "Framework",        "aliases": ["LangGraph AI", "LangGraph Agents"]},
    {"name": "PyTorch",            "type": "Framework",        "aliases": ["Torch", "PyTorch ML"]},
    {"name": "TensorFlow",         "type": "Framework",        "aliases": ["TF", "TF2"]},

    # Tools & Konzepte
    {"name": "Docker",             "type": "Tool",             "aliases": ["Docker Engine", "Docker Desktop"]},
    {"name": "Kubernetes",         "type": "Tool",             "aliases": ["K8s", "K8", "Container Orchestration"]},
    {"name": "Git",                "type": "Tool",             "aliases": ["Git VCS", "Version Control"]},
    {"name": "REST API",           "type": "Protocol",         "aliases": ["RESTful API", "REST", "HTTP API"]},
    {"name": "GraphQL",            "type": "Protocol",         "aliases": ["GQL"]},
    {"name": "WebSocket",          "type": "Protocol",         "aliases": ["WS", "Socket"]},

    # KI / ML
    {"name": "LLM",                "type": "AI_Concept",       "aliases": ["Large Language Model", "Sprachmodell", "Großes Sprachmodell"]},
    {"name": "RAG",                "type": "AI_Concept",       "aliases": ["Retrieval Augmented Generation", "Retrieval-Augmented Generation"]},
    {"name": "Embedding",          "type": "AI_Concept",       "aliases": ["Vektor-Embedding", "Vector Embedding", "Einbettung"]},
    {"name": "Transformer",        "type": "AI_Concept",       "aliases": ["Transformer-Architektur", "Attention Mechanism"]},
    {"name": "Fine-Tuning",        "type": "AI_Concept",       "aliases": ["Feinabstimmung", "RLHF", "Supervised Fine-Tuning"]},
    {"name": "Vektordatenbank",    "type": "AI_Concept",       "aliases": ["Vector Database", "Vector Store", "Embedding Store"]},
    {"name": "Ollama",             "type": "Tool",             "aliases": ["Ollama AI", "Local LLM Runner"]},
    {"name": "ChromaDB",           "type": "Tool",             "aliases": ["Chroma", "Chroma Vector DB"]},

    # ── MATHEMATICS & NATURAL SCIENCES ───────────────────────────────────────
    {"name": "Mathematik",         "type": "Science",          "aliases": ["Mathe", "Mathematics", "Math"]},
    {"name": "Physik",             "type": "Science",          "aliases": ["Physics", "Naturlehre"]},
    {"name": "Chemie",             "type": "Science",          "aliases": ["Chemistry", "Chemische Wissenschaft"]},
    {"name": "Algebra",            "type": "Math_Concept",     "aliases": ["Lineare Algebra", "Algebraische Strukturen"]},
    {"name": "Differential rechnung","type": "Math_Concept",   "aliases": ["Ableitung", "Differentiation", "Calculus"]},
    {"name": "Integralrechnung",   "type": "Math_Concept",     "aliases": ["Integration", "Integral Calculus"]},
    {"name": "Statistik",          "type": "Math_Concept",     "aliases": ["Wahrscheinlichkeitsrechnung", "Statistics", "Stochastik"]},
    {"name": "Primzahl",           "type": "Math_Concept",     "aliases": ["Prime", "Primzahlen", "Prime Number"]},
]


# ─── RELATIONSHIPS ───────────────────────────────────────────────────────────

_RELATIONS: List[Dict[str, str]] = [

    # ── MEDICINE: Taxonomy ───────────────────────────────────────────────────
    {"from": "NSAID",              "rel": "IS_A",              "to": "Analgetikum"},
    {"from": "NSAID",              "rel": "IS_A",              "to": "Medikament"},
    {"from": "Analgetikum",        "rel": "IS_A",              "to": "Medikament"},
    {"from": "Antibiotikum",       "rel": "IS_A",              "to": "Medikament"},
    {"from": "Antihypertensivum",  "rel": "IS_A",              "to": "Medikament"},
    {"from": "Antidiabetikum",     "rel": "IS_A",              "to": "Medikament"},
    {"from": "Antikoagulans",      "rel": "IS_A",              "to": "Medikament"},

    {"from": "Ibuprofen",          "rel": "IS_A",              "to": "NSAID"},
    {"from": "Aspirin",            "rel": "IS_A",              "to": "NSAID"},
    {"from": "Paracetamol",        "rel": "IS_A",              "to": "Analgetikum"},
    {"from": "Amoxicillin",        "rel": "IS_A",              "to": "Antibiotikum"},
    {"from": "Metformin",          "rel": "IS_A",              "to": "Antidiabetikum"},
    {"from": "Warfarin",           "rel": "IS_A",              "to": "Antikoagulans"},
    {"from": "Metoprolol",         "rel": "IS_A",              "to": "Antihypertensivum"},

    {"from": "Kopfschmerzen",      "rel": "IS_A",              "to": "Schmerz"},
    {"from": "Fieber",             "rel": "IS_A",              "to": "Symptom"},
    {"from": "Entzündung",         "rel": "IS_A",              "to": "Symptom"},

    # Medikament → Symptom/Krankheit
    {"from": "Ibuprofen",          "rel": "TREATS",            "to": "Schmerz"},
    {"from": "Ibuprofen",          "rel": "TREATS",            "to": "Entzündung"},
    {"from": "Ibuprofen",          "rel": "TREATS",            "to": "Fieber"},
    {"from": "Paracetamol",        "rel": "TREATS",            "to": "Schmerz"},
    {"from": "Paracetamol",        "rel": "TREATS",            "to": "Fieber"},
    {"from": "Aspirin",            "rel": "TREATS",            "to": "Schmerz"},
    {"from": "Aspirin",            "rel": "TREATS",            "to": "Entzündung"},
    {"from": "Metformin",          "rel": "TREATS",            "to": "Diabetes mellitus"},
    {"from": "Metoprolol",         "rel": "TREATS",            "to": "Hypertonie"},
    {"from": "Warfarin",           "rel": "TREATS",            "to": "Schlaganfall"},

    # Wechselwirkungen
    {"from": "Ibuprofen",          "rel": "INTERACTS_WITH",    "to": "Warfarin"},
    {"from": "Ibuprofen",          "rel": "INTERACTS_WITH",    "to": "Aspirin"},
    {"from": "Aspirin",            "rel": "INTERACTS_WITH",    "to": "Warfarin"},

    # Krankheit → Symptom
    {"from": "Grippe",             "rel": "CAUSES",            "to": "Fieber"},
    {"from": "Grippe",             "rel": "CAUSES",            "to": "Schmerz"},
    {"from": "COVID-19",           "rel": "CAUSES",            "to": "Fieber"},
    {"from": "Hypertonie",         "rel": "CAUSES",            "to": "Kopfschmerzen"},
    {"from": "Hypertonie",         "rel": "CAUSES",            "to": "Herzinfarkt"},
    {"from": "Hypertonie",         "rel": "CAUSES",            "to": "Schlaganfall"},
    {"from": "Diabetes mellitus",  "rel": "CAUSES",            "to": "Hypertonie"},

    # Organ-Warnungen
    {"from": "Ibuprofen",          "rel": "AFFECTS",           "to": "Niere"},
    {"from": "Paracetamol",        "rel": "AFFECTS",           "to": "Leber"},
    {"from": "Warfarin",           "rel": "AFFECTS",           "to": "Herz-Kreislauf-System"},

    # ── LAW: Taxonomy ────────────────────────────────────────────────────────
    {"from": "Strafrecht",         "rel": "IS_A",              "to": "Öffentliches Recht"},
    {"from": "Datenschutzrecht",   "rel": "IS_A",              "to": "Öffentliches Recht"},
    {"from": "Vertragsrecht",      "rel": "IS_A",              "to": "Privatrecht"},
    {"from": "Mietrecht",          "rel": "IS_A",              "to": "Privatrecht"},
    {"from": "Mietrecht",          "rel": "IS_A",              "to": "Vertragsrecht"},
    {"from": "Urheberrecht",       "rel": "IS_A",              "to": "Privatrecht"},
    {"from": "Arbeitsrecht",       "rel": "IS_A",              "to": "Privatrecht"},
    {"from": "Grundrecht",         "rel": "IS_A",              "to": "Öffentliches Recht"},

    # Gesetze → Rechtsgebiete
    {"from": "GG",                 "rel": "DEFINES",           "to": "Grundrecht"},
    {"from": "GG",                 "rel": "DEFINES",           "to": "Meinungsfreiheit"},
    {"from": "GG",                 "rel": "DEFINES",           "to": "Menschenwürde"},
    {"from": "GG",                 "rel": "DEFINES",           "to": "Eigentumsrecht"},
    {"from": "GG",                 "rel": "DEFINES",           "to": "Gleichheitsgrundsatz"},
    {"from": "BGB",                "rel": "DEFINES",           "to": "Vertragsrecht"},
    {"from": "BGB",                "rel": "DEFINES",           "to": "Mietrecht"},
    {"from": "StGB",               "rel": "DEFINES",           "to": "Strafrecht"},
    {"from": "DSGVO",              "rel": "DEFINES",           "to": "Datenschutzrecht"},
    {"from": "DSGVO",              "rel": "DEFINES",           "to": "Datenschutz"},
    {"from": "UrhG",               "rel": "DEFINES",           "to": "Urheberrecht"},

    {"from": "Meinungsfreiheit",   "rel": "IS_A",              "to": "Grundrecht"},
    {"from": "Datenschutz",        "rel": "IS_A",              "to": "Grundrecht"},
    {"from": "Eigentumsrecht",     "rel": "IS_A",              "to": "Grundrecht"},
    {"from": "Menschenwürde",      "rel": "IS_A",              "to": "Grundrecht"},
    {"from": "Gleichheitsgrundsatz","rel": "IS_A",             "to": "Grundrecht"},

    # ── TECHNOLOGY: Taxonomy ─────────────────────────────────────────────────
    {"from": "Python",             "rel": "IS_A",              "to": "Programmiersprache"},
    {"from": "JavaScript",         "rel": "IS_A",              "to": "Programmiersprache"},
    {"from": "TypeScript",         "rel": "IS_A",              "to": "Programmiersprache"},
    {"from": "Rust",               "rel": "IS_A",              "to": "Programmiersprache"},
    {"from": "Go",                 "rel": "IS_A",              "to": "Programmiersprache"},
    {"from": "Java",               "rel": "IS_A",              "to": "Programmiersprache"},
    {"from": "C++",                "rel": "IS_A",              "to": "Programmiersprache"},
    {"from": "SQL",                "rel": "IS_A",              "to": "Programmiersprache"},

    {"from": "FastAPI",            "rel": "IS_A",              "to": "Framework"},
    {"from": "Django",             "rel": "IS_A",              "to": "Framework"},
    {"from": "React",              "rel": "IS_A",              "to": "Framework"},
    {"from": "LangChain",          "rel": "IS_A",              "to": "Framework"},
    {"from": "LangGraph",          "rel": "IS_A",              "to": "Framework"},
    {"from": "PyTorch",            "rel": "IS_A",              "to": "Framework"},
    {"from": "TensorFlow",         "rel": "IS_A",              "to": "Framework"},

    {"from": "TypeScript",         "rel": "EXTENDS",           "to": "JavaScript"},
    {"from": "FastAPI",            "rel": "USES",              "to": "Python"},
    {"from": "Django",             "rel": "USES",              "to": "Python"},
    {"from": "LangChain",          "rel": "USES",              "to": "Python"},
    {"from": "LangGraph",          "rel": "DEPENDS_ON",        "to": "LangChain"},
    {"from": "LangGraph",          "rel": "USES",              "to": "Python"},
    {"from": "PyTorch",            "rel": "USES",              "to": "Python"},
    {"from": "TensorFlow",         "rel": "USES",              "to": "Python"},
    {"from": "React",              "rel": "USES",              "to": "JavaScript"},

    {"from": "Kubernetes",         "rel": "USES",              "to": "Docker"},
    {"from": "LangChain",          "rel": "USES",              "to": "LLM"},
    {"from": "RAG",                "rel": "USES",              "to": "Vektordatenbank"},
    {"from": "RAG",                "rel": "USES",              "to": "Embedding"},
    {"from": "LLM",                "rel": "USES",              "to": "Transformer"},
    {"from": "Ollama",             "rel": "RUNS",              "to": "LLM"},
    {"from": "ChromaDB",           "rel": "IS_A",              "to": "Vektordatenbank"},

    {"from": "Docker",             "rel": "IS_A",              "to": "Container"},
    {"from": "Docker",             "rel": "IS_A",              "to": "Tool"},
    {"from": "Kubernetes",         "rel": "IS_A",              "to": "Tool"},

    # ── MATHEMATICS ──────────────────────────────────────────────────────────
    {"from": "Algebra",            "rel": "IS_A",              "to": "Mathematik"},
    {"from": "Differentialrechnung","rel": "IS_A",             "to": "Mathematik"},
    {"from": "Integralrechnung",   "rel": "IS_A",              "to": "Mathematik"},
    {"from": "Statistik",          "rel": "IS_A",              "to": "Mathematik"},
    {"from": "Primzahl",           "rel": "IS_A",              "to": "Mathematik"},
    {"from": "Differentialrechnung","rel": "RELATED_TO",       "to": "Integralrechnung"},

    # ── PROCEDURAL / CAUSAL ──────────────────────────────────────────────────
    # NECESSITATES_PRESENCE: performing an action requires physical presence at a location
    {"from": "Autowaschen",              "rel": "NECESSITATES_PRESENCE", "to": "Waschanlage"},
    {"from": "Autofahrt",                "rel": "NECESSITATES_PRESENCE", "to": "Fahrzeug"},
    {"from": "On-Premises Deployment",   "rel": "NECESSITATES_PRESENCE", "to": "Rechenzentrum"},
    {"from": "Hardware-Installation",    "rel": "NECESSITATES_PRESENCE", "to": "Serverraum"},
    {"from": "Wartung",                  "rel": "NECESSITATES_PRESENCE", "to": "Anlage"},
    # DEPENDS_ON_LOCATION: an action's result depends on a specific location being reachable
    {"from": "Hardware-Installation",    "rel": "DEPENDS_ON_LOCATION",   "to": "Serverraum"},
    {"from": "Remote Deployment",        "rel": "DEPENDS_ON_LOCATION",   "to": "Netzwerkzugang"},
    # ENABLES_ACTION: a condition or resource makes an action possible
    {"from": "Fahrzeugschlüssel",        "rel": "ENABLES_ACTION",        "to": "Autofahrt"},
    {"from": "Netzwerkzugang",           "rel": "ENABLES_ACTION",        "to": "Remote Deployment"},
    {"from": "Admin-Zugang",             "rel": "ENABLES_ACTION",        "to": "On-Premises Deployment"},
    {"from": "SSH-Schlüssel",            "rel": "ENABLES_ACTION",        "to": "Remote Deployment"},
]

# ─── EXTENSION: SOFTWARE DEVELOPMENT, WEB, DEVOPS, GAMES, DATA SCIENCE ──────

_ENTITIES_EXT: List[Dict[str, Any]] = [

    # ── DATA STRUCTURES ───────────────────────────────────────────────────────
    {"name": "Array",           "type": "DataStructure", "aliases": ["Liste", "List", "Feld", "Vector"]},
    {"name": "Linked List",     "type": "DataStructure", "aliases": ["Verkettete Liste", "LinkedList"]},
    {"name": "Stack",           "type": "DataStructure", "aliases": ["Stapel", "LIFO"]},
    {"name": "Queue",           "type": "DataStructure", "aliases": ["Warteschlange", "FIFO"]},
    {"name": "Hash Table",      "type": "DataStructure", "aliases": ["Hashtabelle", "Dictionary", "Map", "HashMap"]},
    {"name": "Baum",            "type": "DataStructure", "aliases": ["Tree", "Baumstruktur", "Binary Tree"]},
    {"name": "Graph",           "type": "DataStructure", "aliases": ["Netzwerk", "Graph-Struktur"]},
    {"name": "Heap",            "type": "DataStructure", "aliases": ["Prioritätswarteschlange", "Priority Queue"]},

    # ── ALGORITHMS ────────────────────────────────────────────────────────────
    {"name": "Sortieralgorithmus", "type": "Algorithm",  "aliases": ["Sorting Algorithm", "Sortierung"]},
    {"name": "QuickSort",       "type": "Algorithm",     "aliases": ["Quick Sort"]},
    {"name": "MergeSort",       "type": "Algorithm",     "aliases": ["Merge Sort"]},
    {"name": "Binäre Suche",    "type": "Algorithm",     "aliases": ["Binary Search", "Binärsuche"]},
    {"name": "BFS",             "type": "Algorithm",     "aliases": ["Breitensuche", "Breadth-First Search"]},
    {"name": "DFS",             "type": "Algorithm",     "aliases": ["Tiefensuche", "Depth-First Search"]},
    {"name": "Dynamische Programmierung", "type": "Algorithm", "aliases": ["Dynamic Programming", "DP", "Memoization"]},
    {"name": "Rekursion",       "type": "Algorithm",     "aliases": ["Recursion", "Rekursiver Aufruf"]},
    {"name": "Big-O-Notation",  "type": "Concept",       "aliases": ["Zeitkomplexität", "Time Complexity", "O-Notation", "Landau-Symbol"]},

    # ── DESIGN PATTERNS ──────────────────────────────────────────────────────
    {"name": "Design Pattern",  "type": "Concept",       "aliases": ["Entwurfsmuster", "Software-Muster"]},
    {"name": "Singleton",       "type": "Pattern",       "aliases": ["Einzelstück-Muster", "Singleton Pattern"]},
    {"name": "Observer",        "type": "Pattern",       "aliases": ["Beobachter-Muster", "Observer Pattern", "Event Listener"]},
    {"name": "Factory",         "type": "Pattern",       "aliases": ["Fabrik-Muster", "Factory Pattern", "Factory Method"]},
    {"name": "MVC",             "type": "Pattern",       "aliases": ["Model-View-Controller", "Model View Controller"]},
    {"name": "Repository Pattern","type": "Pattern",     "aliases": ["Repository", "Data Access Layer"]},
    {"name": "Dependency Injection","type": "Pattern",   "aliases": ["DI", "IoC", "Inversion of Control"]},

    # ── ARCHITECTURE ─────────────────────────────────────────────────────────
    {"name": "Microservices",   "type": "Architecture",  "aliases": ["Microservice", "Micro-Services", "Dienste-Architektur"]},
    {"name": "Monolith",        "type": "Architecture",  "aliases": ["Monolithische Architektur", "Monolithic App"]},
    {"name": "Event-driven",    "type": "Architecture",  "aliases": ["Ereignisgesteuert", "Event-Driven Architecture", "EDA"]},
    {"name": "CQRS",            "type": "Architecture",  "aliases": ["Command Query Responsibility Segregation"]},
    {"name": "Clean Architecture","type": "Architecture","aliases": ["Hexagonal Architecture", "Ports and Adapters"]},
    {"name": "SOLID",           "type": "Principle",     "aliases": ["SOLID Prinzipien", "Object-Oriented Principles"]},
    {"name": "DRY",             "type": "Principle",     "aliases": ["Don't Repeat Yourself", "Redundanzvermeidung"]},
    {"name": "KISS",            "type": "Principle",     "aliases": ["Keep It Simple Stupid", "Einfachheitsprinzip"]},

    # ── WEB DEVELOPMENT ───────────────────────────────────────────────────────
    {"name": "HTML",            "type": "Language",      "aliases": ["HTML5", "HyperText Markup Language", "HTML-Dokument"]},
    {"name": "CSS",             "type": "Language",      "aliases": ["CSS3", "Cascading Style Sheets", "Stylesheet"]},
    {"name": "DOM",             "type": "Tech_Concept",  "aliases": ["Document Object Model", "DOM-Baum", "DOM-Manipulation"]},
    {"name": "Node.js",         "type": "Framework",     "aliases": ["NodeJS", "Node", "Server-Side JavaScript"]},
    {"name": "npm",             "type": "Tool",          "aliases": ["Node Package Manager", "Paketmanager"]},
    {"name": "Webpack",         "type": "Tool",          "aliases": ["Module Bundler", "Web Bundler"]},
    {"name": "Vite",            "type": "Tool",          "aliases": ["Vite.js", "Build Tool"]},
    {"name": "Vue.js",          "type": "Framework",     "aliases": ["Vue", "VueJS", "Vue Framework"]},
    {"name": "Angular",         "type": "Framework",     "aliases": ["AngularJS", "Angular Framework"]},
    {"name": "Next.js",         "type": "Framework",     "aliases": ["NextJS", "Next", "React SSR"]},
    {"name": "Fetch API",       "type": "Protocol",      "aliases": ["fetch()", "HTTP Fetch", "AJAX"]},
    {"name": "LocalStorage",    "type": "Tech_Concept",  "aliases": ["Web Storage", "SessionStorage", "Browser Storage"]},
    {"name": "Cookie",          "type": "Tech_Concept",  "aliases": ["HTTP Cookie", "Browser Cookie", "Session Cookie"]},

    # ── DEVOPS & INFRASTRUCTURE ──────────────────────────────────────────────
    {"name": "CI/CD",           "type": "DevOps",        "aliases": ["Continuous Integration", "Continuous Deployment", "Continuous Delivery", "Pipeline"]},
    {"name": "GitHub Actions",  "type": "Tool",          "aliases": ["GH Actions", "GitHub CI", "Actions Workflow"]},
    {"name": "GitLab CI",       "type": "Tool",          "aliases": ["GitLab Pipeline", ".gitlab-ci.yml"]},
    {"name": "Jenkins",         "type": "Tool",          "aliases": ["Jenkins CI", "Jenkins Pipeline"]},
    {"name": "Nginx",           "type": "Tool",          "aliases": ["nginx", "Reverse Proxy", "Webserver"]},
    {"name": "Traefik",         "type": "Tool",          "aliases": ["Traefik Proxy", "Traefik Load Balancer"]},
    {"name": "Prometheus",      "type": "Tool",          "aliases": ["Prometheus Monitoring", "PromQL"]},
    {"name": "Grafana",         "type": "Tool",          "aliases": ["Grafana Dashboard", "Grafana Metrics"]},
    {"name": "ELK Stack",       "type": "Tool",          "aliases": ["Elasticsearch Logstash Kibana", "Elastic Stack"]},
    {"name": "Load Balancer",   "type": "Tech_Concept",  "aliases": ["Lastverteilung", "Load Balancing", "HAProxy"]},
    {"name": "DNS",             "type": "Protocol",      "aliases": ["Domain Name System", "Namensauflösung", "FQDN"]},
    {"name": "TCP/IP",          "type": "Protocol",      "aliases": ["TCP", "IP-Protokoll", "Netzwerkprotokoll"]},
    {"name": "SSH",             "type": "Protocol",      "aliases": ["Secure Shell", "SSH-Verbindung"]},
    {"name": "TLS",             "type": "Protocol",      "aliases": ["SSL", "HTTPS", "Transport Layer Security", "SSL/TLS"]},
    {"name": "Terraform",       "type": "Tool",          "aliases": ["Infrastructure as Code", "IaC", "HashiCorp Terraform"]},
    {"name": "Ansible",         "type": "Tool",          "aliases": ["Ansible Playbook", "Configuration Management"]},
    {"name": "Helm",            "type": "Tool",          "aliases": ["Helm Chart", "Kubernetes Package Manager"]},

    # ── SECURITY ─────────────────────────────────────────────────────────────
    {"name": "Authentifizierung","type": "Security",     "aliases": ["Authentication", "Anmeldung", "Login", "Auth"]},
    {"name": "Autorisierung",   "type": "Security",      "aliases": ["Authorization", "Zugangskontrolle", "RBAC"]},
    {"name": "JWT",             "type": "Security",      "aliases": ["JSON Web Token", "Bearer Token", "Access Token"]},
    {"name": "OAuth2",          "type": "Security",      "aliases": ["OAuth 2.0", "OAuth", "Authorization Framework"]},
    {"name": "XSS",             "type": "Security",      "aliases": ["Cross-Site Scripting", "Script Injection"]},
    {"name": "SQL Injection",   "type": "Security",      "aliases": ["SQLi", "SQL-Einschleusung", "Injection Attack"]},
    {"name": "CSRF",            "type": "Security",      "aliases": ["Cross-Site Request Forgery", "XSRF"]},
    {"name": "Firewall",        "type": "Security",      "aliases": ["Paketfilter", "Network Firewall", "UFW", "iptables"]},

    # ── GAME DEVELOPMENT ─────────────────────────────────────────────────────
    {"name": "Game Loop",       "type": "Game_Concept",  "aliases": ["Spielschleife", "Update Loop", "Game Tick"]},
    {"name": "Kollisionserkennung","type":"Game_Concept", "aliases": ["Collision Detection", "Hitbox", "Kollision"]},
    {"name": "Sprite",          "type": "Game_Concept",  "aliases": ["Spielfigur", "Spielobjekt", "Game Sprite", "2D-Objekt"]},
    {"name": "Physik-Engine",   "type": "Game_Concept",  "aliases": ["Physics Engine", "Box2D", "Spielphysik"]},
    {"name": "Score",           "type": "Game_Concept",  "aliases": ["Punkte", "Punktestand", "Highscore", "Spielstand"]},
    {"name": "Level",           "type": "Game_Concept",  "aliases": ["Spiellevel", "Stage", "Schwierigkeitsstufe"]},
    {"name": "Spiellogik",      "type": "Game_Concept",  "aliases": ["Game Logic", "Spielmechanik", "Game Mechanic", "Spielregeln"]},
    {"name": "Schwerkraft",     "type": "Game_Concept",  "aliases": ["Gravity", "Fallen", "Falling Mechanic", "Gravitation im Spiel"]},
    {"name": "Spielfeld",       "type": "Game_Concept",  "aliases": ["Game Board", "Grid", "Spielbrett", "Raster"]},
    {"name": "Pygame",          "type": "Framework",     "aliases": ["pygame", "Python Game Library"]},
    {"name": "Unity",           "type": "Tool",          "aliases": ["Unity Engine", "Unity3D", "Unity Game Engine"]},
    {"name": "Godot",           "type": "Tool",          "aliases": ["Godot Engine", "GDScript"]},
    {"name": "Canvas API",      "type": "Tech_Concept",  "aliases": ["HTML Canvas", "2D Canvas", "CanvasRenderingContext2D"]},

    # ── DATA SCIENCE ─────────────────────────────────────────────────────────
    {"name": "pandas",          "type": "Framework",     "aliases": ["Pandas", "pd", "Python pandas", "DataFrame Library"]},
    {"name": "NumPy",           "type": "Framework",     "aliases": ["numpy", "np", "Numerical Python"]},
    {"name": "scikit-learn",    "type": "Framework",     "aliases": ["sklearn", "Scikit Learn", "Machine Learning Library"]},
    {"name": "matplotlib",      "type": "Framework",     "aliases": ["Matplotlib", "plt", "Python Plotting"]},
    {"name": "Jupyter",         "type": "Tool",          "aliases": ["Jupyter Notebook", "JupyterLab", "iPython Notebook"]},
    {"name": "DataFrame",       "type": "Data_Concept",  "aliases": ["Datenrahmen", "Tabellendaten", "Data Frame"]},
    {"name": "Feature Engineering","type":"Data_Concept","aliases": ["Merkmalserstellung", "Feature Extraction"]},
    {"name": "Kreuzvalidierung","type": "Data_Concept",  "aliases": ["Cross-Validation", "k-fold", "Train-Test-Split"]},
    {"name": "Overfitting",     "type": "Data_Concept",  "aliases": ["Überanpassung", "Overfitted Model"]},
    {"name": "Neuronales Netz", "type": "AI_Concept",    "aliases": ["Neural Network", "Künstliches Neuronales Netz", "ANN", "Deep Learning"]},
    {"name": "Backpropagation", "type": "AI_Concept",    "aliases": ["Rückwärtspropagation", "Gradient Backprop"]},
    {"name": "Gradient Descent","type": "AI_Concept",    "aliases": ["Gradientenabstieg", "SGD", "Adam Optimizer"]},

    # ── CREATIVE WRITING ─────────────────────────────────────────────────────
    {"name": "Handlung",        "type": "Narrative",     "aliases": ["Plot", "Story", "Storyline", "Handlungsstrang"]},
    {"name": "Charakter",       "type": "Narrative",     "aliases": ["Character", "Figur", "Protagonist", "Antagonist"]},
    {"name": "Setting",         "type": "Narrative",     "aliases": ["Schauplatz", "Ort der Handlung", "Welt"]},
    {"name": "Konflikt",        "type": "Narrative",     "aliases": ["Conflict", "Spannung", "Dramatik"]},
    {"name": "Dialog",          "type": "Narrative",     "aliases": ["Dialoge", "Gespräch", "Wechselrede"]},
    {"name": "Erzählperspektive","type": "Narrative",    "aliases": ["Narration", "POV", "Point of View", "Ich-Erzähler"]},
    {"name": "Prosa",           "type": "Narrative",     "aliases": ["Fließtext", "Prosatext", "Kurzgeschichte", "Roman"]},
    {"name": "Lyrik",           "type": "Narrative",     "aliases": ["Gedicht", "Poem", "Poetry", "Vers"]},

    # ── EXTENDED MATHEMATICS ─────────────────────────────────────────────────
    {"name": "Matrizenrechnung","type": "Math_Concept",  "aliases": ["Lineare Algebra Matrizen", "Matrix Multiplication", "Matrixoperation"]},
    {"name": "Vektorrechnung",  "type": "Math_Concept",  "aliases": ["Vektoren", "Vektorraum", "Vector Math"]},
    {"name": "Wahrscheinlichkeit","type":"Math_Concept", "aliases": ["Probability", "Wahrscheinlichkeitstheorie", "P(A)"]},
    {"name": "Mengenlehre",     "type": "Math_Concept",  "aliases": ["Set Theory", "Menge", "Schnittmenge", "Vereinigungsmenge"]},
    {"name": "Zahlentheorie",   "type": "Math_Concept",  "aliases": ["Number Theory", "Divisor", "Modulo", "GGT"]},
    {"name": "Optimierung",     "type": "Math_Concept",  "aliases": ["Optimization", "Minimierung", "Maximierung", "Lineares Programm"]},
]

_RELATIONS_EXT: List[Dict[str, str]] = [

    # Datenstrukturen
    {"from": "Array",           "rel": "IS_A",           "to": "DataStructure"},
    {"from": "Linked List",     "rel": "IS_A",           "to": "DataStructure"},
    {"from": "Stack",           "rel": "IS_A",           "to": "DataStructure"},
    {"from": "Queue",           "rel": "IS_A",           "to": "DataStructure"},
    {"from": "Hash Table",      "rel": "IS_A",           "to": "DataStructure"},
    {"from": "Baum",            "rel": "IS_A",           "to": "DataStructure"},
    {"from": "Graph",           "rel": "IS_A",           "to": "DataStructure"},
    {"from": "Heap",            "rel": "IS_A",           "to": "DataStructure"},
    {"from": "Stack",           "rel": "IMPLEMENTS",     "to": "Array"},
    {"from": "Heap",            "rel": "IMPLEMENTS",     "to": "Baum"},

    # Algorithmen
    {"from": "QuickSort",       "rel": "IS_A",           "to": "Sortieralgorithmus"},
    {"from": "MergeSort",       "rel": "IS_A",           "to": "Sortieralgorithmus"},
    {"from": "Sortieralgorithmus","rel": "IS_A",         "to": "Algorithmus"},
    {"from": "Binäre Suche",    "rel": "IS_A",           "to": "Algorithmus"},
    {"from": "BFS",             "rel": "IS_A",           "to": "Algorithmus"},
    {"from": "DFS",             "rel": "IS_A",           "to": "Algorithmus"},
    {"from": "Dynamische Programmierung","rel": "IS_A",  "to": "Algorithmus"},
    {"from": "Rekursion",       "rel": "IS_A",           "to": "Algorithmus"},
    {"from": "BFS",             "rel": "RELATED_TO",     "to": "DFS"},
    {"from": "MergeSort",       "rel": "USES",           "to": "Rekursion"},
    {"from": "Binäre Suche",    "rel": "DEPENDS_ON",     "to": "Array"},
    {"from": "QuickSort",       "rel": "USES",           "to": "Rekursion"},
    {"from": "Dynamische Programmierung","rel": "USES",  "to": "Rekursion"},
    {"from": "BFS",             "rel": "USES",           "to": "Queue"},
    {"from": "DFS",             "rel": "USES",           "to": "Stack"},
    {"from": "BFS",             "rel": "USES",           "to": "Graph"},
    {"from": "DFS",             "rel": "USES",           "to": "Graph"},

    # Design Patterns
    {"from": "Singleton",       "rel": "IS_A",           "to": "Design Pattern"},
    {"from": "Observer",        "rel": "IS_A",           "to": "Design Pattern"},
    {"from": "Factory",         "rel": "IS_A",           "to": "Design Pattern"},
    {"from": "MVC",             "rel": "IS_A",           "to": "Design Pattern"},
    {"from": "Repository Pattern","rel": "IS_A",         "to": "Design Pattern"},
    {"from": "Dependency Injection","rel": "IS_A",       "to": "Design Pattern"},
    {"from": "MVC",             "rel": "RELATED_TO",     "to": "Clean Architecture"},

    # Architektur
    {"from": "Microservices",   "rel": "IS_A",           "to": "Architecture"},
    {"from": "Monolith",        "rel": "IS_A",           "to": "Architecture"},
    {"from": "Event-driven",    "rel": "IS_A",           "to": "Architecture"},
    {"from": "CQRS",            "rel": "IS_A",           "to": "Architecture"},
    {"from": "Clean Architecture","rel": "IS_A",         "to": "Architecture"},
    {"from": "Microservices",   "rel": "USES",           "to": "Docker"},
    {"from": "Microservices",   "rel": "USES",           "to": "Kubernetes"},
    {"from": "Event-driven",    "rel": "USES",           "to": "Kafka"},
    {"from": "SOLID",           "rel": "IS_A",           "to": "Principle"},
    {"from": "DRY",             "rel": "IS_A",           "to": "Principle"},
    {"from": "KISS",            "rel": "IS_A",           "to": "Principle"},

    # Web
    {"from": "HTML",            "rel": "IS_A",           "to": "Language"},
    {"from": "CSS",             "rel": "IS_A",           "to": "Language"},
    {"from": "Node.js",         "rel": "IS_A",           "to": "Framework"},
    {"from": "Node.js",         "rel": "USES",           "to": "JavaScript"},
    {"from": "Vue.js",          "rel": "IS_A",           "to": "Framework"},
    {"from": "Vue.js",          "rel": "USES",           "to": "JavaScript"},
    {"from": "Angular",         "rel": "IS_A",           "to": "Framework"},
    {"from": "Angular",         "rel": "USES",           "to": "TypeScript"},
    {"from": "Next.js",         "rel": "IS_A",           "to": "Framework"},
    {"from": "Next.js",         "rel": "DEPENDS_ON",     "to": "React"},
    {"from": "Webpack",         "rel": "IS_A",           "to": "Tool"},
    {"from": "Vite",            "rel": "IS_A",           "to": "Tool"},
    {"from": "DOM",             "rel": "USES",           "to": "HTML"},
    {"from": "JavaScript",      "rel": "USES",           "to": "DOM"},
    {"from": "Canvas API",      "rel": "USES",           "to": "HTML"},
    {"from": "Canvas API",      "rel": "USES",           "to": "JavaScript"},

    # DevOps
    {"from": "GitHub Actions",  "rel": "IS_A",           "to": "CI/CD"},
    {"from": "GitLab CI",       "rel": "IS_A",           "to": "CI/CD"},
    {"from": "Jenkins",         "rel": "IS_A",           "to": "CI/CD"},
    {"from": "Nginx",           "rel": "IS_A",           "to": "Tool"},
    {"from": "Traefik",         "rel": "IS_A",           "to": "Tool"},
    {"from": "Prometheus",      "rel": "IS_A",           "to": "Tool"},
    {"from": "Grafana",         "rel": "IS_A",           "to": "Tool"},
    {"from": "Grafana",         "rel": "USES",           "to": "Prometheus"},
    {"from": "Terraform",       "rel": "IS_A",           "to": "Tool"},
    {"from": "Ansible",         "rel": "IS_A",           "to": "Tool"},
    {"from": "Helm",            "rel": "IS_A",           "to": "Tool"},
    {"from": "Helm",            "rel": "USES",           "to": "Kubernetes"},
    {"from": "Nginx",           "rel": "IMPLEMENTS",     "to": "Load Balancer"},
    {"from": "Traefik",         "rel": "IMPLEMENTS",     "to": "Load Balancer"},
    {"from": "TLS",             "rel": "IS_A",           "to": "Protocol"},
    {"from": "SSH",             "rel": "IS_A",           "to": "Protocol"},
    {"from": "DNS",             "rel": "IS_A",           "to": "Protocol"},
    {"from": "TCP/IP",          "rel": "IS_A",           "to": "Protocol"},
    {"from": "CI/CD",           "rel": "USES",           "to": "Git"},
    {"from": "CI/CD",           "rel": "USES",           "to": "Docker"},

    # Sicherheit
    {"from": "JWT",             "rel": "IS_A",           "to": "Authentifizierung"},
    {"from": "OAuth2",          "rel": "IS_A",           "to": "Authentifizierung"},
    {"from": "OAuth2",          "rel": "USES",           "to": "JWT"},
    {"from": "TLS",             "rel": "IMPLEMENTS",     "to": "Authentifizierung"},
    {"from": "XSS",             "rel": "RELATED_TO",     "to": "CSRF"},
    {"from": "SQL Injection",   "rel": "RELATED_TO",     "to": "XSS"},

    # Spielentwicklung
    {"from": "Game Loop",       "rel": "IS_A",           "to": "Game_Concept"},
    {"from": "Kollisionserkennung","rel": "IS_A",        "to": "Game_Concept"},
    {"from": "Sprite",          "rel": "IS_A",           "to": "Game_Concept"},
    {"from": "Physik-Engine",   "rel": "IS_A",           "to": "Game_Concept"},
    {"from": "Spielfeld",       "rel": "IS_A",           "to": "Game_Concept"},
    {"from": "Spiellogik",      "rel": "IS_A",           "to": "Game_Concept"},
    {"from": "Schwerkraft",     "rel": "IS_A",           "to": "Game_Concept"},
    {"from": "Schwerkraft",     "rel": "RELATED_TO",     "to": "Spiellogik"},
    {"from": "Kollisionserkennung","rel": "USES",        "to": "Spielfeld"},
    {"from": "Spiellogik",      "rel": "USES",           "to": "Spielfeld"},
    {"from": "Game Loop",       "rel": "USES",           "to": "Spiellogik"},
    {"from": "Pygame",          "rel": "IS_A",           "to": "Framework"},
    {"from": "Pygame",          "rel": "USES",           "to": "Python"},
    {"from": "Unity",           "rel": "IS_A",           "to": "Tool"},
    {"from": "Godot",           "rel": "IS_A",           "to": "Tool"},
    {"from": "Canvas API",      "rel": "USES",           "to": "Spielfeld"},
    {"from": "Canvas API",      "rel": "RELATED_TO",     "to": "Game Loop"},

    # Data Science
    {"from": "pandas",          "rel": "IS_A",           "to": "Framework"},
    {"from": "pandas",          "rel": "USES",           "to": "Python"},
    {"from": "pandas",          "rel": "USES",           "to": "NumPy"},
    {"from": "NumPy",           "rel": "IS_A",           "to": "Framework"},
    {"from": "NumPy",           "rel": "USES",           "to": "Python"},
    {"from": "scikit-learn",    "rel": "IS_A",           "to": "Framework"},
    {"from": "scikit-learn",    "rel": "USES",           "to": "Python"},
    {"from": "scikit-learn",    "rel": "USES",           "to": "NumPy"},
    {"from": "matplotlib",      "rel": "IS_A",           "to": "Framework"},
    {"from": "matplotlib",      "rel": "USES",           "to": "Python"},
    {"from": "DataFrame",       "rel": "IS_A",           "to": "Data_Concept"},
    {"from": "pandas",          "rel": "IMPLEMENTS",     "to": "DataFrame"},
    {"from": "Kreuzvalidierung","rel": "IS_A",           "to": "Data_Concept"},
    {"from": "Feature Engineering","rel": "IS_A",        "to": "Data_Concept"},
    {"from": "Neuronales Netz", "rel": "IS_A",           "to": "AI_Concept"},
    {"from": "Backpropagation", "rel": "IS_A",           "to": "AI_Concept"},
    {"from": "Gradient Descent","rel": "IS_A",           "to": "AI_Concept"},
    {"from": "Backpropagation", "rel": "USES",           "to": "Gradient Descent"},
    {"from": "Neuronales Netz", "rel": "USES",           "to": "Backpropagation"},
    {"from": "Fine-Tuning",     "rel": "USES",           "to": "Gradient Descent"},
    {"from": "PyTorch",         "rel": "IMPLEMENTS",     "to": "Neuronales Netz"},
    {"from": "TensorFlow",      "rel": "IMPLEMENTS",     "to": "Neuronales Netz"},

    # Mathematik
    {"from": "Matrizenrechnung","rel": "IS_A",           "to": "Mathematik"},
    {"from": "Vektorrechnung",  "rel": "IS_A",           "to": "Mathematik"},
    {"from": "Wahrscheinlichkeit","rel": "IS_A",         "to": "Mathematik"},
    {"from": "Mengenlehre",     "rel": "IS_A",           "to": "Mathematik"},
    {"from": "Zahlentheorie",   "rel": "IS_A",           "to": "Mathematik"},
    {"from": "Optimierung",     "rel": "IS_A",           "to": "Mathematik"},
    {"from": "Matrizenrechnung","rel": "RELATED_TO",     "to": "Algebra"},
    {"from": "Statistik",       "rel": "USES",           "to": "Wahrscheinlichkeit"},
    {"from": "Gradient Descent","rel": "USES",           "to": "Optimierung"},
    {"from": "Vektorrechnung",  "rel": "RELATED_TO",     "to": "Matrizenrechnung"},
]

# Zusammenführung
# ─── PROCEDURAL DOMAIN ───────────────────────────────────────────────────────
# Entities representing physical actions, locations, and enabling conditions.
# Used by the causal learning loop for procedural world-rule extraction.

_ENTITIES_PROC: List[Dict[str, Any]] = [
    {"name": "Action",                "type": "Action",    "aliases": ["Handlung", "Vorgang", "Aktion", "operation", "task"]},
    {"name": "Autowaschen",           "type": "Action",    "aliases": ["car wash", "washing car", "Auto waschen"]},
    {"name": "Autofahrt",             "type": "Action",    "aliases": ["driving", "Fahren", "car trip"]},
    {"name": "On-Premises Deployment","type": "Action",    "aliases": ["on-prem deploy", "lokales Deployment"]},
    {"name": "Hardware-Installation", "type": "Action",    "aliases": ["hardware install", "server setup", "Hardwareeinbau"]},
    {"name": "Remote Deployment",     "type": "Action",    "aliases": ["remote deploy", "ferngesteuertes Deployment"]},
    {"name": "Wartung",               "type": "Action",    "aliases": ["maintenance", "Instandhaltung", "servicing"]},

    {"name": "Location",              "type": "Location",  "aliases": ["Ort", "Standort", "place", "site", "Stelle"]},
    {"name": "Waschanlage",           "type": "Location",  "aliases": ["car wash facility", "Autowaschanlage", "Waschstraße"]},
    {"name": "Fahrzeug",              "type": "Location",  "aliases": ["vehicle", "Auto", "car", "Kraftfahrzeug"]},
    {"name": "Rechenzentrum",         "type": "Location",  "aliases": ["data center", "datacenter", "DC", "Serverstandort"]},
    {"name": "Serverraum",            "type": "Location",  "aliases": ["server room", "machine room", "Maschinenraum"]},
    {"name": "Anlage",                "type": "Location",  "aliases": ["facility", "plant", "Betriebsanlage"]},

    {"name": "Condition",             "type": "Condition", "aliases": ["Bedingung", "Voraussetzung", "prerequisite", "requirement"]},
    {"name": "Fahrzeugschlüssel",     "type": "Condition", "aliases": ["car key", "Autoschlüssel", "Schlüssel"]},
    {"name": "Netzwerkzugang",        "type": "Condition", "aliases": ["network access", "Netzwerkverbindung", "connectivity"]},
    {"name": "Admin-Zugang",          "type": "Condition", "aliases": ["admin access", "root access", "Administratorzugang"]},
    {"name": "SSH-Schlüssel",         "type": "Condition", "aliases": ["SSH key", "private key", "ssh keypair"]},
]

_ENTITIES  = _ENTITIES  + _ENTITIES_EXT + _ENTITIES_PROC
_RELATIONS = _RELATIONS + _RELATIONS_EXT


# ─── PUBLIC API ──────────────────────────────────────────────────────────────

ONTOLOGY: Dict[str, Any] = {
    "entities": _ENTITIES,
    "relations": _RELATIONS,
}
