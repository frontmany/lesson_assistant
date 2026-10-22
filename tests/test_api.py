from fastapi.testclient import TestClient

from app.main import app

client = TestClient(app)


def test_health():
    assert client.get("/api/health").json()["status"] == "ok"


def test_webapp_is_served():
    assert "Я учитель" in client.get("/").text


def test_summary():
    assert client.get("/api/lessons/10a-history-05/summary").json()["ask_next"]


def test_ws_streams_mock_events():
    with client.websocket_connect("/ws/lessons/10a-history-05") as ws:
        first = ws.receive_json()
    assert first["type"] == "scale"
    assert "after" not in first
