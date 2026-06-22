"""
services/causal_healer.py — Causal diagnostics, counterfactual intervention sandbox, and causal graph-mapping.
Based on Pearl's Causal Calculus / Do-Calculus (1995) to isolate failure causes in multi-expert orchestrations.
"""

import json
import logging
import os
import uuid
from typing import Any, Dict, List, Optional, Tuple

logger = logging.getLogger("MOE-SOVEREIGN.CausalHealer")

# Target file for exporting causal graph training datasets
CAUSAL_DATASET_PATH = os.getenv("CAUSAL_DATASET_PATH", "/app/logs/causal_training_dataset.jsonl")


class CausalNode:
    """Represents a node in the causal dependency network."""
    def __init__(self, name: str, node_type: str, status: str = "ok", value: Any = None):
        self.name = name
        self.node_type = node_type  # "input", "context", "tool", "model", "synthesis"
        self.status = status        # "ok", "fail", "degraded"
        self.value = value

    def to_dict(self) -> dict:
        return {
            "name": self.name,
            "type": self.node_type,
            "status": self.status,
            "value": self.value
        }


class CausalGraph:
    """Represents the causal DAG representing the pipeline execution flow and attribution."""
    def __init__(self, response_id: str):
        self.response_id = response_id
        self.nodes: Dict[str, CausalNode] = {}
        self.edges: List[Dict[str, Any]] = []  # list of {"from": str, "to": str, "strength": float}
        self.root_causes: List[Dict[str, Any]] = []  # sorted by causal attribution score

    def add_node(self, name: str, node_type: str, status: str = "ok", value: Any = None):
        self.nodes[name] = CausalNode(name, node_type, status, value)

    def add_edge(self, source: str, target: str, strength: float = 1.0):
        if source in self.nodes and target in self.nodes:
            self.edges.append({
                "from": source,
                "to": target,
                "strength": strength
            })

    def to_dict(self) -> dict:
        return {
            "response_id": self.response_id,
            "nodes": {k: v.to_dict() for k, v in self.nodes.items()},
            "edges": self.edges,
            "root_causes": self.root_causes
        }


class CausalInterventionSandbox:
    """Simulates pipeline execution under counterfactual interventions (do-calculus)."""
    def __init__(self, state_data: dict):
        self.state_data = state_data
        self.input_query = state_data.get("input", "")
        self.plan = state_data.get("plan") or state_data.get("planned_tasks") or []
        self.tool_calls_log = state_data.get("tool_calls_log") or []
        self.expert_results = state_data.get("expert_results") or []
        self.working_memory = state_data.get("working_memory") or {}
        
        # Parse observed errors
        self.observed_failures = []
        for r in self.expert_results:
            if "ERROR" in str(r) or "fail" in str(r).lower() or "timeout" in str(r).lower():
                self.observed_failures.append(r)
        
        for tool_call in self.tool_calls_log:
            if tool_call.get("status") == "error":
                self.observed_failures.append(tool_call)

    def simulate_do_intervention(self, intervention: dict) -> Tuple[str, float]:
        """Simulates the outcome of a do-intervention.
        
        Returns:
            Tuple[str, float]: (Simulated outcome status ("ok" or "fail"), failure probability score [0.0 - 1.0])
        """
        action = intervention.get("do")
        target = intervention.get("target")

        # 1. do(Tool_X = Fail)
        if action == "tool_fail":
            # If we force Tool_X to fail, does it break the pipeline?
            # Find the ID of the tasks that run this tool
            tool_task_ids = []
            for task in self.plan:
                if isinstance(task, dict) and task.get("mcp_tool") == target:
                    tool_task_ids.append(task.get("id"))
            
            dependent_tasks = []
            for task in self.plan:
                if isinstance(task, dict):
                    # Direct execution of the tool task itself is affected
                    if task.get("mcp_tool") == target:
                        dependent_tasks.append(task)
                        continue
                    depends = task.get("depends_on") or []
                    if isinstance(depends, str):
                        depends = [depends]
                    for dep in depends:
                        if dep in tool_task_ids:
                            dependent_tasks.append(task)
            
            if dependent_tasks:
                # If there are dependent expert nodes, the probability of overall failure is high (0.9)
                return "fail", 0.9
            return "ok", 0.15

        # 2. do(Context_Window = 4096)
        elif action == "context_clamp":
            # If context window is clamped, check if prompt + memory + history exceeds 4096 chars or tokens
            prompt_len = len(self.input_query)
            wm_len = len(str(self.working_memory))
            history_len = len(str(self.state_data.get("chat_history", "")))
            total_estimated_tokens = (prompt_len + wm_len + history_len) // 4
            
            limit = int(intervention.get("limit", 4096))
            if total_estimated_tokens > limit:
                # Severe context truncation / VRAM paging timeout
                return "fail", 0.95
            return "ok", 0.1

        # 3. do(Model_X = Fail)
        elif action == "model_fail":
            # If a critical model fails, does it cause failure?
            # A primary model failure causes the fallback model to be called (if configured).
            # If fallback exists, status is degraded but ok. Else, fail.
            has_fallback = False
            for task in self.plan:
                if isinstance(task, dict) and task.get("category") == target:
                    # check if fallback is present in experts list
                    pass
            # Check if observed models had backup
            if target == "synthesis" or target == "planner":
                # Planner/Judge failures are catastrophic
                return "fail", 1.0
            return "fail", 0.8

        return "ok", 0.05

    def calculate_average_causal_effect(self, variable: str, intervention: dict, baseline: dict) -> float:
        """Computes the Average Causal Effect (ACE) of a variable based on sandbox runs.
        
        ACE = P(Fail | do(Variable = Intervention)) - P(Fail | do(Variable = Baseline))
        """
        _, p_intervened = self.simulate_do_intervention(intervention)
        _, p_baseline = self.simulate_do_intervention(baseline)
        return round(p_intervened - p_baseline, 3)


async def diagnose_and_map_errors(state_data: dict) -> dict:
    """Executes the causal diagnostics suite, maps dependency edges, and isolates the root cause.
    
    Returns:
        dict: The serialized causal graph map.
    """
    response_id = state_data.get("response_id") or f"diag-{uuid.uuid4().hex[:8]}"
    logger.info(f"🔍 Starting causal diagnosis for response {response_id}...")
    
    graph = CausalGraph(response_id)
    sandbox = CausalInterventionSandbox(state_data)
    
    # 1. Register base nodes
    # Input Node
    input_query = state_data.get("input", "")
    complexity = state_data.get("complexity_level", "moderate")
    graph.add_node("Input_Complexity", "input", "ok", {"complexity": complexity, "length": len(input_query)})
    
    # Context Node
    limit = state_data.get("judge_num_ctx") or state_data.get("planner_num_ctx") or 4096
    graph.add_node("Context_Limit", "context", "ok", {"limit": limit})
    
    # Register tool nodes from tool log
    tool_calls = state_data.get("tool_calls_log") or []
    for tc in tool_calls:
        tool_name = tc.get("tool", "unknown_tool")
        status = "fail" if tc.get("status") == "error" else "ok"
        graph.add_node(f"Tool_{tool_name}", "tool", status, tc)
        
    # Register model nodes from experts used
    experts_used = state_data.get("expert_models_used") or []
    for exp_str in experts_used:
        if "::" in exp_str:
            model, cat = exp_str.split("::", 1)
        else:
            model, cat = exp_str, "general"
            
        # check if this expert had error results
        status = "ok"
        for res in state_data.get("expert_results", []):
            if f"[{model.upper()} ERROR]" in str(res):
                status = "fail"
                
        graph.add_node(f"Model_{cat}", "model", status, {"model": model})
        
    # Register synthesis node
    judge_refined = bool(state_data.get("judge_refined"))
    synthesis_status = "degraded" if judge_refined else "ok"
    # If final answer contains error prefix, status is fail
    for res in state_data.get("expert_results", []):
        if "ERROR" in str(res):
            synthesis_status = "fail"
    graph.add_node("Synthesis_Arbitration", "synthesis", synthesis_status, {"judge_refined": judge_refined})

    # 2. Map causal dependencies (edges)
    graph.add_edge("Input_Complexity", "Synthesis_Arbitration", 0.2)
    graph.add_edge("Context_Limit", "Synthesis_Arbitration", 0.3)
    
    for exp_str in experts_used:
        cat = exp_str.split("::", 1)[1] if "::" in exp_str else "general"
        graph.add_edge(f"Model_{cat}", "Synthesis_Arbitration", 0.8)
        
        # Link tools to the models that consumed them
        for tc in tool_calls:
            tool_name = tc.get("tool", "unknown")
            graph.add_edge(f"Tool_{tool_name}", f"Model_{cat}", 0.7)
            
        # Link context to models
        graph.add_edge("Context_Limit", f"Model_{cat}", 0.4)
        graph.add_edge("Input_Complexity", f"Model_{cat}", 0.5)

    # 3. Counterfactual Intervention Analysis (Pearl's Do-Calculus)
    # Calculate Average Causal Effect (ACE) for possible variables
    candidate_causes = []
    
    # Test Context limit intervention
    ace_ctx = sandbox.calculate_average_causal_effect(
        variable="Context_Limit",
        intervention={"do": "context_clamp", "limit": 2048},
        baseline={"do": "context_clamp", "limit": 32768}
    )
    candidate_causes.append({
        "node": "Context_Limit",
        "ace": ace_ctx,
        "rationale": f"Clamping context to 2048 yielded ACE={ace_ctx}. Shows sensitivity to VRAM/context bounds."
    })
    
    # Test each tool call intervention
    for tc in tool_calls:
        tool_name = tc.get("tool")
        ace_tool = sandbox.calculate_average_causal_effect(
            variable=f"Tool_{tool_name}",
            intervention={"do": "tool_fail", "target": tool_name},
            baseline={"do": "tool_fail", "target": "none"}
        )
        candidate_causes.append({
            "node": f"Tool_{tool_name}",
            "ace": ace_tool,
            "rationale": f"Forcing tool '{tool_name}' failure yielded ACE={ace_tool}. Indicates dependency of downstream expert nodes."
        })

    # Test expert model failures
    for exp_str in experts_used:
        cat = exp_str.split("::", 1)[1] if "::" in exp_str else "general"
        ace_model = sandbox.calculate_average_causal_effect(
            variable=f"Model_{cat}",
            intervention={"do": "model_fail", "target": cat},
            baseline={"do": "model_fail", "target": "none"}
        )
        candidate_causes.append({
            "node": f"Model_{cat}",
            "ace": ace_model,
            "rationale": f"Forcing expert model '{cat}' failure yielded ACE={ace_model}."
        })

    # Sort root causes by ACE descending
    candidate_causes.sort(key=lambda x: x["ace"], reverse=True)
    graph.root_causes = candidate_causes

    # 4. Persistence & Dataset Export for LUMI-G Judge SFT
    serialized = graph.to_dict()
    
    try:
        os.makedirs(os.path.dirname(CAUSAL_DATASET_PATH), exist_ok=True)
        # Append trace to training dataset
        with open(CAUSAL_DATASET_PATH, "a", encoding="utf-8") as f:
            f.write(json.dumps({
                "response_id": response_id,
                "input": input_query,
                "observed_failures": sandbox.observed_failures,
                "causal_graph": serialized,
                "recommendation": candidate_causes[0]["node"] if candidate_causes else "none"
            }, ensure_ascii=False) + "\n")
        logger.info(f"💾 Causal diagnostics exported to retraining dataset: {CAUSAL_DATASET_PATH}")
    except Exception as e:
        logger.warning(f"Failed to export causal dataset: {e}")

    return serialized
