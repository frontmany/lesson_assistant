"""Сводка и карточки по демо-уроку без звонка: для правки промптов (#20, #21).

    docker compose run --rm --no-deps -v "$PWD:/srv" -w /srv app python -m analytics.summary_demo
    ... python -m analytics.summary_demo --runs 3 --model GigaChat-3-Ultra

Реплики берутся из эталона data/demo_lesson/ground_truth.csv, тип реплик
учеников — классификатор конвейера, ступени — шкала с нормой из
history_seed.json, «кто давно не отвечал» — тоже оттуда. Печатает JSON сводки
и карточек и время ответа. Без LLM_API_KEY — шаблонный вариант.
"""

import argparse
import csv
import dataclasses
import time

from app.config import settings
from app.db.classes import _demo_roster
from app.pipeline import llm, summary
from app.pipeline.classify import classify
from app.pipeline.scoring import Scoreboard, lesson_norm

SEED_STREAKS_LESSONS = 5


def demo_lesson() -> tuple:
    roster = _demo_roster()
    ids = [s.id for s in roster.students]
    utterances: list[dict] = []
    board = Scoreboard(ids, lesson_norm(_seed()), settings.lesson_minutes * 60)
    with open(settings.demo_lesson_dir / "ground_truth.csv", encoding="utf-8") as f:
        rows = list(csv.DictReader(f))
    for row in rows:
        who, t, end = row["speaker_id"], float(row["start_s"]), float(row["end_s"])
        item = {"student_id": who, "t": t, "end": end, "text": row["text"]}
        if who in ids:
            item["kind"] = classify(row["text"], who, t, roster, utterances)
            board.add(who, item["kind"], row["text"], t, end)
        utterances.append(item)
    ended = utterances[-1]["end"]
    levels = {sid: (board.level(sid), board.score(sid), board.flags(sid, ended)) for sid in ids}
    return roster, utterances, levels


def _seed() -> list[float]:
    from app.db.results import seed_history

    return seed_history()


def seed_streaks(ids: list[str]) -> dict[str, int]:
    """Как results.silent_streaks у урока без класса, но без базы."""
    import json

    lessons = json.loads((settings.demo_lesson_dir / "history_seed.json").read_text(encoding="utf-8"))["lessons"]
    streaks = {}
    for sid in ids:
        n = 0
        for item in reversed(lessons[-SEED_STREAKS_LESSONS:]):
            if sid in item.get("replied", []):
                break
            n += 1
        streaks[sid] = n
    return streaks


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--runs", type=int, default=1)
    parser.add_argument("--model", help="вместо LLM_MODEL")
    args = parser.parse_args()
    if args.model:
        llm.settings = dataclasses.replace(settings, llm_model=args.model)
    roster, utterances, levels = demo_lesson()
    streaks = seed_streaks(list(levels))
    print("ступени:", {sid: lv[0] for sid, lv in levels.items()}, "молчит уроков:", streaks)
    print("модель:", (llm.settings.llm_model or llm.DEFAULT_MODEL) if llm.enabled() else "шаблон, LLM_API_KEY нет")
    for run in range(1, args.runs + 1):
        started = time.time()
        s = summary.build_summary(roster, utterances, levels, streaks)
        cards = summary.build_cards(roster, utterances, levels)
        print(f"\n=== прогон {run}, {time.time() - started:.1f} с")
        print(summary.dump(s, cards))


if __name__ == "__main__":
    main()
