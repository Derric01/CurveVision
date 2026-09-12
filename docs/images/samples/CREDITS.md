# Sample photographs

These are **real photographs**, used by `scripts/screenshot.py` to produce the
screenshots in the README. Every one is public domain or CC0, and every one keeps
its attribution here.

They were extracted from [scikit-image](https://github.com/scikit-image/scikit-image)'s
bundled sample data by `scripts/extract_sample_images.py`. scikit-image is **not** a
CurveVision dependency — it was the source of these four files, and the files are what
is committed.

| File | Size | Licence | Credit |
| --- | --- | --- | --- |
| `coffee.jpg` | 600×400 | CC0 | Rachel Michetti, courtesy of Pikolo Espresso Bar |
| `astronaut.jpg` | 512×512 | Public domain (no known copyright restrictions) | NASA — astronaut Eileen Collins, from the NASA Great Images database |
| `rocket.jpg` | 640×427 | Public domain | SpaceX — Falcon 9 carrying DSCOVR, Cape Canaveral |
| `cat.jpg` | 451×300 | CC0 | Stefan van der Walt |

## Why these

* **`coffee.jpg`** — Everyday objects on a table — cups, saucers, spoons. The closest thing here to the COCO-style detection work most people point an annotation tool at.
* **`astronaut.jpg`** — A person, with distinct sub-parts (helmet, badge, flag) worth separate labels.
* **`rocket.jpg`** — A large object against sky, plus launch structures at different scales.
* **`cat.jpg`** — Fur and whiskers: fine texture and soft edges, where a lazy box is obvious.

Photographs rather than renders, deliberately: a tool demonstrated on flat vector
shapes tells a reader nothing about whether it copes with the images they have.
Public domain and CC0 rather than anything else, equally deliberately: a repository
should not carry somebody's copyright.

`astronaut.jpg` shows an identifiable person. It is an official NASA portrait,
released by NASA without copyright restriction, and it is one of the most widely
reproduced test images in computer vision. That settles the licence; it is not a
model release, so it is used here only as a frame in a sample dataset, and it is not
the image on the front page.
