"""Knowledge update API, authorization and public citation provenance."""

from fastapi.testclient import TestClient

from app.config.settings import clear_settings_cache
from app.main import create_app


def test_knowledge_reload_auth_and_chat_citations(tmp_path, monkeypatch):
    policy = tmp_path / "return_policy.md"
    policy.write_text("# 退货\n商品支持七天无理由退货。", encoding="utf-8")
    monkeypatch.setenv("KNOWLEDGE_DIR", str(tmp_path))
    monkeypatch.setenv("ADMIN_API_TOKEN", "test-admin-token")
    clear_settings_cache()
    try:
        with TestClient(create_app()) as client:
            path = "/api/v1/admin/knowledge"
            assert client.get(path).status_code == 401
            assert client.post(path + "/reload").status_code == 401
            headers = {"x-admin-token": "test-admin-token"}
            status = client.get(path, headers=headers)
            assert status.status_code == 200
            old = status.json()["version"]
            policy.write_text("# 退货\n商品支持十四天无理由退货。", encoding="utf-8")
            response = client.post(path + "/reload", headers=headers)
            assert response.status_code == 200
            new = response.json()
            assert new["previous_version"] == old and new["version"] != old
            assert new["embedded_texts"] == 1
            assert not client.post(path + "/reload", headers=headers).json()["changed"]
            reply = client.post(
                "/api/v1/chat", json={"message": "商品退货政策是什么？", "user_id": "u_knowledge"}
            )
            assert reply.status_code == 200
            assert "十四天" in reply.json()["answer"]
            assert reply.json()["citations"]
            assert all(c["knowledge_version"] == new["version"] for c in reply.json()["citations"])
            assert all(len(c["content_sha256"]) == 64 for c in reply.json()["citations"])
    finally:
        clear_settings_cache()
