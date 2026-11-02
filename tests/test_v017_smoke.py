from pathlib import Path
from unittest.mock import patch
from app import integration_smoke as smoke


def test_request_builds_json_and_reports_http_error():
    from urllib.error import HTTPError
    from io import BytesIO
    with patch.object(smoke, "urlopen", side_effect=HTTPError("url", 403, "Forbidden", {}, BytesIO(b"denied"))):
        assert smoke.request("/test", method="POST", payload={"x": 1}) == (403, b"denied")


def test_smoke_is_read_only():
    source = Path(smoke.__file__).read_text()
    assert "INSERT INTO" not in source and "DELETE FROM" not in source
    assert "UPDATE " not in source


def test_expected_tables_are_nonempty():
    assert len(smoke.EXPECTED) >= 5
