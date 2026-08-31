from __future__ import annotations

import hashlib
import json
import wave
from dataclasses import dataclass
from pathlib import Path
from typing import Any

import numpy as np
import numpy.typing as npt

Signal = npt.NDArray[np.float64]

SAMPLE_RATE = 44100
MATERIAL_VERSION = "f9.0"
MANIFEST_FILENAME = "manifest.json"
IMPULSE_DIRNAME = "ir"
LICENSE_FILENAME = "LICENSE.txt"
PEAK = 0.89
SILENCE = 1e-9
ORIGIN_RENDERED = "rendered"
ORIGIN_SAMPLED = "sampled"
ORIGINS = (ORIGIN_RENDERED, ORIGIN_SAMPLED)
READ_BLOCK = 1 << 20


class MaterialError(RuntimeError):
    pass


@dataclass(frozen=True)
class MaterialEntry:
    file: str
    instrument: str
    note: int
    layer: str
    seconds: float
    sha256: str
    origin: str
    license: str
    source: str

    def as_json(self) -> dict[str, Any]:
        return {
            "file": self.file,
            "instrument": self.instrument,
            "note": self.note,
            "layer": self.layer,
            "seconds": round(self.seconds, 4),
            "sha256": self.sha256,
            "origin": self.origin,
            "license": self.license,
            "source": self.source,
        }


@dataclass(frozen=True)
class Manifest:
    material_version: str
    origin: str
    sample_rate: int
    entries: tuple[MaterialEntry, ...]
    impulses: tuple[MaterialEntry, ...]

    def as_json(self) -> dict[str, Any]:
        return {
            "material_version": self.material_version,
            "origin": self.origin,
            "sample_rate": self.sample_rate,
            "entries": [entry.as_json() for entry in self.entries],
            "impulses": [entry.as_json() for entry in self.impulses],
        }

    def by_file(self) -> dict[str, MaterialEntry]:
        return {entry.file: entry for entry in (*self.entries, *self.impulses)}


def midi_to_hz(note: int) -> float:
    return float(440.0 * 2.0 ** ((note - 69) / 12.0))


def material_filename(instrument: str, note: int, layer: str) -> str:
    return f"{instrument}-{note:03d}-{layer}.wav"


def seeded_generator(name: str) -> np.random.Generator:
    digest = hashlib.sha256(name.encode("utf-8")).digest()
    return np.random.default_rng(int.from_bytes(digest[:8], "big"))


def normalize(signal: Signal, peak: float = PEAK) -> Signal:
    top = float(np.max(np.abs(signal))) if signal.size else 0.0
    if top < SILENCE:
        return signal
    return signal * (peak / top)


def apply_fades(signal: Signal, attack: float, release: float) -> Signal:
    frames = signal.size
    head = min(int(attack * SAMPLE_RATE), frames // 2)
    tail = min(int(release * SAMPLE_RATE), frames // 2)
    shaped = signal.copy()
    if head > 0:
        shaped[:head] *= 0.5 - 0.5 * np.cos(np.linspace(0.0, np.pi, head))
    if tail > 0:
        shaped[frames - tail :] *= 0.5 + 0.5 * np.cos(np.linspace(0.0, np.pi, tail))
    return shaped


def band_noise(frames: int, low: float, high: float, generator: np.random.Generator) -> Signal:
    if frames <= 1:
        return np.zeros(max(frames, 0), dtype=np.float64)
    noise: Signal = generator.standard_normal(frames)
    spectrum = np.fft.rfft(noise)
    freqs = np.fft.rfftfreq(frames, 1.0 / SAMPLE_RATE)
    below = (freqs / max(low, 1e-6)) ** 2
    highpass = below / (1.0 + below)
    lowpass = 1.0 / (1.0 + (freqs / max(high, 1e-6)) ** 4)
    shaped: Signal = np.fft.irfft(spectrum * highpass * lowpass, n=frames)
    return shaped


def write_wav(path: Path, signal: Signal, sample_rate: int = SAMPLE_RATE) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    frames = np.round(np.clip(signal, -1.0, 1.0) * 32767.0).astype("<i2")
    with wave.open(str(path), "wb") as handle:
        handle.setnchannels(1)
        handle.setsampwidth(2)
        handle.setframerate(sample_rate)
        handle.writeframes(frames.tobytes())


def read_wav_mono(path: Path) -> tuple[Signal, int]:
    with wave.open(str(path), "rb") as handle:
        channels = handle.getnchannels()
        width = handle.getsampwidth()
        rate = handle.getframerate()
        raw = handle.readframes(handle.getnframes())
    if width == 2:
        samples = np.frombuffer(raw, dtype="<i2").astype(np.float64) / 32768.0
    elif width == 3:
        packed = np.frombuffer(raw, dtype=np.uint8).reshape(-1, 3).astype(np.uint32)
        words = packed[:, 0] | (packed[:, 1] << 8) | (packed[:, 2] << 16)
        signed = np.where(words >= 1 << 23, words.astype(np.int64) - (1 << 24), words)
        samples = signed.astype(np.float64) / 8388608.0
    elif width == 4:
        samples = np.frombuffer(raw, dtype="<i4").astype(np.float64) / 2147483648.0
    else:
        raise MaterialError(f"unsupported sample width in {path.name}: {width * 8} bit")
    if channels > 1:
        usable = samples.size - samples.size % channels
        samples = samples[:usable].reshape(-1, channels).mean(axis=1)
    return np.ascontiguousarray(samples), rate


def resample(signal: Signal, source_rate: int, target_rate: int = SAMPLE_RATE) -> Signal:
    if source_rate == target_rate or signal.size == 0:
        return signal
    frames = round(signal.size * target_rate / source_rate)
    source_positions = np.arange(signal.size, dtype=np.float64) / source_rate
    target_positions = np.arange(frames, dtype=np.float64) / target_rate
    resampled: Signal = np.interp(target_positions, source_positions, signal)
    return resampled


def trim_silence(signal: Signal, threshold: float = 0.002) -> Signal:
    loud = np.flatnonzero(np.abs(signal) > threshold)
    if loud.size == 0:
        return signal
    start = max(int(loud[0]) - int(0.004 * SAMPLE_RATE), 0)
    end = min(int(loud[-1]) + int(0.05 * SAMPLE_RATE), signal.size)
    return signal[start:end]


def curate_sample(
    signal: Signal,
    source_rate: int,
    *,
    max_seconds: float,
    offset_seconds: float = 0.0,
) -> Signal:
    mono = resample(signal, source_rate)
    start = int(offset_seconds * SAMPLE_RATE)
    mono = mono[start:] if start > 0 else trim_silence(mono)
    limit = int(max_seconds * SAMPLE_RATE)
    if mono.size > limit:
        mono = mono[:limit]
    return normalize(apply_fades(mono, 0.004, 0.08))


def sha256_file(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        while True:
            block = handle.read(READ_BLOCK)
            if not block:
                break
            digest.update(block)
    return digest.hexdigest()


def _entry_from_json(payload: object) -> MaterialEntry:
    if not isinstance(payload, dict):
        raise MaterialError("manifest entry is not an object")
    try:
        return MaterialEntry(
            file=str(payload["file"]),
            instrument=str(payload["instrument"]),
            note=int(payload["note"]),
            layer=str(payload["layer"]),
            seconds=float(payload["seconds"]),
            sha256=str(payload["sha256"]),
            origin=str(payload["origin"]),
            license=str(payload["license"]),
            source=str(payload["source"]),
        )
    except (KeyError, TypeError, ValueError) as error:
        raise MaterialError(f"malformed manifest entry: {error}") from error


def manifest_path(destination: Path) -> Path:
    return destination / MANIFEST_FILENAME


def write_manifest(destination: Path, manifest: Manifest) -> None:
    destination.mkdir(parents=True, exist_ok=True)
    payload = json.dumps(manifest.as_json(), ensure_ascii=False, indent=2)
    manifest_path(destination).write_text(payload + "\n", "utf-8")


def read_manifest(destination: Path) -> Manifest:
    path = manifest_path(destination)
    if not path.is_file():
        raise MaterialError(f"no manifest at {path}")
    try:
        payload: object = json.loads(path.read_text("utf-8"))
    except (OSError, json.JSONDecodeError) as error:
        raise MaterialError(f"unreadable manifest: {error}") from error
    if not isinstance(payload, dict):
        raise MaterialError("manifest is not an object")
    entries = payload.get("entries")
    impulses = payload.get("impulses")
    if not isinstance(entries, list) or not isinstance(impulses, list):
        raise MaterialError("manifest is missing its entry lists")
    origin = str(payload.get("origin", ""))
    if origin not in ORIGINS:
        raise MaterialError(f"unknown material origin: {origin!r}")
    return Manifest(
        material_version=str(payload.get("material_version", "")),
        origin=origin,
        sample_rate=int(payload.get("sample_rate", SAMPLE_RATE)),
        entries=tuple(_entry_from_json(item) for item in entries),
        impulses=tuple(_entry_from_json(item) for item in impulses),
    )


def validate_material(destination: Path) -> tuple[str, ...]:
    try:
        manifest = read_manifest(destination)
    except MaterialError as error:
        return (str(error),)
    problems: list[str] = []
    if manifest.sample_rate != SAMPLE_RATE:
        problems.append(f"sample rate is {manifest.sample_rate}, expected {SAMPLE_RATE}")
    if not manifest.entries:
        problems.append("manifest declares no material")
    if not manifest.impulses:
        problems.append("manifest declares no impulse response")
    for entry in (*manifest.entries, *manifest.impulses):
        path = destination / entry.file
        if not path.is_file():
            problems.append(f"missing file: {entry.file}")
            continue
        if sha256_file(path) != entry.sha256:
            problems.append(f"hash mismatch: {entry.file}")
    return tuple(problems)
