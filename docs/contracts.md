# API и события

Модели описаны в `backend/app/models.py`, примеры событий лежат в `webapp/mock`. Формат меняется сразу в трёх местах, это проверяет тест `backend/tests/test_contracts.py`.

## Вход

Приложение отправляет данные запуска MAX в `POST /api/auth/max` и получает сессию. Сервер проверяет подпись токеном бота и возраст данных, не старше суток. Дальше сессия идёт в заголовке `Authorization` и в адресе WebSocket.

Если в MAX нет микрофона, приложение берёт одноразовую ссылку в `POST /api/auth/ticket` и открывает урок в браузере. Браузер меняет её на сессию в `POST /api/auth/browser`. Ссылка живёт десять минут и работает один раз.

## События урока

WebSocket `/ws/lessons/{lesson_id}` присылает события урока. Учитель получает все события, ученик только свою шкалу и выключатель аналитики. При подключении к идущему уроку сервер сразу присылает последнее состояние каждого ученика. До начала урока сервер проигрывает пример событий с пометкой `mock`.

Реплика ученика видна только учителю.

```json
{"type": "utterance", "student_id": "artem_k", "t": 470.3,
 "text": "Ну, там флот строили", "kind": "response", "addressed": true}
```

Тип реплики бывает `answer`, `initiative`, `response` и `other`.

Ступень шкалы от 1 до 4 за урок только растёт. Метки `long_silence` и `quiet_but_precise` видит только учитель.

```json
{"type": "scale", "student_id": "alisa_n", "level": 2,
 "label": "Есть ответ", "flags": ["quiet_but_precise"]}
```

Выключатель аналитики приходит для одного ученика или для всего класса, тогда `student_id` пустой.

```json
{"type": "analytics", "enabled": false, "student_id": "sofya_l"}
```

## Урок

`GET /api/lessons/{id}/me` отвечает, кто открыл урок, учитель или ученик, закончился ли урок и демо ли это.

`GET /api/lessons/{id}/roster` отдаёт состав урока.

`GET /api/lessons/{id}/join` выдаёт адрес LiveKit и токен входа в звонок.

`POST /api/lessons/{id}/start` начинает урок. Ассистент входит в звонок, ученики получают приглашение в боте. Завершённый урок заново не начинается.

`POST /api/lessons/{id}/end` завершает урок и закрывает звонок у всего класса.

`POST /api/lessons/{id}/virtual-class` заводит в звонок записанный класс, а `DELETE` на тот же адрес выводит его.

`POST /api/lessons/{id}/analytics` выключает или включает аналитику ученику, а `GET` на тот же адрес отдаёт, у кого она выключена.

`GET /api/lessons/{id}/summary` отдаёт сводку учителю, `GET /api/lessons/{id}/cards/{student_id}` карточку ученика.

```json
{"type": "summary", "lesson_id": "10a-history-05",
 "carried": ["ivan_i", "maria_s"], "dropped": ["dmitry_p"],
 "weak_topics": ["Табель о рангах"],
 "ask_next": [{"student_id": "artem_k", "topic": "Реформы Петра I",
               "question": "Зачем Петру понадобился Табель о рангах?"}]}
```

## Классы

`GET /api/me/classes` отдаёт классы вошедшего, `GET /api/me/role` и `PUT /api/me/role` его роль, общую с ботом. `POST /api/me/exit` сбрасывает роль и закрывает начатые уроки учителя.

`POST /api/classes` создаёт класс, `GET`, `PATCH` и `DELETE /api/classes/{id}` открывают, переименовывают и удаляют его. Удалённый класс тридцать дней можно вернуть через `POST /api/classes/{id}/restore`.

`DELETE /api/classes/{id}/students/{member_id}` убирает ученика из класса, а `POST` на адрес с `restore` возвращает его. Ученики получают об этом сообщение в боте.

`POST /api/classes/join` впускает ученика в класс по коду. `POST /api/classes/{id}/lessons` открывает урок класса. `POST /api/demo` создаёт свой демо урок с записанным классом.

`GET /api/health` отвечает о состоянии сервера.
