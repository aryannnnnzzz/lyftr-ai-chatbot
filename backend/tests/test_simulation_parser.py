from app.llm import parse_simulation_header, simulated_status


def test_parse_pairs():
    assert parse_simulation_header("key_1=429,key_2=500") == {"key_1": 429, "key_2": 500}


def test_parse_wildcard_and_whitespace():
    plan = parse_simulation_header(" *=503 ")
    assert simulated_status(plan, "key_7") == 503


def test_specific_key_beats_wildcard():
    plan = parse_simulation_header("*=429,key_2=500")
    assert simulated_status(plan, "key_2") == 500
    assert simulated_status(plan, "key_1") == 429


def test_invalid_entries_ignored():
    assert parse_simulation_header("key_1=404,garbage,key_2=abc,=500") == {}
    assert parse_simulation_header(None) == {}
