"""Озвучить демо-урок по сценарию: дорожки участников и эталон реплик (#84).

    docker run --rm -v "$PWD:/srv" -v lesson-tts-cache:/root/.cache -w /srv python:3.12-slim sh -c \
        "pip install -r scripts/requirements-tts.txt && python scripts/make_demo_lesson.py backend/data/demo_lesson_2"

Том lesson-tts-cache хранит pip, torch.hub и модель Silero между запусками:
без него всё это скачивается заново при каждой сборке.

Читает из папки урока scenario.csv и roster.json, озвучивает каждую реплику
Silero TTS (v4_ru, 48 кГц) голосом из roster.json, раскладывает реплики по
времени и пишет tracks/<id>.wav (16 кГц, моно, все одной длины) и
ground_truth.csv. Время в эталоне — из раскладки, а не на слух.

scenario.csv — строка на реплику: speaker_id, pause_before_s, overlap_s, text, kind, addressed.
- pause_before_s — тишина после конца прошлой реплики;
- overlap_s — начать на столько секунд раньше конца прошлой реплики; больше её
  длины — начать одновременно с ней (говорят одновременно);
- text — ударение знаком «+» перед ударной гласной, числа словами. Текст
  «{кашель}» или «{клавиатура}» — не речь, а шум на дорожке этого участника:
  в эталон он не идёт. Шум сгенерирован здесь же, без чужих записей.

Голос в roster.json: «aidar», «xenia» и т. д.; «aidar, pitch low» — тот же
голос ниже тоном (SSML). У того, кто только шумит, голоса нет.
"""

import csv
import json
import re
import sys
import wave
from dataclasses import dataclass
from pathlib import Path

import numpy as np

TTS_RATE = 48_000
RATE = 16_000
LEAD_IN_S = 3.0  # урок начинается после трёх секунд тишины, как первый
TAIL_S = 3.0
NOISE_FLOOR = 0.0015  # очень тихий шум вместо цифровой тишины, как у живого микрофона
SEED = 84  # шум и стук одинаковые при каждой сборке
NOISES = ("{кашель}", "{клавиатура}")


@dataclass
class Line:
    speaker: str
    pause: float
    overlap: float
    text: str
    kind: str
    addressed: str
    start: float = 0.0
    audio: np.ndarray | None = None

    @property
    def end(self) -> float:
        return self.start + len(self.audio) / RATE

    @property
    def is_noise(self) -> bool:
        return self.text in NOISES


def read_scenario(path: Path) -> list[Line]:
    with path.open(encoding="utf-8") as f:
        return [
            Line(r["speaker_id"], float(r["pause_before_s"]), float(r["overlap_s"]), r["text"], r["kind"], r["addressed"])
            for r in csv.DictReader(f)
        ]


def plain(text: str) -> str:
    """Текст для эталона: без знаков ударения."""
    return text.replace("+", "")


def resample(audio: np.ndarray) -> np.ndarray:
    """48 кГц → 16 кГц: низкие частоты до 7,2 кГц и каждый третий отсчёт."""
    taps = np.arange(-48, 49)
    cutoff = 7200 / TTS_RATE
    fir = 2 * cutoff * np.sinc(2 * cutoff * taps) * np.hamming(len(taps))
    return np.convolve(audio, fir / fir.sum(), mode="same")[::3]


def cough(rng: np.random.Generator) -> np.ndarray:
    """Кашель: два коротких выдоха шумом, громкий и тише."""
    parts = []
    for loudness, length in ((0.5, 0.25), (0.3, 0.2)):
        n = int(length * RATE)
        burst = rng.normal(0, 1, n) * np.exp(-np.linspace(0, 6, n)) * loudness
        # «голос» кашля — шум, пропущенный через грубый фильтр низких частот
        burst = np.convolve(burst, np.ones(8) / 8, mode="same")
        parts += [burst, np.zeros(int(0.12 * RATE))]
    return np.concatenate(parts)


def keyboard(rng: np.random.Generator, seconds: float = 3.0) -> np.ndarray:
    """Стук по клавиатуре: короткие щелчки с неровными паузами."""
    out = np.zeros(int(seconds * RATE))
    t = 0.0
    while t < seconds - 0.05:
        i = int(t * RATE)
        n = int(0.012 * RATE)
        out[i:i + n] += rng.normal(0, 1, n) * np.exp(-np.linspace(0, 8, n)) * rng.uniform(0.15, 0.35)
        t += rng.uniform(0.08, 0.3)
    return out


def layout(lines: list[Line]) -> None:
    """Расставить реплики по времени: пауза после прошлой или наложение на неё."""
    t_free = LEAD_IN_S  # когда закончилась последняя реплика
    previous: Line | None = None
    for line in lines:
        if line.overlap > 0 and previous is not None:
            line.start = max(previous.start, previous.end - line.overlap)
        else:
            line.start = t_free + line.pause
        t_free = max(t_free, line.end)
        previous = line


def mix(lines: list[Line], speakers: list[str], rng: np.random.Generator) -> dict[str, np.ndarray]:
    """Дорожка на участника, все одной длины, с тихим шумом вместо тишины."""
    length = int((max(line.end for line in lines) + TAIL_S) * RATE)
    tracks = {sid: rng.normal(0, NOISE_FLOOR, length) for sid in speakers}
    for line in lines:
        i = int(line.start * RATE)
        tracks[line.speaker][i:i + len(line.audio)] += line.audio
    return tracks


def write_wav(path: Path, audio: np.ndarray) -> None:
    pcm = (np.clip(audio, -1, 1) * 32767).astype(np.int16)
    with wave.open(str(path), "wb") as out:
        out.setnchannels(1)
        out.setsampwidth(2)
        out.setframerate(RATE)
        out.writeframes(pcm.tobytes())


def write_ground_truth(path: Path, lines: list[Line]) -> None:
    with path.open("w", encoding="utf-8", newline="") as f:
        out = csv.writer(f, lineterminator="\n")  # в репозитории переводы строк LF
        out.writerow(["speaker_id", "start_s", "end_s", "text", "kind", "addressed"])
        for line in sorted((x for x in lines if not x.is_noise), key=lambda x: x.start):
            out.writerow([line.speaker, f"{line.start:.2f}", f"{line.end:.2f}", plain(line.text), line.kind, line.addressed])


def voices(roster: dict) -> dict[str, tuple[str, str | None]]:
    """id → (голос Silero, высота тона для SSML или None)."""
    people = [roster["teacher"], *roster["students"]]
    out = {}
    for person in people:
        match = re.fullmatch(r"(\w+)(?:, pitch (\w+))?", person.get("voice", ""))
        if match:
            out[person["id"]] = (match[1], match[2])
    return out


class Silero:
    """Silero TTS v4_ru: ударение — «+», остальное расставляет put_accent."""

    def __init__(self) -> None:
        import torch

        self.model, _ = torch.hub.load("snakers4/silero-models", "silero_tts", language="ru", speaker="v4_ru")

    def __call__(self, text: str, voice: str, pitch: str | None) -> np.ndarray:
        options = {"speaker": voice, "sample_rate": TTS_RATE, "put_accent": True, "put_yo": True}
        if pitch:
            audio = self.model.apply_tts(ssml_text=f'<speak><prosody pitch="{pitch}">{text}</prosody></speak>', **options)
        else:
            audio = self.model.apply_tts(text=text, **options)
        return audio.numpy().astype(np.float64)


def build(lesson_dir: Path, speak) -> list[Line]:
    """Собрать урок; speak(text, voice, pitch) → звук 48 кГц. Возвращает разложенные реплики."""
    roster = json.loads((lesson_dir / "roster.json").read_text(encoding="utf-8"))
    lines = read_scenario(lesson_dir / "scenario.csv")
    speakers = [roster["teacher"]["id"], *(s["id"] for s in roster["students"])]
    unknown = {line.speaker for line in lines} - set(speakers)
    if unknown:
        raise SystemExit(f"в сценарии участники не из roster.json: {', '.join(sorted(unknown))}")
    voice_of = voices(roster)
    rng = np.random.default_rng(SEED)
    for n, line in enumerate(lines, 1):
        if line.text == "{кашель}":
            line.audio = cough(rng)
        elif line.text == "{клавиатура}":
            line.audio = keyboard(rng)
        else:
            if line.speaker not in voice_of:
                raise SystemExit(f"строка {n + 1}: у {line.speaker} нет голоса в roster.json")
            line.audio = resample(speak(line.text, *voice_of[line.speaker]))
            print(f"{n:3}/{len(lines)} {line.speaker:14} {len(line.audio) / RATE:5.1f} с  {plain(line.text)}")
    layout(lines)
    tracks_dir = lesson_dir / "tracks"
    tracks_dir.mkdir(exist_ok=True)
    for sid, audio in mix(lines, speakers, rng).items():
        write_wav(tracks_dir / f"{sid}.wav", audio)
    write_ground_truth(lesson_dir / "ground_truth.csv", lines)
    return lines


def main() -> None:
    if len(sys.argv) != 2:
        raise SystemExit("python scripts/make_demo_lesson.py <папка урока>")
    lesson_dir = Path(sys.argv[1])
    lines = build(lesson_dir, Silero())
    length = max(line.end for line in lines) + TAIL_S
    print(f"Готово: {lesson_dir}/tracks, ground_truth.csv; урок {length / 60:.1f} мин")


if __name__ == "__main__":
    main()
