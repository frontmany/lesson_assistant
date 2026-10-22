"""Виртуальный класс: записанный урок заходит в звонок ботами. Владелец: Ярослав.

Инструмент разработки и замеров, а не режим продукта. Каждая дорожка
заходит в комнату LiveKit отдельным участником и «говорит» свою запись.
Для ассистента это неотличимо от живых людей, поэтому так проверяется
весь путь: звонок → скрытый участник → конвейер. Живые люди могут
зайти в тот же урок и слышать виртуальных учеников.

Идея взята из медиабота Callifornia: синтетические участники звонка.

Одна дорожка = один участник. Имя файла = id из roster.json
(teacher.wav, ivan_i.wav, ...). Формат: WAV, 16 кГц, моно.
Все дорожки одинаковой длины и начинаются в одну секунду.

Запуск: python -m app.cli virtual-class data/demo_lesson
"""

from pathlib import Path

from app.models import Roster


async def play(lesson_id: str, roster: Roster, tracks: list[Path]) -> None:
    """Завести в комнату по участнику на дорожку и проиграть запись в реальном времени.

    TODO(Ярослав): для каждой дорожки — rtc.Room().connect(..., app.call.bot_token(...)),
    rtc.AudioSource(16000, 1), rtc.LocalAudioTrack.create_audio_track, publish_track,
    затем source.capture_frame кадрами по 30 мс.
    """
    raise NotImplementedError("sources/virtual_class.py: play")
