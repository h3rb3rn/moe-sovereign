import os
import sys
import json
import logging
import uuid
import random
from datetime import datetime, timezone
from typing import Any, Callable, Dict, List, Tuple

# Add parent directory to sys.path
SCRIPT_DIR = os.path.dirname(os.path.abspath(__file__))
PROJECT_ROOT = os.path.dirname(SCRIPT_DIR)
if PROJECT_ROOT not in sys.path:
    sys.path.append(PROJECT_ROOT)

from admin_ui.database import _get_pool
from services.dynamic_router import _template_collection, _get_cluster_state

logging.basicConfig(level=logging.INFO, format="%(asctime)s [Eurisko] %(message)s")
logger = logging.getLogger("eurisko-optimizer")

HEURISTICS_FILE = os.path.join(PROJECT_ROOT, "data", "eurisko_heuristics.json")


class Heuristic:
    """Represents a self-referential mutator heuristic in Lenat's Eurisko concept."""
    def __init__(
        self,
        name: str,
        mutation_fn: Callable[[dict], dict],
        weight: float = 1.0,
        metadata: dict = None
    ):
        self.name = name
        self.mutate = mutation_fn
        self.weight = weight
        self.metadata = metadata or {}

    def to_dict(self) -> dict:
        return {
            "name": self.name,
            "weight": self.weight,
            "metadata": self.metadata
        }


class HeuristicBreeder:
    """Manages, breeds, and adapts heuristics based on historical performance logs."""
    def __init__(self):
        self.heuristics: Dict[str, Heuristic] = {}
        self._init_default_heuristics()
        self.load_heuristics()

    def _init_default_heuristics(self):
        # Heuristic 1: Context window scaling (1.5x)
        self.heuristics["context_scaling_1.5x"] = Heuristic(
            name="context_scaling_1.5x",
            mutation_fn=lambda cfg: self._scale_context(cfg, 1.5),
            weight=1.0,
            metadata={"description": "Scale context window by 1.5x", "type": "context"}
        )
        
        # Heuristic 2: Option tweaking (Enable Web Research and GraphRAG)
        def _enable_features(cfg: dict) -> dict:
            mutated = dict(cfg)
            mutated["enable_web_research"] = True
            mutated["enable_graphrag"] = True
            return mutated
            
        self.heuristics["enable_context_features"] = Heuristic(
            name="enable_context_features",
            mutation_fn=_enable_features,
            weight=1.0,
            metadata={"description": "Enable GraphRAG and Web Research for completeness", "type": "feature"}
        )

        # Heuristic 3: VSA HABE boost
        def _enable_habe(cfg: dict) -> dict:
            mutated = dict(cfg)
            mutated["enable_habe"] = True
            return mutated

        self.heuristics["enable_vsa_habe"] = Heuristic(
            name="enable_vsa_habe",
            mutation_fn=_enable_habe,
            weight=1.0,
            metadata={"description": "Enable Holographic Background Engine (HABE)", "type": "vsa"}
        )

    def _scale_context(self, config: dict, scale: float) -> dict:
        mutated = dict(config)
        if "planner_num_ctx" in mutated and mutated["planner_num_ctx"]:
            mutated["planner_num_ctx"] = int(mutated["planner_num_ctx"] * scale)
        if "judge_num_ctx" in mutated and mutated["judge_num_ctx"]:
            mutated["judge_num_ctx"] = int(mutated["judge_num_ctx"] * scale)
        if "experts" in mutated and isinstance(mutated["experts"], dict):
            for exp_name, exp_cfg in mutated["experts"].items():
                if isinstance(exp_cfg, dict) and "context_window" in exp_cfg and exp_cfg["context_window"]:
                    exp_cfg["context_window"] = int(exp_cfg["context_window"] * scale)
        return mutated

    def load_heuristics(self):
        if not os.path.exists(HEURISTICS_FILE):
            return
        try:
            with open(HEURISTICS_FILE, "r", encoding="utf-8") as f:
                data = json.load(f)
            for h_name, h_data in data.items():
                if h_name in self.heuristics:
                    self.heuristics[h_name].weight = h_data.get("weight", 1.0)
                    self.heuristics[h_name].metadata = h_data.get("metadata", {})
                else:
                    # Reconstruct spawned/bred heuristics dynamically if metadata contains details
                    if h_data.get("metadata", {}).get("bred_from"):
                        self._spawn_bred_heuristic(h_name, h_data)
            logger.info(f"Loaded {len(self.heuristics)} Eurisko heuristics (weights adapted).")
        except Exception as e:
            logger.error(f"Failed to load heuristics from {HEURISTICS_FILE}: {e}")

    def save_heuristics(self):
        try:
            os.makedirs(os.path.dirname(HEURISTICS_FILE), exist_ok=True)
            serializable = {k: v.to_dict() for k, v in self.heuristics.items()}
            with open(HEURISTICS_FILE, "w", encoding="utf-8") as f:
                json.dump(serializable, f, indent=2)
            logger.debug(f"Saved Eurisko heuristics state to {HEURISTICS_FILE}.")
        except Exception as e:
            logger.error(f"Failed to save heuristics: {e}")

    def _spawn_bred_heuristic(self, name: str, data: dict):
        """Spawns a bred heuristic combining multiple mutations."""
        parents = data["metadata"]["bred_from"]
        
        def _bred_mutation(cfg: dict) -> dict:
            mutated = dict(cfg)
            for parent_name in parents:
                if parent_name in self.heuristics:
                    mutated = self.heuristics[parent_name].mutate(mutated)
            return mutated
            
        self.heuristics[name] = Heuristic(
            name=name,
            mutation_fn=_bred_mutation,
            weight=data.get("weight", 1.0),
            metadata=data.get("metadata", {})
        )

    def breed_heuristics(self) -> str:
        """Eurisko Self-Reference: Breeds a new heuristic from the top performing ones."""
        # Find active heuristics sorted by weight
        sorted_h = sorted(self.heuristics.values(), key=lambda h: h.weight, reverse=True)
        if len(sorted_h) < 2:
            return ""
            
        parent_a = sorted_h[0]
        parent_b = sorted_h[1]
        
        new_name = f"bred_{parent_a.name}_and_{parent_b.name}"
        if new_name in self.heuristics:
            return ""
            
        logger.info(f"🧬 Breed: Breeding new heuristic '{new_name}' from parents ({parent_a.name}, {parent_b.name})")
        
        # Spawn child
        child_data = {
            "weight": 1.0,
            "metadata": {
                "bred_from": [parent_a.name, parent_b.name],
                "description": f"Bred combination of {parent_a.name} and {parent_b.name}",
                "generated_at": datetime.now(timezone.utc).isoformat()
            }
        }
        self._spawn_bred_heuristic(new_name, child_data)
        self.save_heuristics()
        return new_name

    def select_heuristic(self) -> Heuristic:
        """Selects a heuristic using roulette-wheel selection on weights."""
        h_list = list(self.heuristics.values())
        weights = [max(0.01, h.weight) for h in h_list]
        total = sum(weights)
        r = random.uniform(0, total)
        upto = 0.0
        for h, w in zip(h_list, weights):
            if upto + w >= r:
                return h
            upto += w
        return h_list[0]

    def update_weight(self, name: str, success: bool):
        """Adapts heuristic weight based on mutation success."""
        if name not in self.heuristics:
            return
        h = self.heuristics[name]
        old_w = h.weight
        if success:
            h.weight = round(h.weight * 1.15, 3)  # reward successful mutator
        else:
            h.weight = round(max(0.1, h.weight * 0.85), 3)  # penalize failing mutator
        logger.info(f"⚖️ Adapt: Heuristic '{name}' weight adjusted: {old_w} -> {h.weight}")
        self.save_heuristics()


async def mutate_template(config: dict, breeder: HeuristicBreeder) -> Tuple[dict, str]:
    """Mutate template configuration using a selected Eurisko heuristic."""
    # 1. Select best mutator heuristically
    h = breeder.select_heuristic()
    logger.info(f"🎲 Selected mutator heuristic: '{h.name}' (weight={h.weight})")
    
    # 2. Mutate config
    mutated = h.mutate(config)
    
    # 3. Model Upgrade fallback (always available as static base heuristic)
    try:
        models = await _get_cluster_state()
        if models:
            models.sort(key=lambda m: m.get("model_name", ""), reverse=True)
            best_model = models[0]["model_id"]
            if best_model:
                mutated["planner_model"] = best_model
                mutated["judge_model"] = best_model
    except Exception as e:
        logger.warning(f"Could not retrieve cluster state for model upgrade: {e}")
        
    return mutated, h.name


async def run_optimization_loop():
    logger.info("Starting self-referential Eurisko template optimization loop...")
    pool = _get_pool()
    if not pool:
        logger.error("Postgres pool not available.")
        return
        
    breeder = HeuristicBreeder()
    
    # Breed new heuristics if weight metrics are high
    sorted_h = sorted(breeder.heuristics.values(), key=lambda h: h.weight, reverse=True)
    if sorted_h and sorted_h[0].weight > 1.5:
        breeder.breed_heuristics()

    async with pool.connection() as conn:
        async with conn.cursor() as cur:
            # 1. Fetch template feedback history to adjust weights of heuristics
            # Find recently optimized templates and adjust breeder weights based on feedback
            await cur.execute(
                "SELECT config_json, user_rating FROM dynamic_template_feedback_log "
                "WHERE status='optimized_by_eurisko' AND user_rating IS NOT NULL"
            )
            feedback_rows = await cur.fetchall()
            for r in feedback_rows:
                try:
                    cfg = json.loads(r[0])
                    applied_mutator = cfg.get("eurisko_mutator")
                    rating = r[1]
                    if applied_mutator and rating is not None:
                        success = rating >= 4  # positive user feedback (4 or 5)
                        breeder.update_weight(applied_mutator, success)
                except Exception:
                    pass

            # 2. Fetch bad dynamic templates (user_rating < 3 or negative rating)
            await cur.execute(
                "SELECT template_id, prompt, config_json, user_rating FROM dynamic_template_feedback_log "
                "WHERE user_rating < 3 AND status != 'optimized_by_eurisko'"
            )
            rows = await cur.fetchall()
            logger.info(f"Discovered {len(rows)} bad templates with negative user ratings.")
            
            for row in rows:
                tmpl_id = row[0]
                prompt = row[1]
                config_json = row[2]
                
                try:
                    config = json.loads(config_json)
                    if "eurisko_optimized" in config:
                        continue
                        
                    logger.info(f"Optimizing template {tmpl_id} for prompt: '{prompt[:50]}...'")
                    
                    # Mutate using Breeder
                    mutated_config, mutator_name = await mutate_template(config, breeder)
                    mutated_config["eurisko_optimized"] = True
                    mutated_config["eurisko_mutator"] = mutator_name
                    mutated_config["original_template_id"] = tmpl_id
                    
                    new_id = f"moe-dyn-opt-{uuid.uuid4().hex[:8]}"
                    mutated_config["id"] = new_id
                    new_config_json = json.dumps(mutated_config)
                    now = datetime.now(timezone.utc).isoformat()
                    
                    name = f"Eurisko Optimized {new_id[-4:]}"
                    desc = f"Eurisko mutated template using {mutator_name} to replace bad template {tmpl_id}"
                    
                    # Insert new template
                    await cur.execute(
                        "INSERT INTO admin_expert_templates (id, name, description, config_json, is_active, created_at, updated_at) "
                        "VALUES (%s, %s, %s, %s, TRUE, %s, %s)",
                        (new_id, name, desc, new_config_json, now, now)
                    )
                    
                    # Deactivate old template
                    await cur.execute(
                        "UPDATE admin_expert_templates SET is_active=FALSE WHERE id=%s", (tmpl_id,)
                    )
                    
                    # Update log status
                    await cur.execute(
                        "UPDATE dynamic_template_feedback_log SET status='optimized_by_eurisko', config_json=%s WHERE template_id=%s",
                        (new_config_json, tmpl_id)
                    )
                    
                    # Update ChromaDB cache
                    if _template_collection is not None:
                        try:
                            _template_collection.delete(ids=[tmpl_id])
                            _template_collection.add(
                                ids=[new_id],
                                documents=[prompt],
                                metadatas=[{"name": name, "description": desc}]
                            )
                            logger.info(f"ChromaDB cache updated: redirected prompt to new optimized template {new_id}")
                        except Exception as ce:
                            logger.error(f"Failed to update ChromaDB cache during optimization: {ce}")
                            
                    logger.info(f"Successfully optimized and replaced template {tmpl_id} with mutated template {new_id}")
                    
                except Exception as ex:
                    logger.error(f"Error optimizing template {tmpl_id}: {ex}", exc_info=True)
                    
    logger.info("Eurisko template optimization loop complete.")


if __name__ == "__main__":
    import asyncio
    asyncio.run(run_optimization_loop())
