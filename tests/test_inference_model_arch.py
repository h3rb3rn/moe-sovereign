from services.inference import _kv_cache_gb_from_arch


def test_kv_cache_arch_ignores_null_matching_values():
    arch = {
        "qwen.block_count": 64,
        "qwen.attention.head_count_kv": None,
        "qwen2.attention.head_count_kv": 8,
        "qwen.attention.key_length": 128,
        "qwen.attention.value_length": 128,
    }

    assert _kv_cache_gb_from_arch(arch, 262144) > 0


def test_kv_cache_arch_returns_zero_for_invalid_required_values():
    arch = {
        "qwen.block_count": "unknown",
        "qwen.attention.head_count_kv": None,
        "qwen.attention.key_length": 128,
    }

    assert _kv_cache_gb_from_arch(arch, 262144) == 0.0
