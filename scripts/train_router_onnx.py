import os
import argparse
import json
import re
import torch
import torch.nn as nn
import torch.optim as optim
from torch.utils.data import DataLoader, TensorDataset
from transformers import AutoTokenizer, AutoModel
import torch.nn.functional as F
import numpy as np

# Unique lists for classes
EXPERT_CLASSES = [
    "code_reviewer",
    "creative_writer",
    "creative_writing",
    "data_analysis",
    "general",
    "legal_advisor",
    "mail_classify",
    "math",
    "precision_tools",
    "reasoning",
    "research",
    "science",
    "technical_support",
    "tool_agent"
]

COMPLEXITY_CLASSES = ["trivial", "moderate", "complex", "memory_recall"]
GATE_CLASSES = ["web_research", "graphrag"]

# Heuristic helpers to generate training targets
_MEMORY_RECALL_RE = re.compile(
    r'\b(was habe ich (gesagt|erwähnt|genannt)|what did i (say|tell|mention)|'
    r'ich habe (gesagt|erwähnt|genannt|dir gesagt)|i (said|told you|mentioned)|'
    r'wie hieß|wie war|du hast|you said|you told me|'
    r'aus unserem (gespräch|chat|dialog)|in our (conversation|chat|session)|'
    r'erinner(e|st) dich|kannst du dich erinnern|remember when|remember what|'
    r'ich habe (dir )?vorhin|weißt du noch|do you remember|'
    r'welche (datenbank|port|ip|adresse|name|version|limit|schlüssel|key|team)'
    r'\s+(habe ich|hatte ich|hab ich|have i|did i)\b)\b',
    re.I,
)

_COMPLEX_MARKERS = re.compile(
    r'\b(vergleiche?n?|analysiere?n?|erkläre? warum|untersuche?n?|bewerte?n?|evaluiere?n?|'
    r'entwirf|entwickle?n?|plane?n?|implementiere?n?|refaktoriere?n?|optimiere?n?|'
    r'unterschied|vor- und nachteile?|pros? and cons?|step[- ]by[- ]step|'
    r'schritt für schritt|warum|wie genau|inwiefern|welche auswirkungen|'
    r'compare|analyze|explain why|evaluate|design|implement|optimize)\b',
    re.I,
)

_RESEARCH_MARKERS = re.compile(
    r'\b(paper|article|study|studies|journal|publication|published|according to|'
    r'researcher|professor|author|et al\.?|arxiv|doi|isbn|pubchem|orcid|'
    r'database|dataset|classification|compound|species|genus|wikipedia|'
    r'museum|collection|archive|standard|regulation|nonnative|invasive|'
    r'transcript|video|episode|season|series|channel)\b',
    re.I,
)

_TRIVIAL_MARKERS = re.compile(
    r'^(was ist|what is|wer ist|who is|wann ist|when is|wo ist|where is|'
    r'wie viel|how much|wie viele|how many|nenne|list|zeige mir|show me|'
    r'übersetze?|translate)\b',
    re.I,
)

_DOMAIN_MARKERS = re.compile(
    r'\b(§+\s*\d+|bgh|bverfg|awmf|s3-leitlinie?|icd-\d+|dosierung|wirkstoff|'
    r'differentialdiagnose?|subnetz|cidr|bgp|ospf|ldap|oauth|openid|'
    r'integral|ableitung|differentialgleichung|eigenwert|fourier|'
    r'sql|cypher|neo4j|docker|kubernetes|terraform|ansible)\b',
    re.I,
)

_CODE_MARKERS = re.compile(
    r'```|`[^`]+`|\bdef \b|\bclass \b|\bfunction\b|\bimport \b|'
    r'\{["\']|\[\s*\{|<[a-z]+>|#!/',
    re.I,
)

_WEB_INTENT_RE = re.compile(
    r'\b(aktuell|news|wetter|latest|recent|web search|online|search the web|internet|'
    r'google|heute|today|recent events|current affairs|suche im web|aktuellste)\b',
    re.I,
)

def get_complexity(query: str) -> str:
    n = len(query.split())
    if _MEMORY_RECALL_RE.search(query):
        return "memory_recall"
    if n >= 80 or _COMPLEX_MARKERS.search(query) or _RESEARCH_MARKERS.search(query):
        return "complex"
    if n <= 15 and _TRIVIAL_MARKERS.search(query):
        return "trivial"
    if n <= 8 and not _DOMAIN_MARKERS.search(query) and not _CODE_MARKERS.search(query):
        return "trivial"
    has_domain = bool(_DOMAIN_MARKERS.search(query))
    has_code = bool(_CODE_MARKERS.search(query))
    if has_domain or has_code:
        return "moderate"
    return "moderate"

def get_gates(query: str, complexity: str) -> tuple[float, float]:
    if complexity in ("trivial", "memory_recall"):
        return 0.0, 0.0
    web_research = 0.0
    if complexity == "complex" or _WEB_INTENT_RE.search(query):
        web_research = 1.0
    graphrag = 1.0
    return web_research, graphrag

# ── Multi-Task Neural Network ────────────────────────────────────────────────
class SovereignRouterClassifier(nn.Module):
    def __init__(self, input_dim=384, num_experts=14, num_complexities=4, num_gates=2):
        super().__init__()
        self.shared = nn.Sequential(
            nn.Linear(input_dim, 256),
            nn.LayerNorm(256),
            nn.ReLU(),
            nn.Dropout(0.1),
            nn.Linear(256, 128),
            nn.LayerNorm(128),
            nn.ReLU(),
            nn.Dropout(0.1)
        )
        self.expert_head = nn.Linear(128, num_experts)
        self.complexity_head = nn.Linear(128, num_complexities)
        self.gate_head = nn.Linear(128, num_gates)
        
    def forward(self, x):
        features = self.shared(x)
        experts = self.expert_head(features)
        complexity = self.complexity_head(features)
        gates = self.gate_head(features)
        return experts, complexity, gates

# Mean pooling for embeddings
def mean_pooling(model_output, attention_mask):
    token_embeddings = model_output[0]
    input_mask_expanded = attention_mask.unsqueeze(-1).expand(token_embeddings.size()).float()
    return torch.sum(token_embeddings * input_mask_expanded, 1) / torch.clamp(input_mask_expanded.sum(1), min=1e-9)

def main():
    parser = argparse.ArgumentParser(description="Train Sovereign Router Multi-Task Classifier and export to ONNX")
    parser.add_argument("--dataset_path", type=str, default="/home/user/synthetic_router_dataset.json", help="Path to JSON dataset")
    parser.add_argument("--output_onnx", type=str, default="sovereign_router.onnx", help="Path to output ONNX file")
    parser.add_argument("--epochs", type=int, default=30, help="Training epochs")
    parser.add_argument("--batch_size", type=int, default=32, help="Batch size")
    parser.add_argument("--lr", type=float, default=1e-3, help="Learning rate")
    args = parser.parse_args()
    
    print(f"Loading dataset from {args.dataset_path}...")
    with open(args.dataset_path, "r", encoding="utf-8") as f:
        dataset = json.load(f)
        
    print(f"Loaded {len(dataset)} samples. Generating features and embeddings...")
    
    # Load SentenceTransformer / MiniLM tokenizer and model
    device = torch.device("cuda" if torch.cuda.is_available() else "cpu")
    print(f"Using device: {device}")
    
    embed_model_name = "sentence-transformers/all-MiniLM-L6-v2"
    local_path = "/scratch/project_465003058/hornphil/data/all-MiniLM-L6-v2"
    if os.path.exists(local_path):
        print(f"Loading embedding model from local directory: {local_path}")
        embed_model_name = local_path
        tokenizer = AutoTokenizer.from_pretrained(embed_model_name, local_files_only=True)
        embed_model = AutoModel.from_pretrained(embed_model_name, local_files_only=True).to(device)
    else:
        print(f"Loading embedding model from Hugging Face Hub: {embed_model_name}")
        tokenizer = AutoTokenizer.from_pretrained(embed_model_name)
        embed_model = AutoModel.from_pretrained(embed_model_name).to(device)
    embed_model.eval()
    
    prompts = [item["prompt"] for item in dataset]
    embeddings_list = []
    
    # Generate embeddings in batches
    batch_size_embed = 64
    for i in range(0, len(prompts), batch_size_embed):
        batch_prompts = prompts[i:i+batch_size_embed]
        encoded = tokenizer(batch_prompts, padding=True, truncation=True, max_length=256, return_tensors="pt").to(device)
        with torch.no_grad():
            outputs = embed_model(**encoded)
            pooled = mean_pooling(outputs, encoded["attention_mask"])
            normalized = F.normalize(pooled, p=2, dim=1)
            embeddings_list.append(normalized.cpu().numpy())
            
    embeddings = np.concatenate(embeddings_list, axis=0)
    
    # Map classes to indices
    expert_to_idx = {name: idx for idx, name in enumerate(EXPERT_CLASSES)}
    complexity_to_idx = {name: idx for idx, name in enumerate(COMPLEXITY_CLASSES)}
    
    # Prepare target labels
    y_experts = np.zeros((len(dataset), len(EXPERT_CLASSES)), dtype=np.float32)
    y_complexity = np.zeros((len(dataset),), dtype=np.int64)
    y_gates = np.zeros((len(dataset), len(GATE_CLASSES)), dtype=np.float32)
    
    for idx, item in enumerate(dataset):
        prompt = item["prompt"]
        domains = item.get("expert_domains", [])
        
        # Expert multi-label
        for d in domains:
            if d in expert_to_idx:
                y_experts[idx, expert_to_idx[d]] = 1.0
                
        # Complexity classification
        comp = get_complexity(prompt)
        y_complexity[idx] = complexity_to_idx[comp]
        
        # Gates multi-label
        web_res, grag = get_gates(prompt, comp)
        y_gates[idx, 0] = web_res
        y_gates[idx, 1] = grag
        
    # Build PyTorch DataLoader
    dataset_pt = TensorDataset(
        torch.tensor(embeddings, dtype=torch.float32),
        torch.tensor(y_experts, dtype=torch.float32),
        torch.tensor(y_complexity, dtype=torch.long),
        torch.tensor(y_gates, dtype=torch.float32)
    )
    
    loader = DataLoader(dataset_pt, batch_size=args.batch_size, shuffle=True)
    
    # Initialize Multi-Task model
    model = SovereignRouterClassifier().to(device)
    optimizer = optim.AdamW(model.parameters(), lr=args.lr, weight_decay=1e-4)
    
    # Loss functions
    criterion_experts = nn.BCEWithLogitsLoss()
    criterion_complexity = nn.CrossEntropyLoss()
    criterion_gates = nn.BCEWithLogitsLoss()
    
    print("Training Multi-Task gating classifier...")
    model.train()
    for epoch in range(args.epochs):
        epoch_loss = 0.0
        for x_batch, y_exp_batch, y_comp_batch, y_gate_batch in loader:
            x_batch = x_batch.to(device)
            y_exp_batch = y_exp_batch.to(device)
            y_comp_batch = y_comp_batch.to(device)
            y_gate_batch = y_gate_batch.to(device)
            
            optimizer.zero_grad()
            
            out_experts, out_complexity, out_gates = model(x_batch)
            
            loss_exp = criterion_experts(out_experts, y_exp_batch)
            loss_comp = criterion_complexity(out_complexity, y_comp_batch)
            loss_gates = criterion_gates(out_gates, y_gate_batch)
            
            loss = loss_exp + loss_comp + loss_gates
            loss.backward()
            optimizer.step()
            
            epoch_loss += loss.item() * x_batch.size(0)
            
        print(f"Epoch {epoch+1}/{args.epochs} - Loss: {epoch_loss/len(dataset):.4f}")
        
    print("Training completed. Exporting to ONNX format...")
    model.eval()
    
    # Trace with CPU dummy input for high compatibility
    dummy_input = torch.randn(1, 384).to(device)
    torch.onnx.export(
        model,
        dummy_input,
        args.output_onnx,
        input_names=["input_embedding"],
        output_names=["experts", "complexity", "gates"],
        dynamic_axes={
            "input_embedding": {0: "batch_size"},
            "experts": {0: "batch_size"},
            "complexity": {0: "batch_size"},
            "gates": {0: "batch_size"}
        },
        opset_version=12
    )
    print(f"ONNX model successfully saved to: {args.output_onnx}")

if __name__ == "__main__":
    main()
