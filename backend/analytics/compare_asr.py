"""Сравнение моделей распознавания на живых фразах (#55).

    docker compose run --rm --no-deps -v "$PWD:/srv" -w /srv/backend app python -m analytics.compare_asr

Набор — data/asr_check/labels.csv и wav рядом с ним: file, speaker, text, kind
(question, answer, other), intonation_only, noise. Для каждой модели считает
ошибки в словах, долю вопросов со знаком, лишние «?» и секунды счёта на секунду
речи и печатает таблицу в markdown. Настройки Whisper — те же, что у учеников.
"""

import argparse
import csv
import re
import time
from collections.abc import Callable
from dataclasses import dataclass
from pathlib import Path

import numpy as np

SAMPLE_RATE = 16_000
DEFAULT_MODELS = ["small", "medium", "large-v3-turbo", "gigaam-v3-e2e-ctc"]


@dataclass
class Phrase:
    file: str
    text: str
    kind: str
    intonation_only: bool
    noise: bool
    audio: np.ndarray


def words(text: str) -> list[str]:
    return re.findall(r"[а-яa-z0-9-]+", text.lower().replace("ё", "е"))


def edit_distance(ref: list[str], hyp: list[str]) -> int:
    row = list(range(len(hyp) + 1))
    for i, r in enumerate(ref, 1):
        prev, row[0] = row[0], i
        for j, h in enumerate(hyp, 1):
            prev, row[j] = row[j], min(row[j] + 1, row[j - 1] + 1, prev + (r != h))
    return row[-1]


def load_set(directory: Path) -> list[Phrase]:
    from faster_whisper.audio import decode_audio

    labels = directory / "labels.csv"
    if not labels.exists():
        raise SystemExit(f"нет разметки {labels}: набор собирается по задачам #57 и #58")
    phrases = []
    for row in csv.DictReader(labels.open(encoding="utf-8")):
        audio = decode_audio(str(directory / row["file"]), sampling_rate=SAMPLE_RATE)
        phrases.append(
            Phrase(row["file"], row["text"], row["kind"], row["intonation_only"] == "1", row["noise"] == "1", audio)
        )
    return phrases


def whisper(name: str) -> Callable[[np.ndarray], str]:
    from app.pipeline import asr

    model = asr._load(name)

    def run(audio: np.ndarray) -> str:
        segments, _ = model.transcribe(audio, **asr.options("student", audio))
        return asr.join_segments(segments)

    return run


def gigaam(name: str) -> Callable[[np.ndarray], str]:
    from app.pipeline import asr

    model = asr._load(name)
    return lambda audio: model.recognize(audio, sample_rate=SAMPLE_RATE)


def recognizer(name: str) -> Callable[[np.ndarray], str]:
    from app.pipeline.asr import is_gigaam

    return gigaam(name) if is_gigaam(name) else whisper(name)


def share(hits: int, total: int) -> str:
    return f"{hits}/{total} ({hits / total:.0%})" if total else "—"


def evaluate(name: str, phrases: list[Phrase], show_text: bool) -> dict[str, str]:
    run = recognizer(name)
    run(phrases[0].audio)  # первый проход прогревает модель и в замер не идёт
    errors = reference = 0
    spent = spoken = 0.0
    marked = marked_intonation = extra = 0
    for phrase in phrases:
        started = time.perf_counter()
        text = run(phrase.audio)
        spent += time.perf_counter() - started
        spoken += len(phrase.audio) / SAMPLE_RATE
        expected = words(phrase.text)
        errors += edit_distance(expected, words(text))
        reference += len(expected)
        has_mark = "?" in text
        if phrase.kind == "question":
            marked += has_mark
            marked_intonation += has_mark and phrase.intonation_only
        else:
            extra += has_mark
        if show_text:
            print(f"  {name:20} {phrase.file:24} «{text}»", flush=True)
    questions = [p for p in phrases if p.kind == "question"]
    return {
        "Модель": name,
        "Ошибки в словах": f"{errors / reference:.1%}" if reference else "—",
        "Вопросы со знаком": share(marked, len(questions)),
        "Из них только интонацией": share(marked_intonation, sum(p.intonation_only for p in questions)),
        "Лишние «?»": share(extra, len(phrases) - len(questions)),
        "Счёт на секунду речи": f"{spent / spoken:.2f} с",
        # Whisper дополняет любую фразу до 30 секунд, поэтому короткая реплика стоит почти как длинная
        "Счёт на фразу": f"{spent / len(phrases):.1f} с",
    }


def table(rows: list[dict[str, str]]) -> str:
    head = list(rows[0])
    lines = ["| " + " | ".join(head) + " |", "|" + "---|" * len(head)]
    lines += ["| " + " | ".join(row[h] for h in head) + " |" for row in rows]
    return "\n".join(lines)


def main() -> None:
    parser = argparse.ArgumentParser(description="Сравнить модели распознавания на живых фразах")
    parser.add_argument("directory", type=Path, nargs="?", default=Path("data/asr_check"))
    parser.add_argument("--models", nargs="+", default=DEFAULT_MODELS)
    parser.add_argument("--show-text", action="store_true", help="печатать, что распознала каждая модель")
    args = parser.parse_args()

    phrases = load_set(args.directory)
    print(f"Фраз: {len(phrases)}, вопросов: {sum(p.kind == 'question' for p in phrases)}", flush=True)
    rows = []
    for name in args.models:
        print(f"Считаю {name}…", flush=True)
        rows.append(evaluate(name, phrases, args.show_text))
    print()
    print(table(rows))


if __name__ == "__main__":
    main()
