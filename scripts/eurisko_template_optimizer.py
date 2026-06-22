import os
import sys
import json
import logging
import uuid
from datetime import datetime, timezone

# Add parent directory to sys.path
SCRIPT_DIR = os.path.dirname(os.path.abspath(__file__))
PROJECT_ROOT = os.path.dirname(SCRIPT_DIR)
if PROJECT_ROOT not in sys.path:
    sys.path.append(PROJECT_ROOT)

from admin_ui.database import _get_pool
from services.dynamic_router import _template_collection, _get_cluster_state

logging.basicConfig(level=logging.INFO, format="%(asctime)s [Eurisko] %(message)s")
logger = logging.getLogger("eurisko-optimizer")

async def mutate_template(config: dict) -> dict:
    """Mutate template configuration by applying Eurisko self-discovery heuristic changes.
    
    1. Context Window Expansion: bad ratings are often due to context truncation. Expand expert context by 50%.
    2. Model Upgrade: swap default models to more capable alternatives if available.
    3. Option Tweaks: enable cache or toggle graphrag/web research based on query indicators.
    """
    mutated = dict(config)
    
    # Mutation 1: Context window expansion (prevent truncation)
    if "planner_num_ctx" in mutated and mutated["planner_num_ctx"]:
        mutated["planner_num_ctx"] = int(mutated["planner_num_ctx"] * 1.5)
    if "judge_num_ctx" in mutated and mutated["judge_num_ctx"]:
        mutated["judge_num_ctx"] = int(mutated["judge_num_ctx"] * 1.5)
        
    if "experts" in mutated and isinstance(mutated["experts"], dict):
        for exp_name, exp_cfg in mutated["experts"].items():
            if isinstance(exp_cfg, dict) and "context_window" in exp_cfg and exp_cfg["context_window"]:
                exp_cfg["context_window"] = int(exp_cfg["context_window"] * 1.5)
                
    # Mutation 2: Toggle features for robustness
    mutated["enable_cache"] = True  # Always cache optimized templates
    
    # Mutation 3: Upgrade models from cluster state
    try:
        models = await _get_cluster_state()
        if models:
            # Find largest available model for Planner/Judge
            models.sort(key=lambda m: m.get("model_name", ""), reverse=True) # Simple heuristic sorting
            best_model = models[0]["model_id"]
            if best_model:
                mutated["planner_model"] = best_model
                mutated["judge_model"] = best_model
    except Exception as e:
        logger.warning(f"Could not retrieve cluster state for model upgrade mutation: {e}")
        
    return mutated

async def run_optimization_loop():
    logger.info("Starting Eurisko heuristic template optimization loop...")
    pool = _get_pool()
    if not pool:
        logger.error("Postgres pool not available.")
        return
        
    async with pool.connection() as conn:
        async with conn.cursor() as cur:
            # 1. Fetch bad dynamic templates (user_rating < 0)
            await cur.execute(
                "SELECT template_id, prompt, config_json, user_rating FROM dynamic_template_feedback_log "
                "WHERE user_rating < 0"
            )
            rows = await cur.fetchall()
            logger.info(f"Discovered {len(rows)} bad templates with negative user ratings.")
            
            for row in rows:
                tmpl_id = row[0]
                prompt = row[1]
                config_json = row[2]
                
                try:
                    config = json.loads(config_json)
                    # Check if already optimized
                    if "eurisko_optimized" in config:
                        continue
                        
                    logger.info(f"Optimizing template {tmpl_id} for prompt: '{prompt[:50]}...'")
                    
                    # 2. Mutate
                    mutated_config = await mutate_template(config)
                    mutated_config["eurisko_optimized"] = True
                    mutated_config["original_template_id"] = tmpl_id
                    
                    # 3. Save as new optimized template
                    new_id = f"moe-dyn-opt-{uuid.uuid4().hex[:8]}"
                    mutated_config["id"] = new_id
                    new_config_json = json.dumps(mutated_config)
                    now = datetime.now(timezone.utc).isoformat()
                    
                    name = f"Eurisko Optimized {new_id[-4:]}"
                    desc = f"Eurisko mutated template to replace bad template {tmpl_id}"
                    
                    # Insert new template
                    await cur.execute(
                        "INSERT INTO admin_expert_templates (id, name, description, config_json, is_active, created_at, updated_at) "
                        "VALUES (%s, %s, %s, %s, TRUE, %s, %s)",
                        (new_id, name, desc, new_config_json, now, now)
                    )
                    
                    # 4. Deactivate the old bad template
                    await cur.execute(
                        "UPDATE admin_expert_templates SET is_active=FALSE WHERE id=%s", (tmpl_id,)
                    )
                    
                    # 5. Update the feedback log entry status to marked optimized
                    await cur.execute(
                        "UPDATE dynamic_template_feedback_log SET status='optimized_by_eurisko' WHERE template_id=%s", (tmpl_id,)
                    )
                    
                    # 6. Update ChromaDB Template Cache if available
                    if _template_collection is not None:
                        try:
                            # First delete old entry
                            _template_collection.delete(ids=[tmpl_id])
                            # Insert new entry
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
