# Third-Party Notices

CurveVision is distributed under the [MIT License](../LICENSE). It depends on the
open-source software listed below, each under its own license and copyright.

This file is checked by CI (`scripts/check_notices.py`): a declared dependency with no
entry here fails the build. That is deliberate — software reaching users without
attribution is a licensing problem, not a documentation nit.

**One file in this repository is adapted from another project's source code**, listed
under [Adapted source](#adapted-source) below. Everything else is independently
implemented: where CurveVision implements a published *format* (COCO, YOLO, Pascal VOC),
that is written from the format's specification. See
[ARCHITECTURE_COMPARISON.md](./ARCHITECTURE_COMPARISON.md#1-licensing-position) for the
full licensing position.

### Adapting CVAT code

[ADR 0007](./adr/0007-cvat-reuse-policy.md) establishes that we *will* adapt specific
MIT-licensed code from [CVAT](https://github.com/cvat-ai/cvat) — starting with video frame
extraction, where re-deriving years of decoder edge cases would be indefensible. A license
audit of `cvat-ai/cvat` at commit `1d0c395` (2026-09-11) found a single MIT `LICENSE` and
`SPDX-License-Identifier: MIT` on all 1,473 source headers, with no exceptions.

Three things happen together whenever such a file lands, and a change that does fewer is
incomplete:

1. The file keeps CVAT's copyright line and MIT notice at the top, with a note saying what
   we changed and why.
2. A row is added to the **Adapted source** table below, naming the upstream file and commit.
3. `licenses/MIT-cvat.txt` carries CVAT's full license text.

### Adapted source

| CurveVision file | Adapted from | Upstream commit | License | What was taken |
| --- | --- | --- | --- | --- |
| `server/curvevision/media/video.py` | [`cvat/apps/engine/media_extractors.py`](https://github.com/cvat-ai/cvat/blob/1d0c39576c3239dcaf8ba7baee71a1b8de496c0e/cvat/apps/engine/media_extractors.py) | `1d0c395` (2026-09-11) | MIT — © 2019–2022 Intel Corporation, © CVAT.ai Corporation | The decoding strategy (count frames by decoding rather than trusting container metadata; address frames in decode order rather than by seeking) and two edge cases real files require: the `DURATION` metadata fallback for containers that omit a stream duration, and honouring rotation metadata. Re-derived in CurveVision's own idiom — bytes or a filesystem path instead of CVAT's `Openable`, no 3D dimension, no manifest, no Django or DRF. |

The audit behind this row was re-run immediately before the adaptation: `cvat-ai/cvat` at
`1d0c395` carries one MIT `LICENSE` and `SPDX-License-Identifier: MIT` on all 505 Python
source headers, with no exceptions.

Full license texts for permissive licenses used here are reproduced under
[`licenses/`](../licenses/).

---

## Server (Python)

| Package | License | Copyright / Author | Role |
| --- | --- | --- | --- |
| [FastAPI](https://github.com/fastapi/fastapi) | MIT | Sebastián Ramírez | Web framework |
| [Starlette](https://github.com/encode/starlette) (via FastAPI) | BSD-3-Clause | Encode OSS Ltd. | ASGI toolkit |
| [Uvicorn](https://github.com/encode/uvicorn) | BSD-3-Clause | Encode OSS Ltd. | ASGI server |
| [Pydantic](https://github.com/pydantic/pydantic) | MIT | Pydantic Services Inc. and contributors | Validation, serialisation |
| [pydantic-settings](https://github.com/pydantic/pydantic-settings) | MIT | Pydantic Services Inc. | Configuration |
| [SQLAlchemy](https://github.com/sqlalchemy/sqlalchemy) | MIT | Michael Bayer and contributors | ORM and Core |
| [Alembic](https://github.com/sqlalchemy/alembic) | MIT | Michael Bayer and contributors | Database migrations |
| [greenlet](https://github.com/python-greenlet/greenlet) | MIT / PSF | Armin Rigo, Christian Tismer and contributors | Required by SQLAlchemy's async support |
| [argon2-cffi](https://github.com/hynek/argon2-cffi) | MIT | Hynek Schlawack | Argon2id password hashing |
| [PyJWT](https://github.com/jpadilla/pyjwt) | MIT | José Padilla | JSON Web Tokens |
| [email-validator](https://github.com/JoshData/python-email-validator) | Unlicense / CC0 | Joshua Tauberer | Email address validation |
| [python-multipart](https://github.com/Kludex/python-multipart) | Apache-2.0 | Andrew Dunham and contributors | Multipart upload parsing |
| [httpx](https://github.com/encode/httpx) | BSD-3-Clause | Encode OSS Ltd. | HTTP client (inference, webhooks) |
| [prometheus-client](https://github.com/prometheus/client_python) | Apache-2.0 | Prometheus authors | Metrics |
| [asyncpg](https://github.com/MagicStack/asyncpg) | Apache-2.0 | MagicStack Inc. | PostgreSQL driver (`postgres` extra) |
| [psycopg](https://github.com/psycopg/psycopg) | LGPL-3.0-or-later | Daniele Varrazzo and contributors | PostgreSQL driver, used unmodified as a library (`postgres` extra) |
| [boto3](https://github.com/boto/boto3) | Apache-2.0 | Amazon.com, Inc. | S3-compatible storage (`s3` extra) |
| [Pillow](https://github.com/python-pillow/Pillow) | MIT-CMU | Jeffrey A. Clark and contributors | Image probing and thumbnails (`media` extra) |
| [PyAV](https://github.com/PyAV-Org/PyAV) | BSD-3-Clause | PyAV authors | Video decoding (`media` extra) — see FFmpeg note below |
| [PyInstaller](https://github.com/pyinstaller/pyinstaller) | GPL-2.0-or-later **with a bootloader exception** | PyInstaller Development Team | Packages the local server into one executable (`desktop` extra). The exception explicitly permits distributing applications built with it under any license; PyInstaller itself is a build-time tool and is not linked into CurveVision. |
| [Dramatiq](https://github.com/Bogdanp/dramatiq) | LGPL-3.0 | CLEARTYPE SRL | Background job queue, used unmodified as a library (`worker` extra) |
| [redis-py](https://github.com/redis/redis-py) | MIT | Redis Inc. and contributors | Redis client (`worker` extra) |

## Desktop shell (Rust)

Used only by the Tauri shell in `desktop/shell`, which is built per platform and is not
part of the server or web distributions.

| Crate | License | Copyright / Author | Role |
| --- | --- | --- | --- |
| [tauri](https://github.com/tauri-apps/tauri) | Apache-2.0 OR MIT | Tauri Programme within The Commons Conservancy | Native window, webview, IPC |
| [tauri-build](https://github.com/tauri-apps/tauri) | Apache-2.0 OR MIT | Tauri Programme within The Commons Conservancy | Build-time code generation |
| [tauri-plugin-dialog](https://github.com/tauri-apps/plugins-workspace) | Apache-2.0 OR MIT | Tauri Programme within The Commons Conservancy | Native file and folder pickers |
| [tauri-plugin-opener](https://github.com/tauri-apps/plugins-workspace) | Apache-2.0 OR MIT | Tauri Programme within The Commons Conservancy | Opening the data folder in the OS file manager |
| [serde](https://github.com/serde-rs/serde) | Apache-2.0 OR MIT | Erick Tryzelaar, David Tolnay | Serialisation |
| [serde_json](https://github.com/serde-rs/json) | Apache-2.0 OR MIT | Erick Tryzelaar, David Tolnay | Parsing the sidecar handshake |

The shell also links the platform's own webview — WebKitGTK on Linux, WKWebView on macOS,
WebView2 on Windows — which is provided by the operating system and not redistributed here.
On Linux, packages produced from this repository declare a dependency on
`libwebkit2gtk-4.1-0` rather than bundling it.

### FFmpeg (transitive, through PyAV)

PyAV binds to **FFmpeg**, which is licensed **LGPL-2.1-or-later**, or **GPL-2.0-or-later**
when built with GPL-only components (such as `libx264` or `libx265`).

If you distribute a CurveVision container image or binary that bundles FFmpeg, you take on
that build's obligations — at minimum, providing the FFmpeg source or a written offer for
it, and allowing relinking for the LGPL case. The official `deploy/Dockerfile.server`
installs PyAV from PyPI wheels, which bundle an LGPL FFmpeg build without GPL components.

This is the same obligation every tool in this space carries; we state it explicitly
because it is easy to inherit without noticing.

## SDK and CLI (Python)

| Package | License | Copyright / Author | Role |
| --- | --- | --- | --- |
| [httpx](https://github.com/encode/httpx) | BSD-3-Clause | Encode OSS Ltd. | HTTP client |
| [Typer](https://github.com/fastapi/typer) | MIT | Sebastián Ramírez | Command-line interface |
| [Click](https://github.com/pallets/click) (via Typer) | BSD-3-Clause | Pallets | CLI plumbing |

## Web application (TypeScript)

| Package | License | Copyright / Author | Role |
| --- | --- | --- | --- |
| [React](https://github.com/facebook/react) | MIT | Meta Platforms, Inc. and affiliates | UI framework |
| [react-dom](https://github.com/facebook/react) | MIT | Meta Platforms, Inc. and affiliates | DOM renderer |
| [React Router](https://github.com/remix-run/react-router) (`react-router-dom`) | MIT | Remix Software Inc. | Routing |
| [TanStack Query](https://github.com/TanStack/query) (`@tanstack/react-query`) | MIT | Tanner Linsley | Server-state cache |
| [Zustand](https://github.com/pmndrs/zustand) | MIT | Paul Henschel and contributors | Client state |
| [rbush](https://github.com/mourner/rbush) | MIT | Vladimir Agafonkin | R-tree spatial index behind the canvas |
| [clsx](https://github.com/lukeed/clsx) | MIT | Luke Edwards | Class-name composition |
| [lucide-react](https://github.com/lucide-icons/lucide) | ISC | Lucide contributors | Icons |

## Container images

These run as unmodified services; CurveVision links to none of them.

| Image | License | Notes |
| --- | --- | --- |
| [PostgreSQL](https://www.postgresql.org/) | PostgreSQL License (BSD-like) | Primary datastore |
| [Redis](https://redis.io/) | RSALv2 / SSPLv1 (Redis 7.4+) | Cache and job broker. Run as a network service, never linked or redistributed, so the source-available terms do not extend to CurveVision. Substitute [Valkey](https://valkey.io/) (BSD-3-Clause) if you prefer a fully OSI-licensed drop-in. |
| [MinIO](https://min.io/) | AGPL-3.0 | Development object storage, run as an unmodified network service. Any S3-compatible backend works instead. |
| [nginx](https://nginx.org/) | BSD-2-Clause | Static file serving and reverse proxy |

## Referenced but not used

| Project | License | Relationship |
| --- | --- | --- |
| [CVAT](https://github.com/cvat-ai/cvat) | MIT — © 2018-2022 Intel Corporation, © 2022-2025 CVAT.ai Corporation | Studied as an architectural and product reference; see [ARCHITECTURE_COMPARISON.md](./ARCHITECTURE_COMPARISON.md). Where code is actually adapted rather than only studied, it is listed under [Adapting CVAT code](#adapting-cvat-code) above — that table, not this row, is the authoritative list. We claim no affiliation with, or endorsement by, CVAT.ai Corporation or Intel Corporation. |
| [Datumaro](https://github.com/openvinotoolkit/datumaro) | MIT — © Intel Corporation | Evaluated for dataset format conversion and not adopted, for the streaming reasons documented in the architecture comparison. An optional bridge is planned. |

## Sample photographs

`docs/images/samples/` holds four public-domain and CC0 photographs, used by
`scripts/screenshot.py` to produce the screenshots in the README. Per-image licence and
attribution travel with them in
[`docs/images/samples/CREDITS.md`](./images/samples/CREDITS.md).

| File | Licence | Credit |
| --- | --- | --- |
| `coffee.jpg` | CC0 | Rachel Michetti, courtesy of Pikolo Espresso Bar |
| `astronaut.jpg` | Public domain (no known copyright restrictions) | NASA — astronaut Eileen Collins |
| `rocket.jpg` | Public domain | SpaceX — Falcon 9 carrying DSCOVR, Cape Canaveral |
| `cat.jpg` | CC0 | Stefan van der Walt |

They were extracted from [scikit-image](https://github.com/scikit-image/scikit-image)'s
bundled sample data (BSD-3-Clause project; these particular files are public domain or CC0
in their own right) by `scripts/extract_sample_images.py`. scikit-image is not a
CurveVision dependency — it was a one-time extraction tool, and the four JPEGs are what is
committed.

## Reporting an omission

If a dependency is missing, misattributed, or its license is stated incorrectly, please
open an issue — we will treat it as a priority fix.
