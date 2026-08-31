from __future__ import annotations

import json
import wave
from pathlib import Path

import numpy as np
import numpy.typing as npt
import pytest

from arca.music.material import (
    MANIFEST_FILENAME,
    SAMPLE_RATE,
    Manifest,
    MaterialEntry,
    MaterialError,
    apply_fades,
    curate_sample,
    material_filename,
    midi_to_hz,
    normalize,
    read_manifest,
    read_wav_mono,
    sha256_file,
    validate_material,
    write_manifest,
    write_wav,
)
from arca.music.render import (
    ACTION_KEPT,
    ACTION_RENDERED,
    IMPULSE_FILE,
    RENDER_PLAN,
    impulse_response,
    modal_bell,
    render_material,
)

SCALE = 0.05


def dominant_hz(signal: npt.NDArray[np.float64]) -> float:
    spectrum = np.abs(np.fft.rfft(signal * np.hanning(signal.size)))
    freqs = np.fft.rfftfreq(signal.size, 1.0 / SAMPLE_RATE)
    usable = (freqs > 40.0) & (freqs < 6000.0)
    return float(freqs[usable][int(np.argmax(spectrum[usable]))])


def entry(name: str = "bell-060-mf.wav", digest: str = "0" * 64) -> MaterialEntry:
    return MaterialEntry(
        file=name,
        instrument="bell",
        note=60,
        layer="mf",
        seconds=1.0,
        sha256=digest,
        origin="rendered",
        license="none",
        source="test",
    )


def test_midi_to_hz_lands_on_concert_pitch() -> None:
    assert midi_to_hz(69) == pytest.approx(440.0)
    assert midi_to_hz(57) == pytest.approx(220.0)
    assert material_filename("bell", 60, "mf") == "bell-060-mf.wav"


def test_rendering_twice_produces_the_same_bytes(tmp_path: Path) -> None:
    first = render_material(tmp_path / "one", seconds_scale=SCALE)
    second = render_material(tmp_path / "two", seconds_scale=SCALE)
    assert [item.sha256 for item in first.entries] == [item.sha256 for item in second.entries]
    assert first.impulses[0].sha256 == second.impulses[0].sha256
    assert len(first.entries) == len(RENDER_PLAN)


def test_an_interrupted_render_keeps_what_it_had(tmp_path: Path) -> None:
    destination = tmp_path / "material"
    render_material(destination, seconds_scale=SCALE)
    victim = RENDER_PLAN[3]
    name = material_filename(victim.instrument, victim.note, victim.layer)
    (destination / name).unlink()
    actions: dict[str, str] = {}
    render_material(destination, seconds_scale=SCALE, progress=lambda n, a: actions.update({n: a}))
    assert actions[name] == ACTION_RENDERED
    assert actions[IMPULSE_FILE] == ACTION_KEPT
    kept = [key for key, action in actions.items() if action == ACTION_KEPT]
    assert len(kept) == len(RENDER_PLAN)


def test_a_corrupted_file_is_caught_before_use(tmp_path: Path) -> None:
    destination = tmp_path / "material"
    manifest = render_material(destination, seconds_scale=SCALE)
    assert validate_material(destination) == ()
    target = destination / manifest.entries[0].file
    target.write_bytes(target.read_bytes()[:2000] + b"\x00\x00")
    problems = validate_material(destination)
    assert any("hash mismatch" in problem for problem in problems)


def test_a_missing_file_is_caught_before_use(tmp_path: Path) -> None:
    destination = tmp_path / "material"
    manifest = render_material(destination, seconds_scale=SCALE)
    (destination / manifest.entries[2].file).unlink()
    problems = validate_material(destination)
    assert any("missing file" in problem for problem in problems)


def test_the_material_is_rendered_as_mono_wav(tmp_path: Path) -> None:
    destination = tmp_path / "material"
    manifest = render_material(destination, seconds_scale=SCALE)
    with wave.open(str(destination / manifest.entries[0].file), "rb") as handle:
        assert handle.getnchannels() == 1
        assert handle.getsampwidth() == 2
        assert handle.getframerate() == SAMPLE_RATE


def test_the_bell_puts_its_loudest_partial_on_the_written_note() -> None:
    for note in (60, 67, 72):
        voice = modal_bell(note, 3.0, np.random.default_rng(7))
        ratio = dominant_hz(voice) / midi_to_hz(note)
        assert ratio == pytest.approx(2.0, rel=0.04)


def test_the_impulse_response_starts_loud_and_decays() -> None:
    tail = impulse_response(1.5, np.random.default_rng(3))
    head = float(np.sqrt(np.mean(tail[: SAMPLE_RATE // 10] ** 2)))
    end = float(np.sqrt(np.mean(tail[-SAMPLE_RATE // 10 :] ** 2)))
    assert head > end * 8


def test_a_stereo_source_is_read_as_mono(tmp_path: Path) -> None:
    path = tmp_path / "stereo.wav"
    left = np.linspace(-0.5, 0.5, 1000)
    frames = np.stack([left, -left], axis=1)
    with wave.open(str(path), "wb") as handle:
        handle.setnchannels(2)
        handle.setsampwidth(2)
        handle.setframerate(SAMPLE_RATE)
        handle.writeframes(np.round(frames * 32767.0).astype("<i2").tobytes())
    signal, rate = read_wav_mono(path)
    assert rate == SAMPLE_RATE
    assert signal.size == 1000
    assert float(np.max(np.abs(signal))) < 0.001


def test_an_unreadable_sample_width_says_which_one(tmp_path: Path) -> None:
    path = tmp_path / "eight.wav"
    with wave.open(str(path), "wb") as handle:
        handle.setnchannels(1)
        handle.setsampwidth(1)
        handle.setframerate(SAMPLE_RATE)
        handle.writeframes(b"\x80" * 100)
    with pytest.raises(MaterialError, match="8 bit"):
        read_wav_mono(path)


def test_curation_trims_normalises_and_caps_the_length() -> None:
    silence = np.zeros(SAMPLE_RATE, dtype=np.float64)
    tone = np.sin(2.0 * np.pi * 220.0 * np.arange(SAMPLE_RATE * 4) / SAMPLE_RATE) * 0.05
    curated = curate_sample(np.concatenate([silence, tone]), SAMPLE_RATE, max_seconds=2.0)
    assert curated.size == SAMPLE_RATE * 2
    assert float(np.max(np.abs(curated))) == pytest.approx(0.89, abs=0.02)


def test_curation_can_start_from_an_offset() -> None:
    tone = np.sin(2.0 * np.pi * 220.0 * np.arange(SAMPLE_RATE * 4) / SAMPLE_RATE)
    curated = curate_sample(tone, SAMPLE_RATE, max_seconds=1.0, offset_seconds=2.0)
    assert curated.size == SAMPLE_RATE


def test_fades_land_on_silence_at_both_ends() -> None:
    shaped = apply_fades(np.ones(SAMPLE_RATE, dtype=np.float64), 0.1, 0.1)
    assert shaped[0] == pytest.approx(0.0, abs=1e-6)
    assert shaped[-1] == pytest.approx(0.0, abs=1e-3)
    assert shaped[SAMPLE_RATE // 2] == pytest.approx(1.0)


def test_normalising_silence_leaves_it_alone() -> None:
    silence = np.zeros(128, dtype=np.float64)
    assert float(np.max(np.abs(normalize(silence)))) == 0.0


def test_a_manifest_survives_the_round_trip(tmp_path: Path) -> None:
    manifest = Manifest("f9.0", "rendered", SAMPLE_RATE, (entry(),), (entry(IMPULSE_FILE),))
    write_manifest(tmp_path, manifest)
    assert read_manifest(tmp_path) == manifest


def test_a_manifest_with_an_unknown_origin_is_refused(tmp_path: Path) -> None:
    manifest = Manifest("f9.0", "rendered", SAMPLE_RATE, (entry(),), (entry(IMPULSE_FILE),))
    write_manifest(tmp_path, manifest)
    payload = json.loads((tmp_path / MANIFEST_FILENAME).read_text("utf-8"))
    payload["origin"] = "wishful"
    (tmp_path / MANIFEST_FILENAME).write_text(json.dumps(payload), "utf-8")
    with pytest.raises(MaterialError, match="unknown material origin"):
        read_manifest(tmp_path)


def test_a_malformed_entry_is_refused(tmp_path: Path) -> None:
    manifest = Manifest("f9.0", "rendered", SAMPLE_RATE, (entry(),), (entry(IMPULSE_FILE),))
    write_manifest(tmp_path, manifest)
    payload = json.loads((tmp_path / MANIFEST_FILENAME).read_text("utf-8"))
    payload["entries"][0].pop("note")
    (tmp_path / MANIFEST_FILENAME).write_text(json.dumps(payload), "utf-8")
    with pytest.raises(MaterialError, match="malformed manifest entry"):
        read_manifest(tmp_path)


def test_a_missing_manifest_is_a_problem_not_a_crash(tmp_path: Path) -> None:
    problems = validate_material(tmp_path)
    assert len(problems) == 1
    assert "no manifest" in problems[0]


def test_written_audio_hashes_the_same_from_the_same_samples(tmp_path: Path) -> None:
    signal = np.sin(2.0 * np.pi * 440.0 * np.arange(2048) / SAMPLE_RATE)
    write_wav(tmp_path / "a.wav", signal)
    write_wav(tmp_path / "b.wav", signal)
    assert sha256_file(tmp_path / "a.wav") == sha256_file(tmp_path / "b.wav")
