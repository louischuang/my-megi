from mymegi.main import app, auto_confirm_payload, is_auto_confirmable


def test_openapi_schema_is_available() -> None:
    schema = app.openapi()
    assert schema["info"]["title"] == "My Megi API"
    assert "/health" in schema["paths"]


def test_auto_confirm_includes_ninety_percent_confidence() -> None:
    assert is_auto_confirmable({"name": "Louis Chuang", "confidence": 0.9})


def test_auto_confirm_payload_maps_draft_notes() -> None:
    payload = auto_confirm_payload({"name": "Louis Chuang", "notes": "Met at expo"})
    assert payload["note"] == "Met at expo"
