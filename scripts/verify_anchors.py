from __future__ import annotations

import argparse
import hashlib
import json
import shutil
import subprocess
import sys
import tempfile
import zipfile
from collections.abc import Sequence
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from arca.formats import FIXED_LAYOUT, open_document

SAMPLE_SUFFIXES = (".epub", ".fb2", ".txt")
FILLER = "filler words that the layout engine has to spread across pages "
SECTIONS = 12


def build_samples(directory: Path) -> list[Path]:
    directory.mkdir(parents=True, exist_ok=True)
    return [_build_epub(directory), _build_fb2(directory), _build_txt(directory)]


def _build_epub(directory: Path) -> Path:
    path = directory / "sample.epub"
    body = "".join(
        f"<h2>Section {index} of the epub</h2><p>Paragraph {index}. {FILLER * 6}</p>"
        for index in range(1, SECTIONS + 1)
    )
    with zipfile.ZipFile(path, "w") as archive:
        archive.writestr("mimetype", "application/epub+zip", compress_type=zipfile.ZIP_STORED)
        archive.writestr("META-INF/container.xml", CONTAINER_XML)
        archive.writestr("OEBPS/content.opf", CONTENT_OPF)
        archive.writestr("OEBPS/text.xhtml", f"<html><body>{body}</body></html>")
    return path


def _build_fb2(directory: Path) -> Path:
    path = directory / "sample.fb2"
    body = "".join(
        f"<section><title><p>Section {index} of the fb2</p></title>"
        f"<p>Paragraph {index}. {FILLER * 6}</p></section>"
        for index in range(1, SECTIONS + 1)
    )
    path.write_text(FB2_TEMPLATE.format(body=body), "utf-8")
    return path


def _build_txt(directory: Path) -> Path:
    path = directory / "sample.txt"
    paragraphs = [f"Paragraph {index} of the txt. {FILLER * 6}" for index in range(1, SECTIONS + 1)]
    path.write_text("\n\n".join(paragraphs) + "\n", "utf-8")
    return path


def anchors_of(path: Path) -> list[tuple[str, str]]:
    document = open_document(path)
    found: list[tuple[str, str]] = []
    try:
        for index in range(document.page_count):
            page_number = index + 1
            position = 0
            for block in document[index].get_text("blocks", sort=True):
                if len(block) > 6 and int(block[6]) != 0:
                    continue
                text = " ".join(line.strip() for line in str(block[4]).splitlines() if line.strip())
                if not text:
                    continue
                position += 1
                found.append((f"p:{page_number:04d}:{position:02d}", text))
    finally:
        document.close()
    return found


def digest_of(entries: Sequence[tuple[str, str]]) -> str:
    payload = json.dumps(entries, ensure_ascii=False, sort_keys=True).encode("utf-8")
    return hashlib.sha256(payload).hexdigest()


def report(path: Path) -> dict[str, object]:
    entries = anchors_of(path)
    return {
        "name": path.name,
        "blocks": len(entries),
        "pages": len({anchor.split(":")[1] for anchor, _text in entries}),
        "digest": digest_of(entries),
    }


def _second_pass(path: Path, workdir: Path) -> dict[str, object]:
    copy = workdir / f"second-{path.name}"
    shutil.copyfile(path, copy)
    completed = subprocess.run(
        [sys.executable, str(Path(__file__).resolve()), "--report", str(copy)],
        capture_output=True,
        text=True,
    )
    if completed.returncode != 0:
        return {"name": path.name, "blocks": 0, "pages": 0, "digest": completed.stderr.strip()}
    parsed: dict[str, object] = json.loads(completed.stdout)
    parsed["name"] = path.name
    return parsed


def run(samples: Sequence[Path], workdir: Path) -> int:
    print(f"[anchors] layout {FIXED_LAYOUT.signature}")
    failures = 0
    for path in samples:
        first = report(path)
        second = _second_pass(path, workdir)
        same = first == second
        failures += 0 if same else 1
        verdict = "same" if same else "DIFFERENT"
        print(
            f"[anchors] {path.name}: {first['blocks']} blocks on {first['pages']} pages "
            f"-> {verdict}"
        )
        print(f"[anchors]   pass 1 {first['digest']}")
        print(f"[anchors]   pass 2 {second['digest']}")
    print("[anchors] deterministic" if not failures else "[anchors] NOT deterministic")
    return 0 if not failures else 1


def main(argv: Sequence[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="anchor determinism gate for reflowable formats")
    parser.add_argument("--report", type=Path, default=None)
    parser.add_argument("--samples", type=Path, default=None)
    arguments = parser.parse_args(argv)
    if arguments.report is not None:
        print(json.dumps(report(arguments.report), ensure_ascii=False))
        return 0
    with tempfile.TemporaryDirectory(prefix="arca-anchors-") as temporary:
        workdir = Path(temporary)
        if arguments.samples is None:
            samples = build_samples(workdir / "samples")
        else:
            samples = sorted(
                path
                for path in arguments.samples.iterdir()
                if path.suffix.lower() in SAMPLE_SUFFIXES
            )
        if not samples:
            print("[anchors] no samples found")
            return 1
        return run(samples, workdir)


CONTAINER_XML = (
    '<?xml version="1.0"?><container version="1.0" '
    'xmlns="urn:oasis:names:tc:opendocument:xmlns:container"><rootfiles>'
    '<rootfile full-path="OEBPS/content.opf" '
    'media-type="application/oebps-package+xml"/></rootfiles></container>'
)

CONTENT_OPF = (
    '<?xml version="1.0"?><package xmlns="http://www.idpf.org/2007/opf" version="3.0" '
    'unique-identifier="id"><metadata xmlns:dc="http://purl.org/dc/elements/1.1/">'
    '<dc:identifier id="id">urn:uuid:arca-sample</dc:identifier>'
    "<dc:title>Sample book</dc:title><dc:language>en</dc:language></metadata>"
    '<manifest><item id="c1" href="text.xhtml" media-type="application/xhtml+xml"/></manifest>'
    '<spine><itemref idref="c1"/></spine></package>'
)

FB2_TEMPLATE = (
    '<?xml version="1.0" encoding="utf-8"?>'
    '<FictionBook xmlns="http://www.gribuser.ru/xml/fictionbook/2.0"><description>'
    "<title-info><book-title>Sample FB2</book-title></title-info></description>"
    "<body>{body}</body></FictionBook>"
)


if __name__ == "__main__":
    raise SystemExit(main())
