"""Озвучить демо урок по сценарию, дорожки участников и эталон реплик"""

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
LEAD_IN_S = 3.0
TAIL_S = 3.0
NOISE_FLOOR = 0.0015  # тихий шум вместо цифровой тишины
SEED = 84  # шум одинаковый при каждой сборке
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
    return text.replace("+", "")


def resample(audio: np.ndarray) -> np.ndarray:
    taps = np.arange(-48, 49)
    cutoff = 7200 / TTS_RATE
    fir = 2 * cutoff * np.sinc(2 * cutoff * taps) * np.hamming(len(taps))
    return np.convolve(audio, fir / fir.sum(), mode="same")[::3]


def cough(rng: np.random.Generator) -> np.ndarray:
    parts = []
    for loudness, length in ((0.5, 0.25), (0.3, 0.2)):
        n = int(length * RATE)
        burst = rng.normal(0, 1, n) * np.exp(-np.linspace(0, 6, n)) * loudness
        burst = np.convolve(burst, np.ones(8) / 8, mode="same")
        parts += [burst, np.zeros(int(0.12 * RATE))]
    return np.concatenate(parts)


def keyboard(rng: np.random.Generator, seconds: float = 3.0) -> np.ndarray:
    out = np.zeros(int(seconds * RATE))
    t = 0.0
    while t < seconds - 0.05:
        i = int(t * RATE)
        n = int(0.012 * RATE)
        out[i:i + n] += rng.normal(0, 1, n) * np.exp(-np.linspace(0, 8, n)) * rng.uniform(0.15, 0.35)
        t += rng.uniform(0.08, 0.3)
    return out


def layout(lines: list[Line]) -> None:
    t_free = LEAD_IN_S
    previous: Line | None = None
    for line in lines:
        if line.overlap > 0 and previous is not None:
            line.start = max(previous.start, previous.end - line.overlap)
        else:
            line.start = t_free + line.pause
        t_free = max(t_free, line.end)
        previous = line


def mix(lines: list[Line], speakers: list[str], rng: np.random.Generator) -> dict[str, np.ndarray]:
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
        out = csv.writer(f, lineterminator="\n")
        out.writerow(["speaker_id", "start_s", "end_s", "text", "kind", "addressed"])
        for line in sorted((x for x in lines if not x.is_noise), key=lambda x: x.start):
            out.writerow([line.speaker, f"{line.start:.2f}", f"{line.end:.2f}", plain(line.text), line.kind, line.addressed])


def voices(roster: dict) -> dict[str, tuple[str, str | None]]:
    people = [roster["teacher"], *roster["students"]]
    out = {}
    for person in people:
        match = re.fullmatch(r"(\w+)(?:, pitch (\w+))?", person.get("voice", ""))
        if match:
            out[person["id"]] = (match[1], match[2])
    return out


class Silero:
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
