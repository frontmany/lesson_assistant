"""Запуск из командной строки.

    python -m app.cli replay data/demo_lesson          # записанный урок без звонка
    python -m app.cli virtual-class data/demo_lesson --lesson <id>   # записанный урок заходит в звонок ботами
    python -m app.cli mock-summary                     # сводка из мока
"""

import argparse
import asyncio
import json
from pathlib import Path

from app import mocks
from app.models import Roster


def _load(lesson_dir: Path) -> tuple[Roster, list[Path]]:
    roster = Roster.model_validate_json((lesson_dir / "roster.json").read_text(encoding="utf-8"))
    tracks = sorted((lesson_dir / "tracks").glob("*.wav"))
    print(f"Урок: {roster.class_name}, {roster.subject}, тема «{roster.topic}»")
    print(f"Учеников в составе: {len(roster.students)}, дорожек найдено: {len(tracks)}")
    return roster, tracks


def replay(lesson_dir: Path) -> None:
    roster, tracks = _load(lesson_dir)
    from app.pipeline.runner import run_lesson

    try:
        run_lesson(roster, tracks)
    except NotImplementedError as exc:
        print(f"\nКонвейер ещё не готов: {exc}")


def virtual_class(lesson_dir: Path, lesson_id: str | None) -> None:
    from app.sources.virtual_class import load_tracks, play

    roster, _ = _load(lesson_dir)
    try:
        roster, tracks = load_tracks(lesson_dir)
    except FileNotFoundError as exc:
        raise SystemExit(f"Виртуальный класс: {exc}") from exc
    lesson_id = lesson_id or roster.lesson_id
    print(f"Заводим запись в урок {lesson_id}. Чтобы ассистент засчитал ботов, урок начинают так:")
    print(f"  curl -X POST 'http://localhost:8000/api/lessons/{lesson_id}/start?virtual=true'")
    asyncio.run(play(lesson_id, roster, tracks))


def main() -> None:
    parser = argparse.ArgumentParser(prog="python -m app.cli")
    sub = parser.add_subparsers(dest="command", required=True)

    p_replay = sub.add_parser("replay", help="прогнать записанный урок через конвейер без звонка")
    p_replay.add_argument("lesson_dir", type=Path)

    p_virtual = sub.add_parser("virtual-class", help="завести записанный урок в звонок ботами")
    p_virtual.add_argument("lesson_dir", type=Path)
    p_virtual.add_argument("--lesson", help="id урока, в который заходят боты; по умолчанию из roster.json")

    sub.add_parser("mock-summary", help="показать сводку учителю из мока")

    args = parser.parse_args()
    if args.command == "replay":
        replay(args.lesson_dir)
    elif args.command == "virtual-class":
        virtual_class(args.lesson_dir, args.lesson)
    elif args.command == "mock-summary":
        print(json.dumps(mocks.load("summary.json"), ensure_ascii=False, indent=2))


if __name__ == "__main__":
    main()
