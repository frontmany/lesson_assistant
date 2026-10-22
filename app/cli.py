"""Запуск из командной строки.

    python -m app.cli replay data/demo_lesson          # записанный урок без звонка
    python -m app.cli virtual-class data/demo_lesson   # записанный урок заходит в звонок ботами
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


def virtual_class(lesson_dir: Path) -> None:
    roster, tracks = _load(lesson_dir)
    from app.sources.virtual_class import play

    try:
        asyncio.run(play(roster.lesson_id, roster, tracks))
    except NotImplementedError as exc:
        print(f"\nВиртуальный класс ещё не готов: {exc}")


def main() -> None:
    parser = argparse.ArgumentParser(prog="python -m app.cli")
    sub = parser.add_subparsers(dest="command", required=True)

    p_replay = sub.add_parser("replay", help="прогнать записанный урок через конвейер без звонка")
    p_replay.add_argument("lesson_dir", type=Path)

    p_virtual = sub.add_parser("virtual-class", help="завести записанный урок в звонок ботами")
    p_virtual.add_argument("lesson_dir", type=Path)

    sub.add_parser("mock-summary", help="показать сводку учителю из мока")

    args = parser.parse_args()
    if args.command == "replay":
        replay(args.lesson_dir)
    elif args.command == "virtual-class":
        virtual_class(args.lesson_dir)
    elif args.command == "mock-summary":
        print(json.dumps(mocks.load("summary.json"), ensure_ascii=False, indent=2))


if __name__ == "__main__":
    main()
