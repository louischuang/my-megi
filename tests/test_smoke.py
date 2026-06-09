from mymegi.main import app


def test_openapi_schema_is_available() -> None:
    schema = app.openapi()
    assert schema["info"]["title"] == "My Megi API"
    assert "/health" in schema["paths"]
