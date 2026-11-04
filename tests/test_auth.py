"""Вход по данным запуска MAX и права в уроке (#24)."""

import hashlib
import hmac
import json
import time
from urllib.parse import urlencode

import pytest
from fastapi.testclient import TestClient

from app import auth
from app.db import classes as db
from app.main import app
from app.models import ScaleUpdate, Utterance
from app.ws import lesson as ws_lesson

TOKEN = "bot-token"
TEACHER, IVAN, MARIA, STRANGER = 100, 111, 222, 999
client = TestClient(app)


def init_data(user_id, first="Иван", last="Иванов", auth_date=None, start_param=None, token=TOKEN):
    """Данные запуска, подписанные так, как их подписывает MAX."""
    fields = {
        "auth_date": str(auth_date or int(time.time())),
        "query_id": "q1",
        "user": json.dumps({"id": user_id, "first_name": first, "last_name": last}, ensure_ascii=False),
    }
    if start_param:
        fields["start_param"] = start_param
    check = "\n".join(f"{k}={v}" for k, v in sorted(fields.items()))
    secret = hmac.new(b"WebAppData", token.encode(), hashlib.sha256).digest()
    fields["hash"] = hmac.new(secret, check.encode(), hashlib.sha256).hexdigest()
    return urlencode(fields)


@pytest.fixture(autouse=True)
def production(use_settings):
    use_settings(dev_auth=False, max_bot_token=TOKEN, session_secret="secret")


@pytest.fixture
def lesson():
    c = db.create_class(TEACHER, "Елена Викторовна", "10А", "история")
    db.join(c.id, IVAN, "Иван Иванов")
    db.join(c.id, MARIA, "Мария Смирнова")
    return c, db.lesson_for_class(c.id).id


def sign_in(user_id, **kw):
    response = client.post("/api/auth/max", json={"init_data": init_data(user_id, **kw)})
    assert response.status_code == 200, response.text
    return {"Authorization": f"Bearer {response.json()['token']}"}


def test_valid_signature_is_accepted():
    data = auth.check_init_data(init_data(IVAN, start_param="l1"), TOKEN)
    assert data["user"]["id"] == IVAN
    assert data["start_param"] == "l1"


def test_forged_or_old_data_is_rejected():
    assert auth.check_init_data(init_data(IVAN, token="other"), TOKEN) is None
    tampered = init_data(IVAN).replace("%D0%98%D0%B2%D0%B0%D0%BD", "%D0%9F%D1%91%D1%82%D1%80")
    assert auth.check_init_data(tampered, TOKEN) is None
    assert auth.check_init_data(init_data(IVAN, auth_date=int(time.time()) - 2 * 86400), TOKEN) is None
    assert client.post("/api/auth/max", json={"init_data": init_data(IVAN, token="other")}).status_code == 401


def test_nothing_without_session(lesson):
    _, lesson_id = lesson
    assert client.get(f"/api/lessons/{lesson_id}/join").status_code == 401
    assert client.get(f"/api/lessons/{lesson_id}/join", params={"id": "m2"}).status_code == 401


def test_student_joins_only_as_self(lesson):
    _, lesson_id = lesson
    ivan = db.students_of(lesson[0].id)[0]
    headers = sign_in(IVAN)
    me = client.get(f"/api/lessons/{lesson_id}/me", headers=headers).json()
    assert me == {"participant_id": ivan.participant_id, "name": "Иван И.", "role": "student", "analytics": True}
    # чужой id в адресе ничего не меняет: участника сервер берёт из сессии
    join = client.get(f"/api/lessons/{lesson_id}/join", params={"id": "m999"}, headers=headers).json()
    assert join["participant_id"] == ivan.participant_id


def test_stranger_is_not_let_in(lesson):
    _, lesson_id = lesson
    assert client.get(f"/api/lessons/{lesson_id}/join", headers=sign_in(STRANGER)).status_code == 403


def test_only_teacher_manages_lesson(lesson):
    c, lesson_id = lesson
    state = {"type": "analytics", "enabled": False, "student_id": "m2"}
    assert client.post(f"/api/lessons/{lesson_id}/analytics", json=state, headers=sign_in(IVAN)).status_code == 403
    assert client.post(f"/api/lessons/{lesson_id}/analytics", json=state, headers=sign_in(TEACHER)).status_code == 200
    assert client.post(f"/api/classes/{c.id}/lessons", headers=sign_in(IVAN)).status_code == 403
    assert client.post(f"/api/classes/{c.id}/lessons", headers=sign_in(TEACHER)).json()["lesson_id"] == lesson_id


def test_browser_ticket_works_once(lesson):
    _, lesson_id = lesson
    ticket = client.post("/api/auth/ticket", headers=sign_in(IVAN)).json()["ticket"]
    token = client.post("/api/auth/browser", json={"ticket": ticket}).json()["token"]
    me = client.get(f"/api/lessons/{lesson_id}/me", headers={"Authorization": f"Bearer {token}"}).json()
    assert me["role"] == "student"
    assert client.post("/api/auth/browser", json={"ticket": ticket}).status_code == 401
    # сессия не годится как одноразовая ссылка, и наоборот
    assert client.post("/api/auth/browser", json={"ticket": token}).status_code == 401


def test_student_socket_gets_only_own_scale(lesson):
    c, lesson_id = lesson
    ivan, maria = (m.participant_id for m in db.students_of(c.id))
    ws_lesson.set_live(lesson_id, True)
    try:
        ws_lesson.publish(lesson_id, ScaleUpdate(student_id=maria, level=2, label="x"))
        ws_lesson.publish(lesson_id, Utterance(student_id=ivan, t=1, text="реплика", kind="answer", addressed=False))
        ws_lesson.publish(lesson_id, ScaleUpdate(student_id=ivan, level=3, label="x"))
        token = sign_in(IVAN)["Authorization"].split()[1]
        with client.websocket_connect(f"/ws/lessons/{lesson_id}?token={token}") as ws:
            assert ws.receive_json() == {"type": "scale", "student_id": ivan, "level": 3, "label": "x", "flags": []}
            ws.portal.call(ws_lesson.publish, lesson_id, ScaleUpdate(student_id=maria, level=3, label="x"))
            ws.portal.call(ws_lesson.publish, lesson_id, ScaleUpdate(student_id=ivan, level=4, label="x"))
            assert ws.receive_json()["level"] == 4  # шкала Марии до Ивана не дошла
        teacher = sign_in(TEACHER)["Authorization"].split()[1]
        with client.websocket_connect(f"/ws/lessons/{lesson_id}?token={teacher}") as ws:
            got = {(e["type"], e["student_id"]) for e in (ws.receive_json() for _ in range(3))}
            assert ("utterance", ivan) in got and ("scale", maria) in got
    finally:
        ws_lesson.set_live(lesson_id, False)


def test_socket_without_session_is_closed(lesson):
    _, lesson_id = lesson
    with pytest.raises(Exception):
        with client.websocket_connect(f"/ws/lessons/{lesson_id}") as ws:
            ws.receive_json()


def test_my_classes_for_start_page(lesson):
    c, lesson_id = lesson
    teacher = client.get("/api/me/classes", headers=sign_in(TEACHER)).json()
    assert teacher == [{"id": c.id, "name": "10А", "subject": "история", "role": "teacher", "students": 2, "lesson": None}]
    db.mark_live(lesson_id)
    student = client.get("/api/me/classes", headers=sign_in(IVAN)).json()
    assert student[0]["role"] == "student" and student[0]["lesson"] == lesson_id
    assert client.get("/api/me/classes", headers=sign_in(STRANGER)).json() == []


def test_demo_from_start_page(lesson):
    headers = sign_in(MARIA, first="Мария", last="Смирнова")
    lesson_id = client.post("/api/demo", headers=headers).json()["lesson_id"]
    assert client.get(f"/api/lessons/{lesson_id}/me", headers=headers).json() == {
        "participant_id": "teacher", "name": "Мария Смирнова", "role": "teacher", "analytics": True}
    assert client.post("/api/demo").status_code == 401


def test_demo_lesson_teacher_is_who_asked(lesson):
    demo = db.demo_lesson(MARIA, "Мария Смирнова")
    me = client.get(f"/api/lessons/{demo.id}/me", headers=sign_in(MARIA)).json()
    assert me["role"] == "teacher"
    assert client.get(f"/api/lessons/{demo.id}/me", headers=sign_in(IVAN)).status_code == 403


def test_analytics_switch_is_kept_and_sent_to_student(lesson):
    c, lesson_id = lesson
    ivan = db.students_of(c.id)[0].participant_id
    teacher = sign_in(TEACHER)
    state = {"type": "analytics", "enabled": False, "student_id": ivan}
    assert client.post(f"/api/lessons/{lesson_id}/analytics", json=state, headers=teacher).status_code == 200
    assert client.get(f"/api/lessons/{lesson_id}/analytics", headers=teacher).json() == {"all_off": False, "off": [ivan]}
    me = client.get(f"/api/lessons/{lesson_id}/me", headers=sign_in(IVAN)).json()
    assert me["analytics"] is False
    assert client.get(f"/api/lessons/{lesson_id}/analytics", headers=sign_in(IVAN)).status_code == 403
    client.post(f"/api/lessons/{lesson_id}/analytics", json={**state, "enabled": True}, headers=teacher)
    assert client.get(f"/api/lessons/{lesson_id}/me", headers=sign_in(IVAN)).json()["analytics"] is True
