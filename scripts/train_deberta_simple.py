import os
import sys
import json
import time
import numpy as np
import torch
from torch.utils.data import DataLoader, TensorDataset
from transformers import AutoTokenizer, AutoModelForSequenceClassification

SCRIPT_DIR = os.path.dirname(os.path.abspath(__file__))
PROJECT_ROOT = os.path.dirname(SCRIPT_DIR)
if PROJECT_ROOT not in sys.path:
    sys.path.append(PROJECT_ROOT)

COMPLEXITY_CLASSES = ["trivial", "moderate", "complex", "memory_recall"]
class_to_idx = {c: i for i, c in enumerate(COMPLEXITY_CLASSES)}

from complexity_estimator import estimate_complexity

def main():
    # Limit CPU threads to single-thread to avoid OpenMP deadlock/spin-lock thrashing
    torch.set_num_threads(1)
    torch.set_num_interop_threads(1)

    print("Loading seeds...", flush=True)
    seeds_file = os.path.join(PROJECT_ROOT, "router_dataset_seed.json")
    with open(seeds_file, "r", encoding="utf-8") as f:
        seeds = json.load(f)

    # Label them
    data = []
    for item in seeds:
        prompt = item["prompt"]
        label = estimate_complexity(prompt)
        data.append((prompt, class_to_idx[label]))

    print(f"Loaded {len(data)} samples.", flush=True)

    # Tokenizer
    model_name = "microsoft/deberta-v3-small"
    print("Loading tokenizer...", flush=True)
    tokenizer = AutoTokenizer.from_pretrained(model_name)

    print("Tokenizing data...", flush=True)
    texts = [x[0] for x in data]
    labels = [x[1] for x in data]
    encodings = tokenizer(texts, truncation=True, padding=True, max_length=64, return_tensors="pt")

    dataset = TensorDataset(encodings["input_ids"], encodings["attention_mask"], torch.tensor(labels))
    dataloader = DataLoader(dataset, batch_size=4, shuffle=True)

    print("Loading model...", flush=True)
    model = AutoModelForSequenceClassification.from_pretrained(model_name, num_labels=4)
    optimizer = torch.optim.AdamW(model.parameters(), lr=5e-5)

    print("Starting training loop...", flush=True)
    model.train()
    for epoch in range(1):
        for step, batch in enumerate(dataloader):
            t0 = time.time()
            input_ids, attention_mask, batch_labels = batch
            optimizer.zero_grad()
            outputs = model(input_ids=input_ids, attention_mask=attention_mask, labels=batch_labels)
            loss = outputs.loss
            loss.backward()
            optimizer.step()
            print(f"Step {step+1}/{len(dataloader)} | Loss: {loss.item():.4f} | Time: {time.time()-t0:.2f}s", flush=True)
            break

    print("Saving tokenizer...", flush=True)
    models_dir = os.path.join(PROJECT_ROOT, "models")
    os.makedirs(models_dir, exist_ok=True)
    tokenizer_dir = os.path.join(models_dir, "complexity_tokenizer")
    os.makedirs(tokenizer_dir, exist_ok=True)
    tokenizer.save_pretrained(tokenizer_dir)

    print("Exporting to ONNX...", flush=True)
    model.eval()
    dummy_model_input = tokenizer("This is a dummy input", return_tensors="pt")
    onnx_path = os.path.join(models_dir, "complexity_deberta.onnx")
    torch.onnx.export(
        model,
        (dummy_model_input["input_ids"], dummy_model_input["attention_mask"]),
        onnx_path,
        input_names=["input_ids", "attention_mask"],
        output_names=["logits"],
        dynamic_axes={
            "input_ids": {0: "batch_size", 1: "sequence_length"},
            "attention_mask": {0: "batch_size", 1: "sequence_length"},
            "logits": {0: "batch_size"},
        },
        opset_version=12,
    )
    print(f"DeBERTa complexity classifier trained and exported to {onnx_path}", flush=True)

if __name__ == "__main__":
    main()
