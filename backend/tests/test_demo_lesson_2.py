"""Второй демо урок, сценарий и сборка дорожек на подставном голосе"""

import csv
import importlib.util
import json
import shutil
import wave
from pathlib import Path

import numpy as np
import pytest

from app.models import Roster

ROOT = Path(__file__).parent.parent
LESSON = ROOT / "data" / "demo_lesson_2"
spec = importlib.util.spec_from_file_location("make_demo_lesson", ROOT.parent / "scripts" / "make_demo_lesson.py")
make = importlib.util.module_from_spec(spec)
spec.loader.exec_module(make)

KINDS = {"answer", "initiative", "response", "other"}


def rows() -> list[dict]:
    with (LESSON / "scenario.csv").open(encoding="utf-8") as f:
        return list(csv.DictReader(f))


def fake_speak(text, voice, pitch):
    n = int(len(make.plain(text)) * 0.07 * make.TTS_RATE)
    return 0.3 * np.sin(2 * np.pi * 220 * np.arange(n) / make.TTS_RATE)


def test_roster_has_full_names_and_voices():
    data = json.loads((LESSON / "roster.json").read_text(encoding="utf-8"))
    roster = Roster.model_validate(data)
    assert len(roster.students) == 6
    assert all(len(s.name.split()) == 2 for s in roster.students)
    assert [s.name.split()[0] for s in roster.students].count("Иван") == 2
    assert set(make.voices(data)) == {"teacher", "ivan_petrov", "ivan_sidorov", "olga_smirnova", "kirill_belov", "anna_kozlova"}
    assert make.voices(data)["kirill_belov"] == ("aidar", "low")


def test_scenario_is_labeled_and_has_hard_places():
    lines = rows()
    speakers = {r["speaker_id"] for r in lines}
    assert "egor_volkov" in speakers
    speech = [r for r in lines if r["text"] not in make.NOISES]
    assert all(r["kind"] in KINDS and r["addressed"] in {"0", "1"} for r in speech)
    assert {r["text"] for r in lines if r["speaker_id"] == "egor_volkov"} <= set(make.NOISES)
    text = " ".join(r["text"] for r in speech)
    assert "Си+доров, куда плыл" in text
    assert "Ива+н, а ты как думаешь" in text
    assert "Ива+н Си+доров" in text
    assert "Ва+ня" in text
    assert any(float(r["pause_before_s"]) >= 10 and r["speaker_id"] == "teacher" for r in lines)
    assert any(float(r["pause_before_s"]) >= 10 and r["speaker_id"] == "kirill_belov" for r in lines)
    assert any(float(r["overlap_s"]) > 0 and r["speaker_id"] == "olga_smirnova" for r in speech)
    assert "Мари+на Серге+евна, а почему" in text
    assert "зарядка" in text
    assert not any(ch.isdigit() for ch in text)


def test_build_lays_out_tracks_and_ground_truth(tmp_path):
    lesson = tmp_path / "lesson"
    lesson.mkdir()
    for name in ("roster.json", "scenario.csv"):
        shutil.copy(LESSON / name, lesson / name)

    lines = make.build(lesson, fake_speak)

    tracks = sorted((lesson / "tracks").glob("*.wav"))
    assert {t.stem for t in tracks} == {"teacher", "ivan_petrov", "ivan_sidorov", "olga_smirnova",
                                        "kirill_belov", "anna_kozlova", "egor_volkov"}
    lengths = set()
    for t in tracks:
        with wave.open(str(t)) as w:
            assert (w.getframerate(), w.getnchannels(), w.getsampwidth()) == (16000, 1, 2)
            lengths.add(w.getnframes())
            pcm = np.frombuffer(w.readframes(w.getnframes()), dtype=np.int16)
        assert np.abs(pcm[: 16000 * 2]).max() > 0
    assert len(lengths) == 1

    with (lesson / "ground_truth.csv").open(encoding="utf-8") as f:
        truth = list(csv.DictReader(f))
    assert len(truth) == len([x for x in lines if not x.is_noise])
    assert all("+" not in r["text"] for r in truth)
    assert float(truth[0]["start_s"]) == pytest.approx(make.LEAD_IN_S)
    starts = [float(r["start_s"]) for r in truth]
    assert starts == sorted(starts)

    by_text = {make.plain(x.text): x for x in lines}
    petrov = by_text["В Европу повезли золото, серебро и новые растения, например картофель."]
    smirnova = by_text["Ещё кукурузу и помидоры!"]
    assert smirnova.start == pytest.approx(petrov.start)
    belov = by_text["Васко да Гама. В тысяча четыреста девяносто восьмом году."]
    question = lines[lines.index(belov) - 1]
    assert belov.start - question.end == pytest.approx(10, abs=0.01)


def test_resample_keeps_speech_band_and_drops_high():
    t = np.arange(make.TTS_RATE) / make.TTS_RATE
    low = make.resample(np.sin(2 * np.pi * 1000 * t))
    high = make.resample(np.sin(2 * np.pi * 12000 * t))
    assert len(low) == make.RATE
    assert np.abs(low[1000:-1000]).max() > 0.9
    assert np.abs(high[1000:-1000]).max() < 0.05
