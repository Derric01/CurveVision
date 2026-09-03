# Third-Party Notices

CurveVision is distributed under the [MIT License](../LICENSE). It depends on the
open-source software listed below, each under its own license and copyright.

This file is checked by CI (`scripts/check_notices.py`): a declared dependency with no
entry here fails the build. That is deliberate — software reaching users without
attribution is a licensing problem, not a documentation nit.

**Nothing in this repository is copied or adapted from another project's source code.**
Where CurveVision implements a published *format* (COCO, YOLO, Pascal VOC), that is an
independent implementation written from the format's specification. See
[ARCHITECTURE_COMPARISON.md](./ARCHITECTURE_COMPARISON.md#1-licensing-position) for the
full licensing position, including our relationship to CVAT.

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
| [Dramatiq](https://github.com/Bogdanp/dramatiq) | LGPL-3.0 | CLEARTYPE SRL | Background job queue, used unmodified as a library (`worker` extra) |
| [redis-py](https://github.com/redis/redis-py) | MIT | Redis Inc. and contributors | Redis client (`worker` extra) |

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
| [CVAT](https://github.com/cvat-ai/cvat) | MIT — © 2018-2022 Intel Corporation, © 2022-2025 CVAT.ai Corporation | Studied as an architectural and product reference. **No CVAT source code is present in this repository.** See [ARCHITECTURE_COMPARISON.md](./ARCHITECTURE_COMPARISON.md). We claim no affiliation with, or endorsement by, CVAT.ai Corporation or Intel Corporation. |
| [Datumaro](https://github.com/openvinotoolkit/datumaro) | MIT — © Intel Corporation | Evaluated for dataset format conversion and not adopted, for the streaming reasons documented in the architecture comparison. An optional bridge is planned. |

## Reporting an omission

If a dependency is missing, misattributed, or its license is stated incorrectly, please
open an issue — we will treat it as a priority fix.
