from routes.models import _template_context_length, _template_model_entry


def test_template_model_metadata_exposes_context_window():
    template = {
        "name": "moe-n04-rtx-qwen3.6:35b-256k",
        "planner_num_ctx": 131072,
        "experts": {
            "coding": {"context_window": 262144, "models": []},
            "fast": {"context_window": 32768, "models": []},
        },
    }

    assert _template_context_length(template) == 262144
    entry = _template_model_entry("owned-id", template, 123)
    assert entry["id"] == template["name"]
    assert entry["context_length"] == 262144
    assert entry["context_window"] == 262144
