"""Прогон записанного урока через конвейер без звонка и сверка типов реплик с эталоном.

    docker compose run --rm --no-deps -v "$PWD:/srv" -w /srv/backend app python -m analytics.replay_kinds
    ... python -m analytics.replay_kinds --teacher small gigaam-v3-e2e-ctc

Дорожки из data/demo_lesson подаются в transcribe_stream кадрами по 30 мс,
в SPEEDUP раз быстрее реального времени; часы конвейера подменены временем
записи, поэтому паузы между репликами те же, что на уроке. Для каждой модели
учителя печатает речь учителя и совпадение типов реплик учеников с
ground_truth.csv (ученики — на модели по умолчанию).
"""

import argparse
import asyncio
import csv
import dataclasses
import time
from pathlib import Path

import numpy as np

from app.config import settings
from app.pipeline import asr, runner
from app.sources.virtual_class import FRAME, load_tracks, read_track

SAMPLE_RATE = 16_000
TICK_S = FRAME / SAMPLE_RATE
SPEEDUP = 5  # быстрее нельзя: поиск речи не успеет, и конвейер начнёт сбрасывать кадры


class Clock:
    """Часы конвейера: время записи, а не настоящее."""

    now = 0.0

    def monotonic(self) -> float:
        return self.now

    def __getattr__(self, name):
        return getattr(time, name)


async def replay(lesson_dir: Path, teacher_model: str) -> list[dict]:
    asr.settings = dataclasses.replace(settings, whisper_model_teacher=teacher_model)
    roster, tracks = load_tracks(lesson_dir)
    pcms = {t.stem: read_track(t) for t in tracks}
    clock = Clock()
    runner.time = clock
    longest = max(len(p) for p in pcms.values())

    async def frames():
        for i in range(0, longest, FRAME):
            for who, pcm in pcms.items():
                chunk = pcm[i:i + FRAME]
                if len(chunk) < FRAME:
                    chunk = np.pad(chunk, (0, FRAME - len(chunk)))
                yield who, chunk.tobytes()
            clock.now += TICK_S
            await asyncio.sleep(TICK_S / SPEEDUP)

    return await runner.transcribe_stream(frames(), roster, lambda event: None)


def compare(lesson_dir: Path, utterances: list[dict]) -> str:
    rows = [r for r in csv.DictReader(open(lesson_dir / "ground_truth.csv", encoding="utf-8")) if r["speaker_id"] != "teacher"]
    hits = 0
    per_kind: dict[str, list[int]] = {}
    for r in rows:
        start, end = float(r["start_s"]), float(r["end_s"])
        mine = [u for u in utterances if u["student_id"] == r["speaker_id"] and u["t"] < end + 1 and u["end"] > start - 1]
        got = mine[0]["kind"] if mine else "—"
        hits += got == r["kind"]
        per_kind.setdefault(r["kind"], [0, 0])
        per_kind[r["kind"]][0] += got == r["kind"]
        per_kind[r["kind"]][1] += 1
        if got != r["kind"]:
            print(f"    {r['speaker_id']} {start:.0f} с: эталон {r['kind']}, конвейер {got}")
    kinds = ", ".join(f"{k} {v[0]}/{v[1]}" for k, v in sorted(per_kind.items()))
    return f"совпало {hits}/{len(rows)} ({kinds})"


async def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("lesson_dir", type=Path, nargs="?", default=settings.demo_lesson_dir)
    parser.add_argument("--teacher", nargs="+", default=[settings.whisper_model_teacher])
    args = parser.parse_args()
    for model in args.teacher:
        started = time.time()
        utterances = await replay(args.lesson_dir, model)
        print(f"\n=== учитель: {model}, прогон {time.time() - started:.0f} с")
        for u in utterances:
            if u["student_id"] == "teacher":
                print(f"    учитель {u['t']:.0f} с: {u['text']}")
        print(f"{model}: {compare(args.lesson_dir, utterances)}")


if __name__ == "__main__":
    asyncio.run(main())
