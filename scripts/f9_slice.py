from __future__ import annotations

import argparse
import json
import shutil
import sys
import urllib.error
import urllib.parse
import urllib.request
from collections.abc import Sequence
from dataclasses import dataclass
from functools import partial
from http.server import SimpleHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path

import numpy as np

from arca.music.material import (
    LICENSE_FILENAME,
    MATERIAL_VERSION,
    ORIGIN_SAMPLED,
    SAMPLE_RATE,
    Manifest,
    MaterialEntry,
    MaterialError,
    Signal,
    curate_sample,
    material_filename,
    midi_to_hz,
    read_manifest,
    read_wav_mono,
    seeded_generator,
    sha256_file,
    validate_material,
    write_manifest,
    write_wav,
)
from arca.music.render import (
    ACTION_KEPT,
    ACTION_RENDERED,
    IMPULSE_FILE,
    IMPULSE_SECONDS,
    RENDER_SOURCE,
    impulse_response,
    render_material,
)

SLICE_DIR = Path(__file__).resolve().parent.parent / "arca" / "music" / "slice"
RENDERED_DIRNAME = "synth"
SAMPLED_DIRNAME = "sampled"
CACHE_DIRNAME = ".cache"
SOURCES_FILENAME = "SOURCES.json"
PAGE_FILES = ("index.html", "engine.js")

VCSL_COMMIT = "c1ea7bcc3c7309650ab0da9d15c9cd1fbc4a4c7e"
VCSL_BASE = f"https://raw.githubusercontent.com/sgossner/VCSL/{VCSL_COMMIT}/"
VCSL_LICENSE = "CC0-1.0"
VCSL_CREDIT = "Versilian Community Sample Library (sgossner/VCSL), CC0 1.0 Universal"
USER_AGENT = "arca-f9-slice"
PARTIALS = (0.63, 1.0, 1.23, 2.0, 2.95, 3.0, 4.0, 4.05, 5.0, 5.35)
PARTIAL_TOLERANCE = 0.04
DOWNLOAD_TIMEOUT = 120
DOWNLOAD_BLOCK = 1 << 16

VIBES = "Idiophones/Struck Idiophones/Vibraphone/Bowed/"
CHIMES = "Idiophones/Struck Idiophones/Tubular Bells 1/"
PIANO = "Chordophones/Zithers/Grand Piano, Steinway B/Sus/"


@dataclass(frozen=True)
class SamplePick:
    instrument: str
    note: int
    layer: str
    remote: str
    max_seconds: float
    offset_seconds: float = 0.0


SAMPLE_PLAN: tuple[SamplePick, ...] = (
    SamplePick("drone", 57, "sustain", f"{VIBES}Vibes_bowed_A2_rr1_Main.wav", 9.0, 2.0),
    SamplePick("pad", 57, "sustain", f"{VIBES}Vibes_bowed_A2_rr1_Main.wav", 13.0),
    SamplePick("pad", 64, "sustain", f"{VIBES}Vibes_bowed_E3_rr1_Main.wav", 13.0),
    SamplePick("pad", 67, "sustain", f"{VIBES}Vibes_bowed_G3_rr1_Main.wav", 13.0),
    SamplePick("pad", 74, "sustain", f"{VIBES}Vibes_bowed_D4_rr1_Main.wav", 13.0),
    SamplePick("pad", 81, "sustain", f"{VIBES}Vibes_bowed_A4_rr1_Main.wav", 13.0),
    SamplePick("pad", 88, "sustain", f"{VIBES}Vibes_bowed_E5_rr1_Main.wav", 13.0),
    SamplePick("keys", 36, "mf", f"{PIANO}JHPiano_Sus_Close_C2_vl2_rr1.wav", 8.0),
    SamplePick("keys", 42, "mf", f"{PIANO}JHPiano_Sus_Close_F#2_vl2_rr1.wav", 8.0),
    SamplePick("keys", 46, "mf", f"{PIANO}JHPiano_Sus_Close_A#2_vl2_rr1.wav", 8.0),
    SamplePick("keys", 50, "mf", f"{PIANO}JHPiano_Sus_Close_D3_vl2_rr1.wav", 8.0),
    SamplePick("keys", 56, "mf", f"{PIANO}JHPiano_Sus_Close_G#3_vl2_rr1.wav", 7.0),
    SamplePick("keys", 60, "mf", f"{PIANO}JHPiano_Sus_Close_C4_vl2_rr1.wav", 7.0),
    SamplePick("keys", 66, "mf", f"{PIANO}JHPiano_Sus_Close_F#4_vl2_rr1.wav", 6.0),
    SamplePick("keys", 70, "mf", f"{PIANO}JHPiano_Sus_Close_A#4_vl2_rr1.wav", 6.0),
    SamplePick("bell", 60, "mf", f"{CHIMES}chimes_C3_pp_rr2.wav", 9.0),
    SamplePick("bell", 64, "mf", f"{CHIMES}chimes_E3_pp_rr1.wav", 9.0),
    SamplePick("bell", 68, "mf", f"{CHIMES}chimes_G#3_p_rr2.wav", 9.0),
    SamplePick("bell", 72, "mf", f"{CHIMES}chimes_C4_p_rr1.wav", 9.0),
    SamplePick("bell", 76, "mf", f"{CHIMES}chimes_E4_pp_rr3.wav", 9.0),
)


def cache_name(remote: str) -> str:
    return remote.replace("/", "__").replace(" ", "_").replace("#", "sharp")


def download(remote: str, target: Path) -> None:
    target.parent.mkdir(parents=True, exist_ok=True)
    url = VCSL_BASE + urllib.parse.quote(remote)
    request = urllib.request.Request(url, headers={"User-Agent": USER_AGENT})
    partial_target = target.with_suffix(target.suffix + ".part")
    with (
        urllib.request.urlopen(request, timeout=DOWNLOAD_TIMEOUT) as response,
        partial_target.open("wb") as handle,
    ):
        while True:
            block = response.read(DOWNLOAD_BLOCK)
            if not block:
                break
            handle.write(block)
    partial_target.replace(target)


def dominant_partial(signal: Signal) -> float:
    window = signal[: SAMPLE_RATE * 3]
    if window.size < 1024:
        return 0.0
    spectrum = np.abs(np.fft.rfft(window * np.hanning(window.size)))
    freqs = np.fft.rfftfreq(window.size, 1.0 / SAMPLE_RATE)
    usable = (freqs > 40.0) & (freqs < 6000.0)
    if not usable.any():
        return 0.0
    return float(freqs[usable][int(np.argmax(spectrum[usable]))])


def known_partial(ratio: float) -> bool:
    return any(abs(ratio - expected) < expected * PARTIAL_TOLERANCE for expected in PARTIALS)


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


def fetch_material(destination: Path, *, keep_cache: bool) -> Manifest:
    destination.mkdir(parents=True, exist_ok=True)
    cache = destination.parent / CACHE_DIRNAME
    try:
        known = read_manifest(destination).by_file()
    except MaterialError:
        known = {}
    entries: list[MaterialEntry] = []
    sources: list[dict[str, object]] = []

    for pick in SAMPLE_PLAN:
        name = material_filename(pick.instrument, pick.note, pick.layer)
        reused = _reusable(destination, name, known)
        if reused is not None:
            entries.append(reused)
            print(f"[{ACTION_KEPT}] {name}")
            continue
        raw = cache / cache_name(pick.remote)
        if not raw.is_file():
            print(f"[download] {pick.remote}")
            download(pick.remote, raw)
        signal, rate = read_wav_mono(raw)
        curated = curate_sample(
            signal,
            rate,
            max_seconds=pick.max_seconds,
            offset_seconds=pick.offset_seconds,
        )
        write_wav(destination / name, curated)
        entries.append(
            MaterialEntry(
                file=name,
                instrument=pick.instrument,
                note=pick.note,
                layer=pick.layer,
                seconds=curated.size / SAMPLE_RATE,
                sha256=sha256_file(destination / name),
                origin=ORIGIN_SAMPLED,
                license=VCSL_LICENSE,
                source=VCSL_CREDIT,
            )
        )
        sources.append(
            {
                "file": name,
                "remote": pick.remote,
                "commit": VCSL_COMMIT,
                "original_sha256": sha256_file(raw),
                "license": VCSL_LICENSE,
                "credit": VCSL_CREDIT,
            }
        )
        print(f"[curated] {name}")

    impulses: list[MaterialEntry] = []
    reused_impulse = _reusable(destination, IMPULSE_FILE, known)
    if reused_impulse is not None:
        impulses.append(reused_impulse)
        print(f"[{ACTION_KEPT}] {IMPULSE_FILE}")
    else:
        tail = impulse_response(IMPULSE_SECONDS, seeded_generator(IMPULSE_FILE))
        write_wav(destination / IMPULSE_FILE, tail)
        impulses.append(
            MaterialEntry(
                file=IMPULSE_FILE,
                instrument="impulse",
                note=0,
                layer="hall",
                seconds=IMPULSE_SECONDS,
                sha256=sha256_file(destination / IMPULSE_FILE),
                origin=ORIGIN_SAMPLED,
                license="none (synthesised by ARCA)",
                source=RENDER_SOURCE,
            )
        )
        print(f"[{ACTION_RENDERED}] {IMPULSE_FILE}")

    manifest = Manifest(
        material_version=MATERIAL_VERSION,
        origin=ORIGIN_SAMPLED,
        sample_rate=SAMPLE_RATE,
        entries=tuple(entries),
        impulses=tuple(impulses),
    )
    write_manifest(destination, manifest)
    if sources:
        existing: list[dict[str, object]] = []
        sources_path = destination / SOURCES_FILENAME
        if sources_path.is_file():
            loaded: object = json.loads(sources_path.read_text("utf-8"))
            if isinstance(loaded, list):
                existing = [item for item in loaded if isinstance(item, dict)]
        merged = {str(item["file"]): item for item in (*existing, *sources)}
        payload = json.dumps(sorted(merged.values(), key=lambda item: str(item["file"])), indent=2)
        sources_path.write_text(payload + "\n", "utf-8")
    license_path = destination / LICENSE_FILENAME
    if not license_path.is_file():
        print("[download] LICENSE")
        download("LICENSE", license_path)
    if not keep_cache and cache.is_dir():
        shutil.rmtree(cache)
        print("[cache] removed")
    return manifest


def command_render(out: Path) -> int:
    manifest = render_material(out / RENDERED_DIRNAME, progress=lambda n, a: print(f"[{a}] {n}"))
    print(f"[render] {len(manifest.entries)} files in {out / RENDERED_DIRNAME}")
    return 0


def command_fetch(out: Path, *, keep_cache: bool) -> int:
    try:
        manifest = fetch_material(out / SAMPLED_DIRNAME, keep_cache=keep_cache)
    except (OSError, urllib.error.URLError, MaterialError) as error:
        print(f"[fetch] failed: {error}", file=sys.stderr)
        return 1
    print(f"[fetch] {len(manifest.entries)} files in {out / SAMPLED_DIRNAME}")
    return 0


def command_verify(out: Path) -> int:
    failed = False
    for name in (RENDERED_DIRNAME, SAMPLED_DIRNAME):
        destination = out / name
        if not destination.is_dir():
            print(f"[verify] {name}: absent")
            continue
        problems = validate_material(destination)
        if problems:
            failed = True
            for problem in problems:
                print(f"[verify] {name}: {problem}", file=sys.stderr)
            continue
        manifest = read_manifest(destination)
        print(f"[verify] {name}: {len(manifest.entries)} files, hashes match")
        for entry in manifest.entries:
            signal, _ = read_wav_mono(destination / entry.file)
            measured = dominant_partial(signal)
            ratio = measured / midi_to_hz(entry.note) if measured > 0.0 else 0.0
            if not known_partial(ratio):
                failed = True
                print(
                    f"[verify] {name}: {entry.file} declares {entry.note} "
                    f"({midi_to_hz(entry.note):.1f} Hz) but its loudest partial is "
                    f"{measured:.1f} Hz, {ratio:.2f} times the declared pitch",
                    file=sys.stderr,
                )
    return 1 if failed else 0


class SliceHandler(SimpleHTTPRequestHandler):
    def translate_path(self, path: str) -> str:
        clean = urllib.parse.urlparse(path).path.lstrip("/")
        if clean == "":
            clean = "index.html"
        if clean in PAGE_FILES:
            return str(SLICE_DIR / clean)
        return super().translate_path(path)

    def log_message(self, format: str, *args: object) -> None:
        return


def command_serve(out: Path, port: int) -> int:
    if not (out / RENDERED_DIRNAME).is_dir() and not (out / SAMPLED_DIRNAME).is_dir():
        print(f"[serve] no material under {out}; run render or fetch first", file=sys.stderr)
        return 1
    handler = partial(SliceHandler, directory=str(out))
    server = ThreadingHTTPServer(("127.0.0.1", port), handler)
    print(f"[serve] http://127.0.0.1:{port}/  (ctrl-c to stop)")
    try:
        server.serve_forever()
    except KeyboardInterrupt:
        print("\n[serve] stopped")
    finally:
        server.server_close()
    return 0


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description="F9.0 listening slice for ARCA Music")
    parser.add_argument("--out", required=True, type=Path, help="directory holding the material")
    subparsers = parser.add_subparsers(dest="command", required=True)
    subparsers.add_parser("render", help="synthesise the rendered material")
    fetch = subparsers.add_parser("fetch", help="download and curate the CC0 sampled material")
    fetch.add_argument("--keep-cache", action="store_true", help="keep the downloaded originals")
    subparsers.add_parser("verify", help="check hashes and declared pitches")
    serve = subparsers.add_parser("serve", help="serve the listening page")
    serve.add_argument("--port", type=int, default=8931)
    return parser


def main(argv: Sequence[str] | None = None) -> int:
    arguments = build_parser().parse_args(argv)
    out = Path(arguments.out)
    if arguments.command == "render":
        return command_render(out)
    if arguments.command == "fetch":
        return command_fetch(out, keep_cache=bool(arguments.keep_cache))
    if arguments.command == "verify":
        return command_verify(out)
    return command_serve(out, int(arguments.port))


if __name__ == "__main__":
    raise SystemExit(main())
