import mimetypes

from app.main import create_app


def test_frontend_mime_types_override_windows_file_associations(monkeypatch):
    mimetypes.init()
    monkeypatch.setitem(mimetypes.types_map, ".js", "text/plain")
    monkeypatch.setitem(mimetypes.types_map, ".css", "application/octet-stream")
    create_app()
    assert mimetypes.guess_type("index.js")[0] == "text/javascript"
    assert mimetypes.guess_type("index.css")[0] == "text/css"
