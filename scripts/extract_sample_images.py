#!/usr/bin/env python3
"""Extract the sample photographs used by the screenshots, and record where they came from.

The frames in the README are **real photographs**, not renders. That matters: an annotation
tool demonstrated on flat vector shapes tells a reader nothing about whether it works on
the images they actually have.

Every image here is public domain or CC0, taken from
[scikit-image](https://github.com/scikit-image/scikit-image)'s bundled sample data, whose
provenance is documented per-image in that project. `docs/images/samples/CREDITS.md`
carries the attribution that travels with them.

This is a **one-time extraction**, not part of any build. scikit-image (and NumPy, and
SciPy) are not dependencies of CurveVision — they are how these four JPEGs were produced,
and the JPEGs are what is committed:

    pip install scikit-image
    python scripts/extract_sample_images.py
"""

from __future__ import annotations

import sys
from pathlib import Path

OUT = Path(__file__).resolve().parents[1] / "docs" / "images" / "samples"

#: name -> (skimage.data function, licence, credit, what it is good for demonstrating)
SAMPLES = {
    "coffee": (
        "coffee",
        "CC0",
        "Rachel Michetti, courtesy of Pikolo Espresso Bar",
        ("Everyday objects on a table — cups, saucers, spoons. The closest thing here to "
         "the COCO-style detection work most people point an annotation tool at."),
    ),
    "astronaut": (
        "astronaut",
        "Public domain (no known copyright restrictions)",
        "NASA — astronaut Eileen Collins, from the NASA Great Images database",
        "A person, with distinct sub-parts (helmet, badge, flag) worth separate labels.",
    ),
    "rocket": (
        "rocket",
        "Public domain",
        "SpaceX — Falcon 9 carrying DSCOVR, Cape Canaveral",
        "A large object against sky, plus launch structures at different scales.",
    ),
    "cat": (
        "chelsea",
        "CC0",
        "Stefan van der Walt",
        "Fur and whiskers: fine texture and soft edges, where a lazy box is obvious.",
    ),
}


def main() -> int:
    try:
        from skimage import data
    except ImportError:
        print(
            "This needs scikit-image, which is NOT a CurveVision dependency:\n"
            "    pip install scikit-image\n"
            "The committed JPEGs in docs/images/samples/ are the output; you only need\n"
            "this script to regenerate them.",
            file=sys.stderr,
        )
        return 1

    from PIL import Image

    OUT.mkdir(parents=True, exist_ok=True)
    rows = []
    for name, (source, licence, credit, purpose) in SAMPLES.items():
        array = getattr(data, source)()
        image = Image.fromarray(array).convert("RGB")
        path = OUT / f"{name}.jpg"
        image.save(path, format="JPEG", quality=92, optimize=True)
        rows.append((name, image.size, licence, credit, purpose))
        print(f"  {path.relative_to(OUT.parents[2])}  {image.size}  {path.stat().st_size // 1024} KB")

    credits = [
        "# Sample photographs",
        "",
        "These are **real photographs**, used by `scripts/screenshot.py` to produce the",
        "screenshots in the README. Every one is public domain or CC0, and every one keeps",
        "its attribution here.",
        "",
        "They were extracted from [scikit-image](https://github.com/scikit-image/scikit-image)'s",
        "bundled sample data by `scripts/extract_sample_images.py`. scikit-image is **not** a",
        "CurveVision dependency — it was the source of these four files, and the files are what",
        "is committed.",
        "",
        "| File | Size | Licence | Credit |",
        "| --- | --- | --- | --- |",
    ]
    for name, size, licence, credit, _ in rows:
        credits.append(f"| `{name}.jpg` | {size[0]}×{size[1]} | {licence} | {credit} |")
    credits += [
        "",
        "## Why these",
        "",
    ]
    for name, _, _, _, purpose in rows:
        credits.append(f"* **`{name}.jpg`** — {purpose}")
    credits += [
        "",
        "Photographs rather than renders, deliberately: a tool demonstrated on flat vector",
        "shapes tells a reader nothing about whether it copes with the images they have.",
        "Public domain and CC0 rather than anything else, equally deliberately: a repository",
        "should not carry somebody's copyright.",
        "",
        "`astronaut.jpg` shows an identifiable person. It is an official NASA portrait,",
        "released by NASA without copyright restriction, and it is one of the most widely",
        "reproduced test images in computer vision. That settles the licence; it is not a",
        "model release, so it is used here only as a frame in a sample dataset, and it is not",
        "the image on the front page.",
        "",
    ]
    (OUT / "CREDITS.md").write_text("\n".join(credits))
    print(f"  {OUT.relative_to(OUT.parents[2])}/CREDITS.md")
    return 0


if __name__ == "__main__":
    sys.exit(main())
