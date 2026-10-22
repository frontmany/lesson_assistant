# Контракты

Форматы сообщений между сервером, мини-приложением и ботом. В коде они описаны в `app/models.py`, примеры лежат в `webapp/mock/`. Меняем всё три места в одном PR, а `tests/test_contracts.py` следит, чтобы они совпадали.

## WebSocket `/ws/lessons/{lesson_id}`

Сервер шлёт события урока. Клиент различает их по полю `type`.

### `utterance` — реплика ученика

Видит только учитель.

```json
{"type": "utterance", "student_id": "artem_k", "t": 470.3,
 "text": "Ну, там флот строили", "kind": "response", "addressed": true}
```

| Поле | Значение |
|---|---|
| `t` | секунда от начала урока |
| `kind` | `answer` — ответ по теме, `initiative` — сам спросил или вызвался, `response` — ответил на обращение, `other` |
| `addressed` | ответ на обращение учителя |

### `scale` — ступень шкалы

Учитель получает события всех учеников, ученик — только свои.

```json
{"type": "scale", "student_id": "alisa_n", "level": 2,
 "label": "Есть ответ", "flags": ["quiet_but_precise"]}
```

| Поле | Значение |
|---|---|
| `level` | 1–4, только растёт в течение урока |
| `label` | «Пока тихо», «Есть ответ», «Участвуешь», «Активно на уроке» |
| `flags` | метки для учителя: `long_silence` — «давно не отвечал», `quiet_but_precise` — «тихий, но точный». Ученику не показываются |

### `analytics` — выключатель

```json
{"type": "analytics", "enabled": false, "student_id": "sofya_l"}
```

`student_id: null` — для всего класса.

## REST

| Метод | Путь | Ответ |
|---|---|---|
| `GET` | `/api/health` | состояние сервера |
| `GET` | `/api/lessons/{id}/roster` | состав класса (`Roster`) |
| `GET` | `/api/lessons/{id}/join?participant_id=...` | вход в звонок (`JoinInfo`) |
| `POST` | `/api/lessons/{id}/start` | ассистент заходит в звонок и начинает слушать |
| `GET` | `/api/lessons/{id}/summary` | сводка учителю (`Summary`) |
| `GET` | `/api/lessons/{id}/cards/{student_id}` | карточка ученика (`StudentCard`) |
| `POST` | `/api/lessons/{id}/analytics` | включить или выключить аналитику (`AnalyticsState`) |

### `JoinInfo` — вход в звонок

```json
{"url": "ws://localhost:7880", "token": "eyJhbGciOi...",
 "participant_id": "artem_k", "name": "Артём К.", "role": "student"}
```

Токен LiveKit пускает в комнату `lesson-{id}`, identity в нём = `participant_id`. Участника нет в составе класса — ответ 404.

### `summary` — сводка учителю

```json
{"type": "summary", "lesson_id": "10a-history-05",
 "carried": ["ivan_i", "maria_s"], "dropped": ["dmitry_p"],
 "weak_topics": ["Табель о рангах"],
 "ask_next": [{"student_id": "artem_k", "topic": "Реформы Петра I",
               "question": "Зачем Петру понадобился Табель о рангах?"}]}
```

Пустые списки не показываем: если рекомендовать нечего, блока в сводке нет.

### `card` — карточка ученика

```json
{"type": "card", "lesson_id": "10a-history-05", "student_id": "artem_k",
 "level": 2, "went_well": ["Ответил про строительство флота"],
 "to_repeat": ["Табель о рангах"]}
```

Только своё, без оценок, рейтингов и сравнения с другими.
