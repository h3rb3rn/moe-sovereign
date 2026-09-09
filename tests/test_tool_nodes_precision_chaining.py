"""Tests for the dependency-ordered precision_tools scheduler."""

from graph.tool_nodes import _topological_batches


def _task(task_id, refs=()):
    operands = [{"$task_result": ref} for ref in refs] or ["1"]
    return {
        "id": task_id,
        "mcp_tool": "decimal_finance",
        "mcp_args": {"operands": operands},
    }


def test_topological_batches_puts_independent_tasks_in_one_batch():
    tasks = [_task("a"), _task("b"), _task("c")]

    batches, unscheduled = _topological_batches(tasks)

    assert unscheduled == []
    assert len(batches) == 1
    assert {t["id"] for t in batches[0]} == {"a", "b", "c"}


def test_topological_batches_orders_a_linear_chain():
    year1 = _task("year1")
    year2 = _task("year2", refs=["year1"])
    year3 = _task("year3", refs=["year2"])

    batches, unscheduled = _topological_batches([year3, year1, year2])

    assert unscheduled == []
    assert [t["id"] for batch in batches for t in batch] == ["year1", "year2", "year3"]
    assert [len(batch) for batch in batches] == [1, 1, 1]


def test_topological_batches_groups_mixed_independent_and_dependent_tasks():
    base = _task("base")
    dependent_a = _task("dep_a", refs=["base"])
    dependent_b = _task("dep_b", refs=["base"])
    independent = _task("independent")

    batches, unscheduled = _topological_batches(
        [dependent_a, base, independent, dependent_b]
    )

    assert unscheduled == []
    assert len(batches) == 2
    assert {t["id"] for t in batches[0]} == {"base", "independent"}
    assert {t["id"] for t in batches[1]} == {"dep_a", "dep_b"}


def test_topological_batches_leaves_a_cycle_unscheduled():
    a = _task("a", refs=["b"])
    b = _task("b", refs=["a"])

    batches, unscheduled = _topological_batches([a, b])

    assert batches == []
    assert {t["id"] for t in unscheduled} == {"a", "b"}
