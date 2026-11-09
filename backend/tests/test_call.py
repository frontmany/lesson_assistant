import jwt
from fastapi.testclient import TestClient

from app.call import assistant_token, room_name
from app.config import settings
from app.main import app

client = TestClient(app)
LESSON = "10a-history-05"


def _claims(token: str) -> dict:
    return jwt.decode(token, settings.livekit_api_secret, algorithms=["HS256"], options={"verify_aud": False})


def test_student_joins_own_room_under_own_id():
    info = client.get(f"/api/lessons/{LESSON}/join", params={"id": "artem_k"}).json()
    assert info["role"] == "student"
    assert info["url"] == settings.livekit_public_url

    claims = _claims(info["token"])
    assert claims["sub"] == "artem_k"
    assert claims["video"]["room"] == room_name(LESSON)
    assert claims["video"]["canPublish"] is True
    assert sorted(claims["video"]["canPublishSources"]) == ["camera", "microphone"]


def test_teacher_joins():
    info = client.get(f"/api/lessons/{LESSON}/join", params={"id": "teacher"}).json()
    assert info["role"] == "teacher"
    assert "screen_share" in _claims(info["token"])["video"]["canPublishSources"]


def test_stranger_cannot_join():
    assert client.get(f"/api/lessons/{LESSON}/join", params={"id": "stranger"}).status_code == 403


def test_assistant_is_hidden_listener():
    video = _claims(assistant_token(LESSON))["video"]
    assert video["hidden"] is True
    assert video["canPublish"] is False
    assert video["canSubscribe"] is True
