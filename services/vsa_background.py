import numpy as np
import logging

logger = logging.getLogger(__name__)

class HolographicBackgroundEngine:
    """
    Holographic Ambient Background Engine (HABE) using Vector Symbolic Architectures (VSA).
    
    Implements Holographic Reduced Representations (HRR) to bind, bundle, and retrieve
    semantic context (GraphRAG structures, cache histories) directly in high-dimensional vector space.
    """
    def __init__(self, dimension: int = 2048):
        self.dimension = dimension
        self.vocab = {}
        
    def save_vocab(self, filepath: str) -> None:
        """Saves the vocabulary to a JSON file (converting numpy arrays to lists)."""
        import json
        try:
            serializable = {k: v.tolist() for k, v in self.vocab.items()}
            with open(filepath, "w", encoding="utf-8") as f:
                json.dump(serializable, f, indent=2)
            logger.info(f"VSA: Saved vocabulary with {len(self.vocab)} entries to {filepath}.")
        except Exception as e:
            logger.error(f"VSA: Failed to save vocabulary to {filepath}: {e}")

    def load_vocab(self, filepath: str) -> bool:
        """Loads the vocabulary from a JSON file."""
        import json
        import os
        if not os.path.exists(filepath):
            return False
        try:
            with open(filepath, "r", encoding="utf-8") as f:
                data = json.load(f)
            self.vocab = {k: np.array(v, dtype=np.float64) for k, v in data.items()}
            logger.info(f"VSA: Loaded vocabulary with {len(self.vocab)} entries from {filepath}.")
            return True
        except Exception as e:
            logger.error(f"VSA: Failed to load vocabulary from {filepath}: {e}")
            return False

        
    def get_or_create_vector(self, concept_name: str) -> np.ndarray:
        """Retrieves the vector for a concept from the vocabulary, or generates a new one."""
        if concept_name not in self.vocab:
            # HRR vector generation: Normal distribution scaled by 1/sqrt(dim)
            vec = np.random.normal(0, 1.0 / np.sqrt(self.dimension), self.dimension)
            # Normalise to unit length
            self.vocab[concept_name] = vec / np.linalg.norm(vec)
        return self.vocab[concept_name]
        
    def bind(self, x: np.ndarray, y: np.ndarray) -> np.ndarray:
        """Binds two vectors using circular convolution."""
        # circular convolution computed efficiently in frequency domain using FFT
        return np.fft.ifft(np.fft.fft(x) * np.fft.fft(y)).real
        
    def unbind(self, bound: np.ndarray, x: np.ndarray) -> np.ndarray:
        """Unbinds x from a bound vector using the approximate inverse (involution)."""
        # Involution is the circular shift of the reversed vector
        x_inv = np.roll(x[::-1], 1)
        return self.bind(bound, x_inv)
        
    def bundle(self, vectors: list[np.ndarray]) -> np.ndarray:
        """Bundles multiple vectors using superposition (addition and normalization)."""
        if not vectors:
            return np.zeros(self.dimension)
        sum_vec = np.sum(vectors, axis=0)
        norm = np.linalg.norm(sum_vec)
        if norm == 0:
            return sum_vec
        return sum_vec / norm
        
    def cosine_similarity(self, x: np.ndarray, y: np.ndarray) -> float:
        """Computes the cosine similarity between two vectors."""
        denom = np.linalg.norm(x) * np.linalg.norm(y)
        if denom == 0:
            return 0.0
        return float(np.dot(x, y) / denom)
        
    def cleanup(self, query_result: np.ndarray, threshold: float = None, num_bundled: int = None) -> list[tuple[str, float]]:
        """Matches a noisy vector against the vocabulary and returns matches above threshold.
        
        If threshold is None, auto-calculates the threshold dynamically using statistical outlier
        detection or based on the number of bundled elements:
            threshold = C * sqrt(N / D)
        where C = 3.0 and D is the vector dimension. If num_bundled is not provided, it falls back
        to a statistical estimate based on the noise floor of random decoy vectors.
        """
        if threshold is None:
            if num_bundled is not None and num_bundled > 0:
                # Theoretical threshold based on SNR decay
                threshold = 3.0 * np.sqrt(num_bundled / self.dimension)
            else:
                # Empirical calibration: generate random decoy vectors to measure the noise floor
                decoys = []
                for _ in range(30):
                    v = np.random.normal(0, 1.0 / np.sqrt(self.dimension), self.dimension)
                    decoys.append(v / np.linalg.norm(v))
                similarities = [self.cosine_similarity(query_result, d) for d in decoys]
                # Outlier detection: mean + 3.5 * std of noise
                threshold = float(np.mean(similarities) + 3.5 * np.std(similarities))
            
            # Clamp threshold to sensible minimum/maximum limits
            threshold = max(0.12, min(0.45, threshold))

        matches = []
        for name, vec in self.vocab.items():
            sim = self.cosine_similarity(query_result, vec)
            if sim >= threshold:
                matches.append((name, sim))
        return sorted(matches, key=lambda x: x[1], reverse=True)


    def compile_graph_to_vsa(self, triples: list[tuple[str, str, str]]) -> np.ndarray:
        """
        Compresses a list of GraphRAG triples (subject, predicate, object) 
        into a single Holographic Ambient Vector (HAV).
        """
        bound_triples = []
        for s, p, o in triples:
            v_s = self.get_or_create_vector(f"subj:{s}")
            v_p = self.get_or_create_vector(f"pred:{p}")
            v_o = self.get_or_create_vector(f"obj:{o}")
            
            # Bind Subject * Predicate * Object
            bound = self.bind(self.bind(v_s, v_p), v_o)
            bound_triples.append(bound)
            
        logger.info(f"VSA: Bound {len(triples)} triples into a unified vector space.")
        return self.bundle(bound_triples)

    def query_vsa_relation(self, hav: np.ndarray, subject: str, predicate: str) -> list[tuple[str, float]]:
        """
        Queries the Holographic Ambient Vector (HAV) algebraically to find
        associated objects: obj = HAV * Inv(subj * pred).
        """
        v_s = self.vocab.get(f"subj:{subject}")
        v_p = self.vocab.get(f"pred:{predicate}")
        
        if v_s is None or v_p is None:
            return []
            
        query_key = self.bind(v_s, v_p)
        retrieved_noisy_obj = self.unbind(hav, query_key)
        
        # Cleanup to find matching objects
        matches = self.cleanup(retrieved_noisy_obj)
        # Filter for object namespace tags only
        filtered_matches = [
            (name.split("obj:", 1)[1], sim)
            for name, sim in matches
            if name.startswith("obj:")
        ]
        return filtered_matches
