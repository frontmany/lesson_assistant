"""Сборка конвейера: источник → VAD → ASR → классификатор → скоринг → события.

Владелец: Ярослав.
"""

from pathlib import Path

from app.models import Roster


async def run_call(lesson_id: str) -> None:
    """Слушать звонок урока до его конца и рассылать события по WebSocket.

    TODO(Ярослав): кадры из sources.call.listen → по участнику копить речь
    (vad.speech_segments) → asr.transcribe → classify.classify → scoring,
    события Utterance и ScaleUpdate — в app/ws. Кадры участников,
    которых нет в составе класса, отбрасывать.
    """
    raise NotImplementedError("pipeline/runner.py: run_call")


def run_lesson(roster: Roster, tracks: list[Path]) -> None:
    """Прогнать записанный урок без звонка и напечатать транскрипт, шкалы и сводку.

    Нужно для отладки конвейера и замеров качества (analytics/).
    TODO(Ярослав): тот же конвейер, что в run_call, но кадры читаются из WAV.
    """
    raise NotImplementedError("pipeline/runner.py: run_lesson")
