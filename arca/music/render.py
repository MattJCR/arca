from __future__ import annotations

from collections.abc import Callable
from dataclasses import dataclass
from pathlib import Path

import numpy as np

from arca.music.material import (
    IMPULSE_DIRNAME,
    MATERIAL_VERSION,
    ORIGIN_RENDERED,
    SAMPLE_RATE,
    Manifest,
    MaterialEntry,
    MaterialError,
    Signal,
    apply_fades,
    band_noise,
    material_filename,
    midi_to_hz,
    normalize,
    read_manifest,
    seeded_generator,
    sha256_file,
    write_manifest,
    write_wav,
)

RENDER_VERSION = 1
RENDER_SOURCE = f"arca.music.render v{RENDER_VERSION}"
RENDERED_LICENSE = "none (synthesised by ARCA)"
IMPULSE_FILE = f"{IMPULSE_DIRNAME}/hall.wav"
IMPULSE_SECONDS = 2.6
NYQUIST_MARGIN = 0.45
BELL_PARTIALS = (
    (0.63, 0.20),
    (1.23, 0.50),
    (2.00, 1.00),
    (2.95, 0.85),
    (4.05, 0.70),
    (5.35, 0.35),
    (6.75, 0.15),
    (8.20, 0.07),
)
STRING_PARTIALS = 26
STRING_INHARMONICITY = 0.00042
HAMMER_POSITION = 0.13
PAD_PARTIALS = 12
DRONE_PARTIALS = 16
IMPULSE_BANDS = ((60.0, 320.0, 0.95), (320.0, 1300.0, 0.78), (1300.0, 4200.0, 0.48))
EARLY_REFLECTIONS = ((0.011, 0.52), (0.017, -0.39), (0.023, 0.31), (0.031, -0.24), (0.043, 0.18))

ACTION_RENDERED = "rendered"
ACTION_KEPT = "kept"

Progress = Callable[[str, str], None]


@dataclass(frozen=True)
class RenderSpec:
    instrument: str
    note: int
    layer: str
    seconds: float


RENDER_PLAN: tuple[RenderSpec, ...] = (
    RenderSpec("drone", 45, "sustain", 16.0),
    RenderSpec("pad", 57, "sustain", 13.0),
    RenderSpec("pad", 62, "sustain", 13.0),
    RenderSpec("pad", 67, "sustain", 13.0),
    RenderSpec("pad", 72, "sustain", 13.0),
    RenderSpec("pad", 76, "sustain", 13.0),
    RenderSpec("keys", 40, "mf", 8.0),
    RenderSpec("keys", 45, "mf", 8.0),
    RenderSpec("keys", 50, "mf", 7.0),
    RenderSpec("keys", 55, "mf", 7.0),
    RenderSpec("keys", 60, "mf", 7.0),
    RenderSpec("keys", 65, "mf", 6.0),
    RenderSpec("keys", 70, "mf", 6.0),
    RenderSpec("bell", 60, "mf", 9.0),
    RenderSpec("bell", 64, "mf", 9.0),
    RenderSpec("bell", 68, "mf", 9.0),
    RenderSpec("bell", 72, "mf", 9.0),
    RenderSpec("bell", 76, "mf", 9.0),
)


def _timeline(seconds: float) -> Signal:
    frames = max(int(seconds * SAMPLE_RATE), 1)
    return np.arange(frames, dtype=np.float64) / SAMPLE_RATE


def _audible(frequency: float) -> bool:
    return 0.0 < frequency < NYQUIST_MARGIN * SAMPLE_RATE


def modal_bell(note: int, seconds: float, generator: np.random.Generator) -> Signal:
    time = _timeline(seconds)
    fundamental = midi_to_hz(note)
    voice = np.zeros_like(time)
    for ratio, amplitude in BELL_PARTIALS:
        frequency = fundamental * ratio
        if not _audible(frequency):
            continue
        decay = seconds * 0.9 / (0.6 + 0.5 * ratio)
        beating = 0.11 + 0.34 * float(generator.random())
        phase = 2.0 * np.pi * float(generator.random())
        envelope = np.exp(-time / decay)
        partial = np.sin(2.0 * np.pi * frequency * time + phase)
        shifted = np.sin(2.0 * np.pi * (frequency + beating) * time + phase * 0.5)
        voice += amplitude * envelope * (partial + 0.68 * shifted)
    strike = band_noise(time.size, 900.0, 8500.0, generator) * np.exp(-time / 0.011)
    voice = voice + 0.32 * normalize(strike, 1.0)
    return normalize(apply_fades(voice, 0.0015, min(0.3, seconds * 0.2)))


def struck_string(note: int, seconds: float, generator: np.random.Generator) -> Signal:
    time = _timeline(seconds)
    fundamental = midi_to_hz(note)
    voice = np.zeros_like(time)
    for partial in range(1, STRING_PARTIALS + 1):
        frequency = (
            fundamental * partial * float(np.sqrt(1.0 + STRING_INHARMONICITY * partial * partial))
        )
        if not _audible(frequency):
            break
        hammer = abs(float(np.sin(np.pi * partial * HAMMER_POSITION)))
        amplitude = hammer / partial**1.05
        decay = seconds * 0.8 / (1.0 + 0.3 * (partial - 1))
        phase = 2.0 * np.pi * float(generator.random())
        voice += amplitude * np.exp(-time / decay) * np.sin(2.0 * np.pi * frequency * time + phase)
    knock = band_noise(time.size, 140.0, 3600.0, generator) * np.exp(-time / 0.007)
    voice = voice + 0.22 * normalize(knock, 1.0)
    return normalize(apply_fades(voice, 0.001, min(0.35, seconds * 0.2)))


def sustained_pad(note: int, seconds: float, generator: np.random.Generator) -> Signal:
    time = _timeline(seconds)
    fundamental = midi_to_hz(note)
    voice = np.zeros_like(time)
    for partial in range(1, PAD_PARTIALS + 1):
        frequency = fundamental * partial * (1.0 + 0.0004 * partial * partial)
        if not _audible(frequency):
            break
        amplitude = 1.0 / partial**1.45
        rate = 0.03 + 0.21 * float(generator.random())
        swell = 1.0 + 0.38 * np.sin(2.0 * np.pi * rate * time + 2.0 * np.pi * generator.random())
        detune = 1.0 + (float(generator.random()) - 0.5) * 0.0018
        phase = 2.0 * np.pi * float(generator.random())
        voice += amplitude * swell * np.sin(2.0 * np.pi * frequency * detune * time + phase)
    air = band_noise(time.size, fundamental * 1.6, fundamental * 7.0, generator)
    voice = normalize(voice, 1.0) + 0.16 * normalize(air, 1.0)
    return normalize(apply_fades(voice, 0.35, 0.6))


def sustained_drone(note: int, seconds: float, generator: np.random.Generator) -> Signal:
    time = _timeline(seconds)
    fundamental = midi_to_hz(note)
    voice = np.zeros_like(time)
    for partial in range(1, DRONE_PARTIALS + 1):
        frequency = fundamental * partial
        if not _audible(frequency):
            break
        amplitude = 1.0 / partial**1.75
        rate = 0.017 + 0.09 * float(generator.random())
        swell = 1.0 + 0.45 * np.sin(2.0 * np.pi * rate * time + 2.0 * np.pi * generator.random())
        detune = 1.0 + (float(generator.random()) - 0.5) * 0.0026
        phase = 2.0 * np.pi * float(generator.random())
        voice += amplitude * swell * np.sin(2.0 * np.pi * frequency * detune * time + phase)
    body = band_noise(time.size, fundamental * 0.5, fundamental * 3.0, generator)
    sweep = 0.5 + 0.5 * np.sin(2.0 * np.pi * 0.011 * time)
    voice = normalize(voice, 1.0) + 0.24 * normalize(body, 1.0) * sweep
    return normalize(apply_fades(voice, 0.9, 1.2))


def impulse_response(seconds: float, generator: np.random.Generator) -> Signal:
    time = _timeline(seconds)
    tail = np.zeros_like(time)
    for low, high, share in IMPULSE_BANDS:
        decay = seconds * share / 3.0
        tail += band_noise(time.size, low, high, generator) * np.exp(-time / decay)
    tail *= 1.0 - np.exp(-time / 0.006)
    for delay, gain in EARLY_REFLECTIONS:
        index = int(delay * SAMPLE_RATE)
        if index < tail.size:
            tail[index] += gain
    tail[0] += 1.0
    return normalize(apply_fades(tail, 0.0, 0.15))


def render_spec(spec: RenderSpec, seconds: float, generator: np.random.Generator) -> Signal:
    if spec.instrument == "bell":
        return modal_bell(spec.note, seconds, generator)
    if spec.instrument == "keys":
        return struck_string(spec.note, seconds, generator)
    if spec.instrument == "pad":
        return sustained_pad(spec.note, seconds, generator)
    if spec.instrument == "drone":
        return sustained_drone(spec.note, seconds, generator)
    raise MaterialError(f"no renderer for instrument {spec.instrument!r}")


def _entry(name: str, spec: RenderSpec, seconds: float, digest: str) -> MaterialEntry:
    return MaterialEntry(
        file=name,
        instrument=spec.instrument,
        note=spec.note,
        layer=spec.layer,
        seconds=seconds,
        sha256=digest,
        origin=ORIGIN_RENDERED,
        license=RENDERED_LICENSE,
        source=RENDER_SOURCE,
    )


def _known_entries(destination: Path) -> dict[str, MaterialEntry]:
    try:
        return read_manifest(destination).by_file()
    except MaterialError:
        return {}


def _reusable(
    destination: Path, name: str, known: dict[str, MaterialEntry]
) -> MaterialEntry | None:
    entry = known.get(name)
    path = destination / name
    if entry is None or not path.is_file():
        return None
    if sha256_file(path) != entry.sha256:
        return None
    return entry


def render_material(
    destination: Path,
    *,
    seconds_scale: float = 1.0,
    progress: Progress | None = None,
) -> Manifest:
    destination.mkdir(parents=True, exist_ok=True)
    known = _known_entries(destination)
    entries: list[MaterialEntry] = []
    impulses: list[MaterialEntry] = []

    def snapshot() -> Manifest:
        return Manifest(
            material_version=MATERIAL_VERSION,
            origin=ORIGIN_RENDERED,
            sample_rate=SAMPLE_RATE,
            entries=tuple(entries),
            impulses=tuple(impulses),
        )

    for spec in RENDER_PLAN:
        name = material_filename(spec.instrument, spec.note, spec.layer)
        seconds = spec.seconds * seconds_scale
        reused = _reusable(destination, name, known)
        if reused is not None and abs(reused.seconds - seconds) < 1e-6:
            entries.append(reused)
            if progress is not None:
                progress(name, ACTION_KEPT)
            continue
        voice = render_spec(spec, seconds, seeded_generator(name))
        write_wav(destination / name, voice)
        entries.append(_entry(name, spec, seconds, sha256_file(destination / name)))
        write_manifest(destination, snapshot())
        if progress is not None:
            progress(name, ACTION_RENDERED)

    impulse_spec = RenderSpec("impulse", 0, "hall", IMPULSE_SECONDS)
    impulse_seconds = IMPULSE_SECONDS * seconds_scale
    reused_impulse = _reusable(destination, IMPULSE_FILE, known)
    if reused_impulse is not None and abs(reused_impulse.seconds - impulse_seconds) < 1e-6:
        impulses.append(reused_impulse)
        if progress is not None:
            progress(IMPULSE_FILE, ACTION_KEPT)
    else:
        tail = impulse_response(impulse_seconds, seeded_generator(IMPULSE_FILE))
        write_wav(destination / IMPULSE_FILE, tail)
        digest = sha256_file(destination / IMPULSE_FILE)
        impulses.append(_entry(IMPULSE_FILE, impulse_spec, impulse_seconds, digest))
        if progress is not None:
            progress(IMPULSE_FILE, ACTION_RENDERED)

    manifest = snapshot()
    write_manifest(destination, manifest)
    return manifest
