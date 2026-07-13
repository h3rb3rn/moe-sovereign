import os
import sys
import json
import random
import argparse
import numpy as np
import torch
from torch.utils.data import Dataset
from transformers import AutoTokenizer, AutoModelForSequenceClassification, Trainer, TrainingArguments
from sklearn.model_selection import train_test_split

# Add project root to sys.path to import complexity_estimator
SCRIPT_DIR = os.path.dirname(os.path.abspath(__file__))
PROJECT_ROOT = os.path.dirname(SCRIPT_DIR)
if PROJECT_ROOT not in sys.path:
    sys.path.append(PROJECT_ROOT)

COMPLEXITY_CLASSES = ["trivial", "moderate", "complex", "memory_recall"]
class_to_idx = {c: i for i, c in enumerate(COMPLEXITY_CLASSES)}

from complexity_estimator import estimate_complexity

def augment_text(text):
    templates = [
        "{}",
        "please {}",
        "can you {}",
        "hey, {}",
        "tell me {}",
        "{}",
        "{}?",
        "{}...",
        "write {}",
        "explain {}",
        "solve {}",
    ]
    augmented = []
    # Add original
    augmented.append(text)
    # Add variations
    for t in random.sample(templates, 0):
        augmented.append(t.format(text))
    return list(set(augmented))

class SimpleDataset(Dataset):
    def __init__(self, encodings, labels):
        self.encodings = encodings
        self.labels = labels

    def __getitem__(self, idx):
        item = {key: torch.tensor(val[idx]) for key, val in self.encodings.items()}
        item['labels'] = torch.tensor(self.labels[idx])
        return item

    def __len__(self):
        return len(self.labels)

def main():
    # Optimizations for CPU training speed and resource limits
    torch.set_num_threads(4)
    torch.set_num_interop_threads(4)
    
    parser = argparse.ArgumentParser()
    parser.add_argument("--epochs", type=int, default=1)
    parser.add_argument("--batch_size", type=int, default=32)
    args = parser.parse_args()

    # Load seeds
    seeds_file = os.path.join(PROJECT_ROOT, "router_dataset_seed.json")
    print(f"Loading seeds from {seeds_file}...")
    with open(seeds_file, "r", encoding="utf-8") as f:
        seeds = json.load(f)

    # Generate dataset
    data = []
    for item in seeds:
        prompt = item["prompt"]
        label = estimate_complexity(prompt)
        # Augment
        for augmented_prompt in augment_text(prompt):
            data.append((augmented_prompt, class_to_idx[label]))

    print(f"Generated {len(data)} samples from seeds after augmentation.")

    # Split
    texts = [x[0] for x in data]
    labels = [x[1] for x in data]
    train_texts, val_texts, train_labels, val_labels = train_test_split(texts, labels, test_size=0.2, random_state=42)

    # Tokenizer
    model_name = "microsoft/deberta-v3-small"
    tokenizer = AutoTokenizer.from_pretrained(model_name)

    train_encodings = tokenizer(train_texts, truncation=True, padding=True, max_length=64)
    val_encodings = tokenizer(val_texts, truncation=True, padding=True, max_length=64)

    train_dataset = SimpleDataset(train_encodings, train_labels)
    val_dataset = SimpleDataset(val_encodings, val_labels)

    # Model
    model = AutoModelForSequenceClassification.from_pretrained(model_name, num_labels=4)

    # Train
    training_args = TrainingArguments(
        output_dir="./results",
        num_train_epochs=args.epochs,
        per_device_train_batch_size=args.batch_size,
        per_device_eval_batch_size=args.batch_size,
        warmup_ratio=0.1,
        weight_decay=0.01,
        logging_dir="./logs",
        logging_steps=10,
        eval_strategy="epoch",
        save_strategy="epoch",
        load_best_model_at_end=True,
        metric_for_best_model="accuracy",
    )

    def compute_metrics(eval_pred):
        logits, labels = eval_pred
        predictions = np.argmax(logits, axis=-1)
        return {"accuracy": (predictions == labels).mean()}

    trainer = Trainer(
        model=model,
        args=training_args,
        train_dataset=train_dataset,
        eval_dataset=val_dataset,
        compute_metrics=compute_metrics,
    )

    trainer.train()

    # Save best model and tokenizer
    models_dir = os.path.join(PROJECT_ROOT, "models")
    os.makedirs(models_dir, exist_ok=True)
    tokenizer_dir = os.path.join(models_dir, "complexity_tokenizer")
    os.makedirs(tokenizer_dir, exist_ok=True)
    tokenizer.save_pretrained(tokenizer_dir)

    # Export to ONNX
    model.eval()
    dummy_model_input = tokenizer("This is a dummy input", return_tensors="pt")
    
    # Export to ONNX
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
    print(f"DeBERTa complexity classifier trained and exported to {onnx_path}")

if __name__ == "__main__":
    main()
