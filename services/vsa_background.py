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
        
    def cleanup(self, query_result: np.ndarray, threshold: float = 0.25) -> list[tuple[str, float]]:
        """Matches a noisy vector against the vocabulary and returns matches above threshold."""
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
