import json

from fastapi.testclient import TestClient

from app.config.settings import clear_settings_cache
from app.main import create_app
from app.services.seed import seed_demo_data


def test_case_api_stream_restart_confirm_and_dismiss(tmp_path, monkeypatch):
    knowledge = tmp_path / "knowledge"
    knowledge.mkdir()
    (knowledge / "cancel.md").write_text("# 取消政策\n未发货订单可申请取消。", encoding="utf-8")
    monkeypatch.setenv("DATABASE_URL", f"sqlite+aiosqlite:///{tmp_path / 'api.db'}")
    monkeypatch.setenv("KNOWLEDGE_DIR", str(knowledge))
    clear_settings_cache()
    try:
        with TestClient(create_app()) as client:

            async def seed():
                async with client.app.state.container.session_factory() as db:
                    await seed_demo_data(db)
                    await db.commit()

            client.portal.call(seed)
            response = client.post(
                "/api/v1/chat/stream", json={"message": "取消订单 ORD10003", "user_id": "u_demo"}
            )
            assert response.status_code == 200
            events = [
                json.loads(line[5:].strip())
                for line in response.text.splitlines()
                if line.startswith("data:")
            ]
            reply = next(item["data"] for item in events if item["event"] == "answer")
            case_id = reply["case_id"]
            path = f"/api/v1/cases/{case_id}"
            assert reply["case_status"] == "pending_review"
            assert client.get(path, params={"user_id": "other"}).status_code == 400
            assert client.get("/api/v1/cases", params={"user_id": "other"}).json() == []
            assert (
                client.get("/api/v1/cases", params={"user_id": "u_demo", "status": "failed"}).json()
                == []
            )
        with TestClient(create_app()) as restarted:
            detail = restarted.get(path, params={"user_id": "u_demo"}).json()
            assert detail["action"]["action_id"] == reply["pending_action"]["action_id"]
            assert len(detail["messages"]) == 2
            result = restarted.post(path + "/confirm", json={"user_id": "u_demo"})
            assert result.status_code == 200 and result.json()["case_status"] == "completed"
            repeat = restarted.post(path + "/confirm", json={"user_id": "u_demo"})
            assert repeat.json()["answer"] == result.json()["answer"]
            detail = restarted.get(path, params={"user_id": "u_demo"}).json()
            assert all(detail["verification"].values())
            assert detail["attempts"] == 1
            assert (
                restarted.post(path + "/dismiss", json={"user_id": "u_demo"}).json()["status"]
                == "completed"
            )
            proposal = restarted.post(
                "/api/v1/chat", json={"message": "我要退货 ORD10001", "user_id": "u_demo"}
            ).json()
            cancel_path = f"/api/v1/cases/{proposal['case_id']}"
            assert (
                restarted.post(cancel_path + "/dismiss", json={"user_id": "u_demo"}).json()[
                    "status"
                ]
                == "cancelled"
            )
            assert (
                restarted.post(cancel_path + "/confirm", json={"user_id": "u_demo"}).json()[
                    "case_status"
                ]
                == "cancelled"
            )
            assert len(restarted.get("/api/v1/cases", params={"user_id": "u_demo"}).json()) == 2
    finally:
        clear_settings_cache()
