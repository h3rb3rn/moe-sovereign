from scripts.validate_task38_e2e import EXPECTED_FIELDS, _extract_json, _score_payload


def test_task38_payload_contract_and_ground_truth_pass():
    payload = {
        "gcd": 23,
        "gcd_proof": "391 = 17 × 23; 299 = 13 × 23",
        "speed_m_s": 20,
        "weekday_de": "Mittwoch",
        "sql_injection": True,
        "safe_execute": 'cursor.execute("SELECT * FROM students WHERE name = ?", (user_input,))',
    }

    assert set(payload) == EXPECTED_FIELDS
    assert all(_score_payload(payload).values())


def test_task38_payload_contract_rejects_extra_fields_and_wrong_facts():
    payload = {
        "gcd": 29,
        "gcd_proof": "unverified",
        "speed_m_s": 20,
        "weekday_de": "Freitag",
        "sql_injection": True,
        "safe_execute": 'cursor.execute("SELECT " + user_input)',
        "extra": "not allowed",
    }

    checks = _score_payload(payload)

    assert checks["exact_fields"] is False
    assert checks["gcd"] is False
    assert checks["gcd_proof"] is False
    assert checks["weekday_de"] is False
    assert checks["safe_execute"] is False


def test_task38_json_extractor_accepts_plain_json_and_code_fence():
    assert _extract_json('{"gcd": 23}') == {"gcd": 23}
    assert _extract_json('```json\n{"gcd": 23}\n```') == {"gcd": 23}
