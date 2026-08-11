import pytest
from services.sovereignty import calculate_shannon_entropy, assert_egress_entropy_safe, EgressDenied

def test_empty_text():
    assert calculate_shannon_entropy("") == 0.0

def test_single_character():
    assert calculate_shannon_entropy("aaaa") == 0.0

def test_two_characters():
    assert calculate_shannon_entropy("ab") == 1.0

def test_normal_german_text():
    text = "Dies ist ein ganz normaler deutscher Text, der eine gewisse Entropie aufweist."
    entropy = calculate_shannon_entropy(text)
    assert 3.5 <= entropy <= 4.5

def test_high_entropy_text():
    # 95 unique characters
    text = "".join(chr(i) for i in range(32, 127))
    entropy = calculate_shannon_entropy(text)
    assert entropy > 5.0

def test_assert_egress_entropy_safe_normal():
    assert assert_egress_entropy_safe("Normaler Text", max_entropy=5.6) is True

def test_assert_egress_entropy_safe_high():
    text = "".join(chr(i) for i in range(32, 127))
    with pytest.raises(EgressDenied):
        assert_egress_entropy_safe(text, max_entropy=5.0)
