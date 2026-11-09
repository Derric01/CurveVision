# Third-Party Notices

CurveVision is distributed under the [MIT License](../LICENSE). It depends on the
open-source software listed below, each under its own license and copyright.

This file is checked by CI (`scripts/check_notices.py`): a declared dependency with no
entry here fails the build. That is deliberate — software reaching users without
attribution is a licensing problem, not a documentation nit.

**Four files in this repository owe something to another project's source**, listed under
[Adapted source](#adapted-source) below — one adapts code, three follow a design. Everything
else is independently implemented: where CurveVision implements a published *format* (COCO,
YOLO, Pascal VOC) or a published *algorithm*, that is written from the specification or the
paper.

### Adapted source

[ADR 0007](./adr/0007-reusing-third-party-code.md) sets out when source may be adapted and
what that obliges. The obligation under MIT is the whole point of this section: **the
copyright notice and licence text travel with the code, and removing either while keeping
the code is not something this project does.**

| CurveVision file | Adapted from | Upstream commit | License | What was taken |
| --- | --- | --- | --- | --- |
| `server/curvevision/formats/rle.py` (`_counts_from_string`) | [`common/maskApi.c`](https://github.com/cocodataset/cocoapi/blob/master/common/maskApi.c) (`rleFrString`) | `master` | BSD-2-Clause — © 2015 Piotr Dollar and Tsung-Yi Lin | The decoding of COCO's compressed RLE string: 5-bit little-endian groups offset by 48, a continuation bit, a sign bit, and runs from the third on stored as a delta from the run two before. Re-implemented in Python so that importing a COCO file does not depend on pycocotools; held to pycocotools' own encoder by `tests/unit/test_coco_reference.py`. |
| `server/curvevision/services/organizations.py` (`_assert_may_grant`, `_assert_may_manage`) | [`cvat/apps/organizations/rules/memberships.rego`](https://github.com/cvat-ai/cvat/blob/develop/cvat/apps/organizations/rules/memberships.rego) | `develop` (read 2026-10-12) | MIT — © CVAT.ai Corporation | The *rule* rather than the code (upstream is an OPA policy): below the owner, a member manages only members ranked below them and never themselves. Adapted to organizations that may have several owners, where an owner manages anyone and the last owner is protected separately. |
| `server/curvevision/formats/cvat_xml.py` (track closing, skeleton labels) | [`cvat/apps/dataset_manager/formats/cvat.py`](https://github.com/cvat-ai/cvat/blob/develop/cvat/apps/dataset_manager/formats/cvat.py) (`dump_as_cvat_interpolation`, `CvatExtractor`) | `develop` (read 2026-10-12) | MIT — © 2019–2022 Intel Corporation, © CVAT.ai Corporation | Conventions, not code: a one-frame track's closing `outside` shape is written only when it does not pass the last frame, and a skeleton's joints are declared as labels naming their parent, which is how CVAT's reader resolves a `<skeleton>`'s `<points>`. The export was then read back with CVAT's own extractor to confirm. |
| `server/curvevision/services/comparison.py` | [`cvat/apps/quality_control/annotation_matching.py`](https://github.com/cvat-ai/cvat/blob/1d0c39576c3239dcaf8ba7baee71a1b8de496c0e/cvat/apps/quality_control/annotation_matching.py) | `1d0c395` (2026-09-11) | MIT — © CVAT.ai Corporation | The *strategy* rather than the code: compare frame by frame and label by label, match by overlap above a threshold, and classify the remainder into distinct conflict kinds (missing / extra / wrong label / poor overlap) instead of one "mismatch" bucket. No upstream code is copied — the upstream computes overlap through Datumaro and solves the assignment with SciPy, neither of which CurveVision depends on. Listed here anyway because the design is theirs and saying so costs nothing. |
| `web/src/canvas/scissors.ts` | [`cvat-core/src/opencv/intelligent-scissors.ts`](https://github.com/cvat-ai/cvat/blob/1d0c39576c3239dcaf8ba7baee71a1b8de496c0e/cvat-core/src/opencv/intelligent-scissors.ts) | `1d0c395` (2026-09-11) | MIT — © 2021–2022 Intel Corporation, © CVAT.ai Corporation | The *shape of the tool*: anchor on click, live preview to the cursor, backspace unwinds one anchor, and the search runs on a downscaled grid. **No algorithm is taken, because theirs contains none** — their 196 lines delegate to OpenCV.js's `segmentation_IntelligentScissorsMB` (a ~10 MB WASM asset). CurveVision implements the published live-wire algorithm directly (Mortensen & Barrett, SIGGRAPH 1995); see [ADR 0009](./adr/0009-intelligent-scissors.md) for why, and for the two departures from it. Listed here because the interaction design is theirs. |
| `server/curvevision/services/merge.py` | [`cvat/apps/dataset_manager/annotation.py`](https://github.com/cvat-ai/cvat/blob/1d0c39576c3239dcaf8ba7baee71a1b8de496c0e/cvat/apps/dataset_manager/annotation.py) (`ObjectManager`, `ShapeManager`, `TagManager`) | `1d0c395` (2026-09-11) | MIT — © 2018–2022 Intel Corporation, © CVAT.ai Corporation | The *strategy* for reconciling two annotators' work on the frames their jobs share: only objects inside the overlap are candidates, one cost matrix per frame with cost = 1 − similarity, an optimal one-to-one assignment, a similarity floor below which a pair is two objects rather than one, and "the earlier job wins" as the tie-break. Their cost threshold of 0.25 is a tuned constant and is inherited rather than re-guessed. Not taken: their similarity function (which returns 0 for points, polylines, ellipses and 2D cuboids, with a `FIXME`; CurveVision uses its own exact IoU instead and merges shape types theirs cannot), SciPy for the assignment (`services/matching.py` is the same algorithm without the dependency), and their IR-level track merging (CurveVision flattens tracks before export and reunites identity afterwards). |
| `server/curvevision/media/video.py` | [`cvat/apps/engine/media_extractors.py`](https://github.com/cvat-ai/cvat/blob/1d0c39576c3239dcaf8ba7baee71a1b8de496c0e/cvat/apps/engine/media_extractors.py) | `1d0c395` (2026-09-11) | MIT — © 2019–2022 Intel Corporation, © CVAT.ai Corporation | The decoding strategy (count frames by decoding rather than trusting container metadata; address frames in decode order rather than by seeking) and two edge cases real files require: the `DURATION` metadata fallback for containers that omit a stream duration, and honouring rotation metadata. Re-derived in CurveVision's own idiom — bytes or a filesystem path instead of the upstream `Openable`, no 3D dimension, no manifest, no Django or DRF. |

All four rows are the same upstream project at the same audited commit: one MIT `LICENSE`,
no other licence file anywhere in the tree, and `SPDX-License-Identifier: MIT` on every
source header with no exceptions. The audit is re-run before each row is added. Every one of
the four files keeps an upstream copyright header, and `licenses/MIT-video-decoding.txt`
carries the full licence text — a name that is now historical, since it was added for the
video work and covers all four.

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
| [aiosqlite](https://github.com/omnilib/aiosqlite) | MIT | Amethyst Reese | SQLite driver: the default database, and the one every desktop installation uses |
| [greenlet](https://github.com/python-greenlet/greenlet) | MIT / PSF | Armin Rigo, Christian Tismer and contributors | Required by SQLAlchemy's async support |
| [argon2-cffi](https://github.com/hynek/argon2-cffi) | MIT | Hynek Schlawack | Argon2id password hashing |
| [defusedxml](https://github.com/tiran/defusedxml) | PSF-2.0 | Christian Heimes | Parsing imported annotation files (CVAT XML, Pascal VOC) without entity expansion or external entities |
| [PyJWT](https://github.com/jpadilla/pyjwt) | MIT | José Padilla | JSON Web Tokens |
| [cryptography](https://github.com/pyca/cryptography) (via `pyjwt[crypto]`) | Apache-2.0 OR BSD-3-Clause | The Python Cryptographic Authority and contributors | Verifying single sign-on ID tokens (RS256/ES256; `oidc` extra) |
| [email-validator](https://github.com/JoshData/python-email-validator) | Unlicense / CC0 | Joshua Tauberer | Email address validation |
| [python-multipart](https://github.com/Kludex/python-multipart) | Apache-2.0 | Andrew Dunham and contributors | Multipart upload parsing |
| [httpx](https://github.com/encode/httpx) | BSD-3-Clause | Encode OSS Ltd. | HTTP client (inference, webhooks) |
| [anthropic](https://github.com/anthropics/anthropic-sdk-python) | MIT | Anthropic, PBC | Calling Claude for AI-assisted annotation (ADR 0010) |
| [httpx2](https://github.com/pydantic/httpx2) (via anthropic) | BSD-3-Clause | Encode OSS Ltd. and contributors | HTTP client underneath the Anthropic SDK |
| [jiter](https://github.com/pydantic/jiter) (via anthropic) | MIT | Samuel Colvin and contributors | JSON parsing for the Anthropic SDK |
| [docstring-parser](https://github.com/rr-/docstring_parser) (via anthropic) | MIT | Marcin Kurczewski | Used by the Anthropic SDK |
| [truststore](https://github.com/sethmlarson/truststore) | MIT | Seth Michael Larson and contributors | The operating system's certificate store, for a desktop installation checking a team server |
| [prometheus-client](https://github.com/prometheus/client_python) | Apache-2.0 | Prometheus authors | Metrics |
| [asyncpg](https://github.com/MagicStack/asyncpg) | Apache-2.0 | MagicStack Inc. | PostgreSQL driver (`postgres` extra) |
| [psycopg](https://github.com/psycopg/psycopg) | LGPL-3.0-or-later | Daniele Varrazzo and contributors | PostgreSQL driver, used unmodified as a library (`postgres` extra) |
| [boto3](https://github.com/boto/boto3) | Apache-2.0 | Amazon.com, Inc. | S3-compatible storage (`s3` extra) |
| [Pillow](https://github.com/python-pillow/Pillow) | MIT-CMU | Jeffrey A. Clark and contributors | Image probing and thumbnails (`media` extra) |
| [PyAV](https://github.com/PyAV-Org/PyAV) | BSD-3-Clause | PyAV authors | Video decoding (`media` extra) — see FFmpeg note below |
| [PyInstaller](https://github.com/pyinstaller/pyinstaller) | GPL-2.0-or-later **with a bootloader exception** | PyInstaller Development Team | Packages the local server into one executable (`desktop` extra). The exception explicitly permits distributing applications built with it under any license; PyInstaller itself is a build-time tool and is not linked into CurveVision. |
| [Dramatiq](https://github.com/Bogdanp/dramatiq) | LGPL-3.0 | CLEARTYPE SRL | Background job queue, used unmodified as a library (`worker` extra) |
| [redis-py](https://github.com/redis/redis-py) | MIT | Redis Inc. and contributors | Redis client (`worker` and `redis` extras: job broker, shared rate limit) |
| [opentelemetry-sdk](https://github.com/open-telemetry/opentelemetry-python) | Apache-2.0 | The OpenTelemetry Authors | Tracing (`tracing` extra) |
| [opentelemetry-exporter-otlp-proto-http](https://github.com/open-telemetry/opentelemetry-python) | Apache-2.0 | The OpenTelemetry Authors | Sends traces to an OTLP/HTTP collector (`tracing` extra) |
| [opentelemetry-instrumentation-fastapi](https://github.com/open-telemetry/opentelemetry-python-contrib) | Apache-2.0 | The OpenTelemetry Authors | Request spans (`tracing` extra) |
| [opentelemetry-instrumentation-sqlalchemy](https://github.com/open-telemetry/opentelemetry-python-contrib) | Apache-2.0 | The OpenTelemetry Authors | SQL spans (`tracing` extra) |
| [opentelemetry-instrumentation-httpx](https://github.com/open-telemetry/opentelemetry-python-contrib) | Apache-2.0 | The OpenTelemetry Authors | Outbound HTTP spans (`tracing` extra) |
| [python3-saml](https://github.com/SAML-Toolkits/python3-saml) | MIT | Sixto Martín, SAML-Toolkits contributors | SAML 2.0 single sign-on: parsing and validating responses (`saml` extra). Brings [xmlsec](https://github.com/xmlsec/python-xmlsec) (MIT) and [lxml](https://github.com/lxml/lxml) (BSD-3-Clause). |
| [Datumaro](https://github.com/openvinotoolkit/datumaro) | MIT | Intel Corporation | The long tail of dataset formats, through `formats/datumaro_bridge.py` (`datumaro` extra; [ADR 0011](./adr/0011-datumaro-bridge.md)). Not in the desktop application or the default server image. |

## Desktop shell (Rust)

Used only by the Tauri shell in `desktop/shell`, which is built per platform and is not
part of the server or web distributions.

| Crate | License | Copyright / Author | Role |
| --- | --- | --- | --- |
| [tauri](https://github.com/tauri-apps/tauri) | Apache-2.0 OR MIT | Tauri Programme within The Commons Conservancy | Native window, webview, IPC |
| [tauri-build](https://github.com/tauri-apps/tauri) | Apache-2.0 OR MIT | Tauri Programme within The Commons Conservancy | Build-time code generation |
| [tauri-plugin-dialog](https://github.com/tauri-apps/plugins-workspace) | Apache-2.0 OR MIT | Tauri Programme within The Commons Conservancy | Native file and folder pickers |
| [tauri-plugin-opener](https://github.com/tauri-apps/plugins-workspace) | Apache-2.0 OR MIT | Tauri Programme within The Commons Conservancy | Opening the data folder in the OS file manager |
| [tauri-plugin-updater](https://github.com/tauri-apps/plugins-workspace) | Apache-2.0 OR MIT | Tauri Programme within The Commons Conservancy | Checking a release feed and installing a signed update |
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

## TypeScript client (`@curvevision/client`)

| Package | License | Copyright / Author | Role |
| --- | --- | --- | --- |
| [openapi-fetch](https://github.com/openapi-ts/openapi-typescript) | MIT | Drew Powers and contributors | The typed `fetch` client the package wraps; its only runtime dependency |

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
| [@tauri-apps/api](https://github.com/tauri-apps/tauri) | Apache-2.0 OR MIT | © 2017–present Tauri Apps Contributors | Calling the desktop shell (native folder picker, menu events). Dynamic-imported behind `isDesktop()`, so a browser never loads it. See [ADR 0008](./adr/0008-calling-the-desktop-shell.md). |

## Container images

These run as unmodified services; CurveVision links to none of them.

| Image | License | Notes |
| --- | --- | --- |
| [PostgreSQL](https://www.postgresql.org/) | PostgreSQL License (BSD-like) | Primary datastore |
| [Redis](https://redis.io/) | RSALv2 / SSPLv1 (Redis 7.4+) | Cache and job broker. Run as a network service, never linked or redistributed, so the source-available terms do not extend to CurveVision. Substitute [Valkey](https://valkey.io/) (BSD-3-Clause) if you prefer a fully OSI-licensed drop-in. |
| [Versity Gateway](https://github.com/versity/versitygw) | Apache-2.0 | The object store Docker Compose runs (`s3`), and CI's S3 server, as an unmodified network service. Any S3-compatible backend works instead. (MinIO, AGPL-3.0, held this role until October 2026.) |
| [ClamAV](https://www.clamav.net/) (`clamav/clamav` image) | GPL-2.0 | Optional malware scanning (`--profile scanning`), run as an unmodified network service and spoken to over clamd's socket protocol; nothing of it is linked or redistributed. |
| [nginx](https://nginx.org/) | BSD-2-Clause | Static file serving and reverse proxy |

## Referenced but not used

| Project | License | Relationship |
| --- | --- | --- |
| [Datumaro](https://github.com/openvinotoolkit/datumaro) | MIT — © Intel Corporation | Not the core of the format registry, for the streaming reasons in [ADR 0004](./adr/0004-streaming-format-registry.md); bridged to for the long tail as an optional extra ([ADR 0011](./adr/0011-datumaro-bridge.md)), listed above. The conformance checks also use it as a separate program's reader and writer. |

CurveVision claims no affiliation with, or endorsement by, any project listed in this file.
Where source is adapted, the copyright holders are named in [Adapted source](#adapted-source)
above, which is the authoritative record.

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
