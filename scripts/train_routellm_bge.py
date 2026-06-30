import os
import json
import httpx
import asyncio
import numpy as np

# Config
DATASET_PATH = os.getenv("ROUTELLM_DATASET_PATH", "/app/data/routellm/train.jsonl")
EMBED_URL = os.getenv("MOE_EMBED_URL", "http://moe-embed:11434").rstrip("/") + "/api/embed"
MODEL_NAME = "bge-m3"
SUBSET_SIZE = int(os.getenv("ROUTELLM_SUBSET_SIZE", "1000"))  # Number of samples to train on for a fast local baseline
BATCH_SIZE = 16
EPOCHS = 1000
LEARNING_RATE = 0.5
WEIGHTS_PATH = os.getenv("ROUTELLM_WEIGHTS_PATH", "/app/models/routellm_bge_weights.json")

async def get_embeddings_batch(client, prompts):
    payload = {
        "model": MODEL_NAME,
        "input": prompts
    }
    try:
        response = await client.post(EMBED_URL, json=payload, timeout=60.0)
        if response.status_code == 200:
            return response.json().get("embeddings", [])
        else:
            print(f"Error fetching embeddings: {response.status_code} - {response.text}")
    except Exception as e:
        print(f"Exception fetching embeddings: {e}")
    return []

# Sigmoid function with clamping to avoid overflow
def sigmoid(z):
    return 1.0 / (1.0 + np.exp(-np.clip(z, -50, 50)))

async def main():
    if not os.path.exists(DATASET_PATH):
        print(f"Dataset not found at {DATASET_PATH}")
        return

    print(f"Loading first {SUBSET_SIZE} samples from {DATASET_PATH}...")
    dataset = []
    with open(DATASET_PATH, "r", encoding="utf-8") as f:
        for idx, line in enumerate(f):
            if idx >= SUBSET_SIZE:
                break
            try:
                dataset.append(json.loads(line))
            except Exception as e:
                print(f"Failed to parse line {idx}: {e}")

    print(f"Loaded {len(dataset)} samples.")
    
    prompts = [item["prompt"] for item in dataset]
    # Binary label: 1 if mixtral_score < 5 (requires strong model), 0 if mixtral_score == 5 (weak model is fine)
    labels = np.array([1.0 if item.get("mixtral_score", 0) < 5 else 0.0 for item in dataset], dtype=np.float32)
    
    print(f"Label distribution: Strong model (1.0) = {np.sum(labels == 1.0)}, Weak model (0.0) = {np.sum(labels == 0.0)}")

    print("Fetching BGE-M3 embeddings in batches from local moe-embed container...")
    embeddings = []
    
    async with httpx.AsyncClient() as client:
        for i in range(0, len(prompts), BATCH_SIZE):
            batch_prompts = prompts[i:i+BATCH_SIZE]
            print(f"Processing batch {i//BATCH_SIZE + 1} / {-(-len(prompts)//BATCH_SIZE)}...", end="\r", flush=True)
            batch_embeds = await get_embeddings_batch(client, batch_prompts)
            if len(batch_embeds) != len(batch_prompts):
                # Fallback: retry individually for safety if a batch fails
                print(f"\nWarning: Batch size mismatch ({len(batch_embeds)} != {len(batch_prompts)}), retrying individually...")
                for p in batch_prompts:
                    embeds = await get_embeddings_batch(client, [p])
                    if embeds:
                        embeddings.append(embeds[0])
                    else:
                        embeddings.append([0.0] * 1024)
            else:
                embeddings.extend(batch_embeds)
    
    print("\nEmbeddings fetched successfully.")
    X = np.array(embeddings, dtype=np.float32)
    y = labels
    
    # Split train/validation (80/20)
    split_idx = int(len(X) * 0.8)
    X_train, X_val = X[:split_idx], X[split_idx:]
    y_train, y_val = y[:split_idx], y[split_idx:]
    
    print(f"Training set: {len(X_train)} samples, Validation set: {len(X_val)} samples.")

    # Initialize weights and bias
    np.random.seed(42)
    w = np.random.normal(0, 0.01, (1024,)).astype(np.float32)
    b = 0.0
    
    print("Training Logistic Regression classifier using Gradient Descent...")
    best_val_loss = float("inf")
    best_weights = None
    best_bias = None

    for epoch in range(EPOCHS):
        # Forward pass train
        logits_train = np.dot(X_train, w) + b
        probs_train = sigmoid(logits_train)
        
        # Loss train
        loss_train = -np.mean(y_train * np.log(probs_train + 1e-15) + (1.0 - y_train) * np.log(1.0 - probs_train + 1e-15))
        
        # Gradients
        dw = np.dot(X_train.T, (probs_train - y_train)) / len(y_train)
        db = np.mean(probs_train - y_train)
        
        # L2 Regularization (weight decay)
        dw += 1e-4 * w
        
        # Update weights
        w -= LEARNING_RATE * dw
        b -= LEARNING_RATE * db
        
        # Validation evaluation
        if (epoch + 1) % 50 == 0 or epoch == 0:
            logits_val = np.dot(X_val, w) + b
            probs_val = sigmoid(logits_val)
            loss_val = -np.mean(y_val * np.log(probs_val + 1e-15) + (1.0 - y_val) * np.log(1.0 - probs_val + 1e-15))
            
            preds_train = (probs_train >= 0.5).astype(np.float32)
            preds_val = (probs_val >= 0.5).astype(np.float32)
            acc_train = np.mean(preds_train == y_train) * 100
            acc_val = np.mean(preds_val == y_val) * 100
            
            print(f"Epoch {epoch+1:04d} | Train Loss: {loss_train:.4f} | Val Loss: {loss_val:.4f} | Train Acc: {acc_train:.2f}% | Val Acc: {acc_val:.2f}%")
            
            if loss_val < best_val_loss:
                best_val_loss = loss_val
                best_weights = w.copy()
                best_bias = b

    print(f"\nTraining completed. Best validation loss: {best_val_loss:.4f}")
    
    # Save the trained weights to JSON
    os.makedirs(os.path.dirname(WEIGHTS_PATH), exist_ok=True)
    weights_data = {
        "w": best_weights.tolist(),
        "b": float(best_bias)
    }
    with open(WEIGHTS_PATH, "w", encoding="utf-8") as f:
        json.dump(weights_data, f, indent=2)
        
    print(f"Trained weights exported successfully to {WEIGHTS_PATH}")

if __name__ == "__main__":
    asyncio.run(main())
