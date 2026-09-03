"""tests/test_extract_planner_grounding_pairs.py — Unit tests for
scripts/extract_planner_grounding_pairs.py: deterministic, topically-grounded
plan generation for the planner-task-fabrication SFT dataset (LUMI-G
post-training Candidate 2).
"""

from __future__ import annotations

import json

import pytest

from scripts.extract_planner_grounding_pairs import (
    _ADVERSARIAL_EXAMPLES,
    _CATEGORY_MAP,
    _PERSISTENCE_ADVERSARIAL_EXAMPLES,
    _normalize_prompt,
    build_plan_for_adversarial_example,
    build_plan_for_benchmark_task,
    build_plan_for_persistence_task,
    curate,
    curate_json_structure,
    grounded_task_description,
    load_benchmark_tasks,
    render_messages,
)


class TestGroundedTaskDescription:
    def test_derives_task_text_from_prompt_words_only(self):
        desc = grounded_task_description("Calculate the exact mathematical figures for X.")
        assert "Calculate the exact mathematical figures for X." in desc

    def test_truncates_long_prompts(self):
        desc = grounded_task_description("x" * 500, max_chars=50)
        assert len(desc) < 200
        assert desc.endswith("...")

    def test_uses_only_first_line(self):
        desc = grounded_task_description("First line.\nSecond line with unrelated content.")
        assert "Second line" not in desc


class TestBuildPlanForBenchmarkTask:
    def test_maps_category_and_grounds_task_in_prompt(self):
        task = {"id": "t1", "category": "precision", "prompt": "Compute 840 kW baseline load."}
        plan = build_plan_for_benchmark_task(task)
        assert len(plan) == 1
        assert plan[0]["category"] == "precision_tools"
        assert "840 kW baseline load" in plan[0]["task"]

    def test_every_declared_category_is_mapped(self):
        # Guards against a future benchmark category silently falling back
        # to "general" instead of raising in load_benchmark_tasks.
        for category in ["systems_programming", "compounding_knowledge", "precision", "reasoning", "governance"]:
            assert category in _CATEGORY_MAP


class TestBuildPlanForAdversarialExample:
    def test_ping_maps_to_general_not_fabricated_topic(self):
        example = next(e for e in _ADVERSARIAL_EXAMPLES if e["prompt"] == "ping")
        plan = build_plan_for_adversarial_example(example)
        assert plan[0]["category"] == "general"
        assert "DNS" not in plan[0]["task"] and "HTTP" not in plan[0]["task"]

    def test_arithmetic_example_uses_precision_tools_with_mcp_args(self):
        example = next(e for e in _ADVERSARIAL_EXAMPLES if e["prompt"] == "What is 2+2?")
        plan = build_plan_for_adversarial_example(example)
        assert plan[0]["category"] == "precision_tools"
        assert plan[0]["mcp_tool"] == "calculate"
        assert plan[0]["mcp_args"] == {"expression": "2+2"}


class TestRenderMessages:
    def test_produces_system_user_assistant_turns(self):
        rendered = render_messages("hi", [{"task": "Greet", "category": "general"}])
        roles = [m["role"] for m in rendered["messages"]]
        assert roles == ["system", "user", "assistant"]
        assert rendered["messages"][1]["content"] == "hi"
        assert json.loads(rendered["messages"][2]["content"]) == [{"task": "Greet", "category": "general"}]


class TestNormalizePrompt:
    def test_leaves_single_turn_task_untouched(self):
        task = {"id": "t1", "prompt": "already here"}
        assert _normalize_prompt(task)["prompt"] == "already here"

    def test_concatenates_multi_turn_prompts(self):
        task = {"id": "t2", "turns": [{"prompt": "first"}, {"prompt": "second"}]}
        normalized = _normalize_prompt(task)
        assert normalized["prompt"] == "first\nsecond"

    def test_does_not_mutate_input(self):
        task = {"id": "t2", "turns": [{"prompt": "first"}]}
        _normalize_prompt(task)
        assert "prompt" not in task


class TestLoadBenchmarkTasks:
    def test_reads_real_benchmark_dataset(self):
        from pathlib import Path
        tasks = load_benchmark_tasks(Path("benchmarks/datasets/sovereign_scientific_benchmark_v1.json"))
        assert len(tasks) == 8
        assert all("prompt" in t and "category" in t and t["prompt"] for t in tasks)

    def test_raises_on_unmapped_category(self, tmp_path):
        bad_dataset = tmp_path / "bad.json"
        bad_dataset.write_text(json.dumps({"test_cases": [{"id": "x", "category": "unknown_cat", "prompt": "p"}]}))
        with pytest.raises(ValueError, match="unmapped category"):
            load_benchmark_tasks(bad_dataset)


class TestCurate:
    def test_produces_one_example_per_task_plus_adversarial_examples(self):
        benchmark_tasks = [{"id": "t1", "category": "precision", "prompt": "Compute X."}]
        examples = curate(benchmark_tasks)
        assert len(examples) == 1 + len(_ADVERSARIAL_EXAMPLES)


class TestBuildPlanForPersistenceTask:
    def test_produces_single_flat_acknowledge_task_no_nested_json(self):
        plan = build_plan_for_persistence_task("Alice owns X. Bob owns Y.", "compounding_knowledge")
        assert len(plan) == 1
        assert plan[0]["category"] == "compounding_knowledge"
        assert plan[0]["task"].startswith("Acknowledge the following information and confirm it is noted:")
        assert "Alice owns X. Bob owns Y." in plan[0]["task"]
        # The whole point of Candidate 4's fix: a single string value, never
        # a task field that itself parses as JSON.
        assert not plan[0]["task"].strip().startswith("{")
        assert not plan[0]["task"].strip().startswith("[")


class TestCurateJsonStructure:
    def test_only_includes_multi_turn_tasks_from_the_benchmark(self):
        benchmark_tasks = [
            {"id": "single", "type": "single_turn", "category": "precision", "prompt": "Compute X."},
            {"id": "multi", "type": "multi_turn", "category": "governance", "prompt": "Rule A. Rule B."},
        ]
        examples = curate_json_structure(benchmark_tasks)
        # 1 multi_turn benchmark task + the hand-written persistence adversarial examples.
        assert len(examples) == 1 + len(_PERSISTENCE_ADVERSARIAL_EXAMPLES)
        prompts = [e["messages"][1]["content"] for e in examples]
        assert "Rule A. Rule B." in prompts
        assert "Compute X." not in prompts

    def test_real_multi_turn_benchmark_tasks_are_covered(self):
        from pathlib import Path
        tasks = load_benchmark_tasks(Path("benchmarks/datasets/sovereign_scientific_benchmark_v1.json"))
        multi_turn_ids = {t["id"] for t in tasks if t.get("type") == "multi_turn"}
        assert multi_turn_ids == {"sci-graphrag-01-topology-cascade", "sci-graphrag-02-paraconsistent-reconciliation"}
        examples = curate_json_structure(tasks)
        assert len(examples) == len(multi_turn_ids) + len(_PERSISTENCE_ADVERSARIAL_EXAMPLES)
