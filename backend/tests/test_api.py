from fastapi.testclient import TestClient

from app.main import app

client = TestClient(app)


def test_health():
    assert client.get("/api/health").json()["status"] == "ok"


def test_health_tells_whether_dev_links_work(monkeypatch):
    import dataclasses

    from app import main

    for dev in (True, False):
        monkeypatch.setattr(main, "settings", dataclasses.replace(main.settings, dev_auth=dev))
        assert client.get("/api/health").json()["dev_auth"] is dev


def test_webapp_is_served():
    assert "Сферум AI" in client.get("/").text


def test_summary():
    assert client.get("/api/lessons/10a-history-05/summary").json()["ask_next"]


def test_ws_streams_mock_events():
    with client.websocket_connect("/ws/lessons/10a-history-05") as ws:
        first = ws.receive_json()
    assert first["type"] == "scale"
    assert "after" not in first
    # экран отличает мок от урока: при старте сбрасывает его, ученику не показывает (#78)
    assert first["mock"] is True


def test_webapp_files_are_revalidated():
    # иначе MAX берёт из кэша старый скрипт к новой странице
    assert client.get("/js/start.js").headers["cache-control"] == "no-cache"
