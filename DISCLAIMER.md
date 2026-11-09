# Disclaimer

CurveVision is free, open-source software, released under the [MIT License](LICENSE). This
page says in plain words what that licence already says, and what using the project involves.

## No warranty

CurveVision is provided **"as is", without warranty of any kind**, express or implied. The
authors and contributors are not liable for any claim, damage or loss arising from its use,
including lost or corrupted annotations, datasets or images. Keep backups of your data, and
check an exported dataset before you train on it or rely on it.

## Your data and your responsibilities

* You are responsible for having the right to the images and video you annotate, and for
  complying with the laws that apply to them, such as data-protection law when they show
  people.
* **The desktop application** works on your own computer. It has no analytics and no
  telemetry. It sends data elsewhere only when you tell it to: when you connect it to a team
  server, add a hosted model, or when it checks GitHub for a newer version (a release built
  with an update key does this when it starts).
* **A team server** runs on infrastructure you control. Whoever operates it is responsible for
  securing it (HTTPS, backups, access), as described in `docs/OPERATIONS.md` and
  `docs/SECURITY.md`.

## Models and third-party services

CurveVision does not ship a model. Models and services you connect are governed by their own
licences and terms of use, which you accept with their providers:

* Images you send to a **hosted model** (for example Anthropic's Claude, OpenAI or Google
  Gemini) are processed by that provider under its terms.
* **Model weights** you run yourself carry their own licences. For example, Ultralytics YOLO
  weights are AGPL-3.0; the project's demo script downloads them at run time and does not
  redistribute them.
* A model's suggestions can be wrong. They arrive as suggestions for a person to review.

## Trademarks and affiliation

CurveVision is an independent project. It is not affiliated with, endorsed by or sponsored by
CVAT, Ultralytics, Anthropic, OpenAI, Google, Microsoft, Apple, the COCO Consortium, Labelme,
Cityscapes, TensorFlow or any other project or company it names. Those names are used only to
describe compatibility, and they are trademarks of their respective owners.

## Third-party code and content

Third-party software is used under its own licence; see `docs/THIRD_PARTY_NOTICES.md`. The
sample photographs are public domain or CC0 (`docs/images/samples/CREDITS.md`). The website's
fonts are under the SIL Open Font License (`site/fonts/LICENSE.md`).

## Security

Report a vulnerability privately as `SECURITY.md` describes, not in a public issue.
