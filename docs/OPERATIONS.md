# Operations

Running CurveVision for other people. Read [SECURITY.md](./SECURITY.md) alongside this —
several operator responsibilities live there.

## Deployment

```bash
cp .env.example .env
python3 -c 'import secrets; print(secrets.token_urlsafe(48))'   # CURVEVISION_SECRET_KEY
docker compose up -d
```

Six services:

| Service | Role | Stateful |
| --- | --- | --- |
| `api` | HTTP API, applies migrations on start | no |
| `worker` | Background jobs (media, import, export, inference, webhooks) | no |
| `web` | Static bundle plus reverse proxy | no |
| `postgres` | Every row of annotation and metadata | **yes** |
| `s3` | Media, thumbnails, export artifacts — [Versity Gateway](https://github.com/versity/versitygw), a directory served as S3 | **yes** |
| `redis` | Cache and job broker | no — safe to lose |

Plus `storage-init`, a one-shot that creates the bucket (`curvevision-server storage-init`)
and exits before `api` starts. Any S3 service can stand in for `s3` — AWS, R2, Ceph,
Backblaze: set `CURVEVISION_S3_ENDPOINT_URL`, `S3_ACCESS_KEY` and `S3_SECRET_KEY` in `.env`.
Until October 2026 `s3` was MinIO, whose images can no longer be pulled anonymously; see
[Upgrading](#from-the-minio-based-stack) if your installation still has its data.

**How images reach the browser.** By default the API streams them: browsers cannot reach
`http://s3:9000`, and a presigned URL naming it would fail. When browsers *can* reach the
store — AWS S3, R2, a public gateway — set `CURVEVISION_S3_PUBLIC_ENDPOINT_URL` to that
address and allow this application's origin in the bucket's CORS rules; images are then
fetched from the store directly by short-lived presigned URL, which takes their bandwidth
off the API.

Scale `api` and `worker` horizontally; they hold no local state. Redis is deliberately not
a source of truth: media chunks live in object storage, so losing Redis costs a cache, not
data. It also counts the **rate limit** every `api` replica shares
(`CURVEVISION_RATE_LIMIT_BACKEND=redis`, the compose default); while Redis is down, requests
are not rate-limited rather than refused ([API.md](./API.md#rate-limiting)).

### Scanning uploads for malware

Off by default. To have ClamAV scan every upload before it is stored:

```bash
echo CURVEVISION_CONTENT_SCANNER=clamd >> .env
docker compose --profile scanning up -d
```

An infected file is refused with a `422` naming what was found; nothing is stored. A file
that **cannot** be scanned — clamd down, still loading its signatures, or the file past its
`StreamMaxLength` (25 MB unless configured) — is refused with a `503`: scanning fails
closed. `CURVEVISION_CONTENT_SCANNER_FAIL_OPEN=true` accepts such files instead, logged
each time; choose it only if an outage of the scanner must not stop uploads. Raise clamd's
`StreamMaxLength` for video, or uploads of long videos will be refused. Other settings:
`CURVEVISION_CLAMD_HOST`, `_PORT` (3310), `_SOCKET` (a Unix socket instead),
`_TIMEOUT_SECONDS` (30).

Folders imported in place by the desktop application are not scanned: they are already on
the machine that would be protected.

## Single sign-on

People can sign in through any OpenID Connect provider — Keycloak, Okta, Microsoft Entra
ID, Google, Authentik, Dex. Register CurveVision with it as a *confidential* client whose
redirect URI is this deployment's web address plus `/auth/callback`, then:

```bash
CURVEVISION_OIDC_ENABLED=true
CURVEVISION_OIDC_ISSUER=https://idp.example.com/realms/team   # .well-known is read from here
CURVEVISION_OIDC_CLIENT_ID=curvevision
CURVEVISION_OIDC_CLIENT_SECRET=...
CURVEVISION_OIDC_REDIRECT_URI=https://curvevision.example.com/auth/callback
CURVEVISION_OIDC_DISPLAY_NAME="Team SSO"                       # "Sign in with Team SSO"
```

The sign-in page then offers the provider. Who gets in, and as whom:

* An account is linked by the provider's issuer and its `sub`, which never changes. On a
  first sign-in, the account with the same email is used **only if the provider says the
  email is verified**; otherwise a new account is created
  (`CURVEVISION_OIDC_CREATE_USERS=false` refuses instead).
* `CURVEVISION_OIDC_ALLOWED_DOMAINS=team.example` admits only those email domains.
* `CURVEVISION_OIDC_DEFAULT_WORKSPACE=<slug>` puts a new account in that workspace as an
  annotator, so their first sign-in lands somewhere.
* `CURVEVISION_OIDC_ONLY=true` turns password sign-in and registration off.

The server image includes what this needs; a pip install needs `curvevision-server[oidc]`.
The desktop application has no sign-in, and ignores all of this.

## Kubernetes

A Helm chart is in [`deploy/helm/curvevision`](../deploy/helm/curvevision): the API, the
workers and the web application, with a migration Job that runs once before each install
and upgrade. **PostgreSQL, Redis and S3-compatible storage are yours to provide** — every
cluster already runs those its own way, and a copy bundled here would be one more thing to
back up.

```bash
# No images are published yet: build and push both to a registry your cluster pulls from.
docker build -f deploy/Dockerfile.server -t registry.example.com/curvevision-server:0.1.0 .
docker build -f deploy/Dockerfile.web    -t registry.example.com/curvevision-web:0.1.0 .
docker push registry.example.com/curvevision-server:0.1.0
docker push registry.example.com/curvevision-web:0.1.0

helm install cv deploy/helm/curvevision \
  --set image.server.repository=registry.example.com/curvevision-server \
  --set image.web.repository=registry.example.com/curvevision-web \
  --set secretKey=$(python3 -c 'import secrets; print(secrets.token_urlsafe(48))') \
  --set database.url=postgresql+asyncpg://curvevision:PASSWORD@postgres:5432/curvevision \
  --set redis.url=redis://redis:6379/0 \
  --set storage.s3.endpointUrl=https://s3.example.com \
  --set storage.s3.accessKeyId=... --set storage.s3.secretAccessKey=... \
  --set ingress.enabled=true --set ingress.host=curvevision.example.com
```

Or keep the credentials in a Secret of your own (`existingSecret`; its keys are listed in
`values.yaml`). Every API replica counts against one rate limit, in that Redis; malware
scanning and tracing are `config.contentScanner` and `config.tracing`.

Every CI run lints the chart, validates its manifests against the Kubernetes 1.30 schemas,
and installs it into a `kind` cluster with PostgreSQL, Redis and an S3 service in it
(`deploy/helm/ci/`): it waits for every pod, uses the release through its web service —
sign-up, an image uploaded and read back, an annotation exported, a background job run by
a worker — takes a backup from inside the cluster, and runs `helm upgrade`.

## Backups

**Back up the database and object storage together.** An annotation row whose media is
missing is not a usable dataset, and neither half alone is worth restoring. One command
does both, into one archive:

```bash
docker compose exec api curvevision-server backup /backups/cv-$(date +%F).tar
docker compose cp api:/backups/cv-$(date +%F).tar .     # then copy it off the machine
```

Every row of every table, as JSON, and every stored object a row refers to — images,
video, thumbnails, frame chunks, each released dataset version's frozen copy — with its
SHA-256. Because the rows are not a database dump, **an archive restores into either
database**: a desktop installation's SQLite into a team server's PostgreSQL is the same
command. `/backups` is a named volume (`backups`), so archives survive the containers; copy
them off the machine all the same.

```bash
# Restore, into a NEW installation: migrations are run first, and a database with any
# rows in it, or at another schema revision, is refused rather than merged into.
docker compose cp cv-2026-01-15.tar api:/backups/
docker compose exec api curvevision-server restore /backups/cv-2026-01-15.tar
```

On the desktop, the same commands act on the application's data directory: quit the app,
then `curvevision-server backup cv.tar --desktop` (or `--desktop /path/to/data`), and
`restore cv.tar --desktop /new/data`. Folders annotated in place are the user's own files
and are not copied; the command says how many.

**Test your restore.** An untested backup is a hypothesis. Restore into a scratch instance,
open a project, and export a dataset — that exercises the database, storage and their
relationship in one go. (`tests/api/test_backup.py` does exactly that on every CI run, into
SQLite and into PostgreSQL.)

### For very large installations: the databases' own tools

The archive is written row by row and object by object; with millions of images,
`pg_dump` and a storage mirror are faster. The same rule applies — both, in this order:

```bash
# Database
docker compose exec -T postgres pg_dump -U curvevision -Fc curvevision > cv-$(date +%F).dump

# Object storage: the bundled `s3` service keeps objects as plain files
docker compose exec -T s3 tar -C / -cf - objects meta > s3-$(date +%F).tar
```

Take the object-storage snapshot **after** the database dump. Media is content-addressed
and immutable, so a blob written between the two is simply unreferenced — harmless. The
other order can leave a row pointing at a blob that was never captured.

Restore:

```bash
docker compose up -d postgres s3
docker compose exec -T postgres pg_restore -U curvevision -d curvevision --clean < cv-2026-01-15.dump
docker compose exec -T s3 tar -C / -xf - < s3-2026-01-15.tar
docker compose up -d
```

## Upgrading

```bash
git pull
docker compose build
docker compose up -d
```

The `api` container runs `alembic upgrade head` on start. Take a database dump first: a
migration is the one operation that can lose data if it goes wrong.

### From the MinIO-based stack

Until October 2026 the bundled object store was MinIO. Its images were withdrawn from
Docker Hub, and Quay stopped serving them anonymously, so a fresh `docker compose pull` of
the old file fails — and the new `s3` service cannot read MinIO's on-disk format. Move the
data across with a backup, which holds the database and every stored object together:

```bash
# 1. With the old stack still running, and before pulling this version. (/tmp, because
#    the old stack's ./backups mount was not writable by the server's user.)
docker compose exec api curvevision-server backup /tmp/before-s3.tar
docker compose cp api:/tmp/before-s3.tar .
docker compose down

# 2. Once before-s3.tar is safely copied somewhere, start from an empty database
#    (a restore refuses to merge into one with rows in it):
docker volume rm curvevision_postgres-data
git pull
docker compose up -d --build

# 3. Restore into the new, empty installation:
docker compose cp before-s3.tar api:/backups/
docker compose exec api curvevision-server restore /backups/before-s3.tar
```

The old `minio-data` volume is left alone; remove it when you are satisfied. An `.env` that
sets `MINIO_ROOT_USER` and `MINIO_ROOT_PASSWORD` keeps working: they are read as the new
service's keys.

## Monitoring

Prometheus metrics at `/metrics` (restricted to private networks by the shipped nginx
config):

| Metric | Watch for |
| --- | --- |
| `curvevision_http_request_duration_seconds` | p99 on `PATCH /jobs/{id}/annotations` — the autosave path annotators feel |
| `curvevision_http_requests_total{status="5xx"}` | any sustained non-zero rate |
| `curvevision_annotation_writes_total` | throughput, and unexplained drops |
| `curvevision_background_tasks_total{state="failed"}` | failing import/export/inference jobs |

Health check at `/api/v1/health` — it touches the database rather than only proving the
process is alive.

Logs are JSON, one line per request, each carrying the `request_id` also returned in
`X-Request-ID`. A user reporting "it failed at 14:32" plus that header takes you straight
to the cause.

### Tracing

OpenTelemetry traces go to any OTLP/HTTP collector — Jaeger, Grafana Tempo, Honeycomb, the
OpenTelemetry Collector:

```bash
CURVEVISION_TRACING_ENABLED=true
CURVEVISION_TRACING_OTLP_ENDPOINT=http://otel-collector:4318/v1/traces  # or OTEL_EXPORTER_OTLP_ENDPOINT
CURVEVISION_TRACING_SAMPLE_RATIO=0.1   # keep one trace in ten; 1.0 keeps all
```

One trace per request, named by its route template (`PATCH /api/v1/jobs/{job_id}/annotations`),
with its SQL statements, its outbound HTTP calls (model servers, hosted models, webhooks)
and any background job it ran inline as spans inside it. A job a worker runs is its own
trace. `/metrics` and the health check are not traced. While tracing is on, every JSON log
line written inside a span carries its `trace_id` and `span_id`. The server image includes
the `tracing` extra; a pip install needs `curvevision-server[tracing]`.

## Capacity

Rough starting points, not promises — measure your own workload:

| Scale | Suggested |
| --- | --- |
| One team, <100k images | The default compose file on one machine |
| Several teams, ~1M images | Managed Postgres, 2-4 API replicas, 2+ workers, real S3 |
| Heavy video | Extra workers on the `media` queue; that is where the CPU goes |

The database grows with annotations, not media. A million bounding boxes is a few hundred
megabytes of rows; the images are the large part, and they live in object storage.

Split worker queues when one kind of work starves another:

```bash
dramatiq curvevision.jobs.dramatiq_app --queues media
dramatiq curvevision.jobs.dramatiq_app --queues import export inference webhooks
```

## Disaster recovery

| Failure | Impact | Recovery |
| --- | --- | --- |
| `api` or `worker` dies | Requests fail or jobs pause | Restart. Background tasks are idempotent and resume rather than duplicate. |
| Redis lost | Cache cold, queued jobs lost | Restart. Re-trigger any in-flight export or import; nothing is corrupted. |
| Object storage lost | Media gone, annotations intact | Restore from backup. Annotation rows survive but reference missing blobs until you do. |
| Database lost | Everything lost | Restore from dump. **This is the one that matters.** |
| Migration fails mid-upgrade | Instance down | Restore the pre-upgrade dump, then open an issue with the Alembic output. |

The recovery-time question worth answering before you need it: how long does restoring your
largest dump actually take? Measure it once, on real data.

## Common issues

**Uploads fail with 413.** A proxy in front of nginx is imposing its own body limit; the
shipped config sets `client_max_body_size 0`.

**Media 404s after a restore.** Object storage was restored but the bucket name differs
from `CURVEVISION_S3_BUCKET`.

**Background jobs never run.** `CURVEVISION_JOB_QUEUE_BACKEND=dramatiq` without a worker
running, or the worker cannot reach Redis. Check `/api/v1/background-tasks`.

**Editor feels slow on a huge job.** Check `curvevision_http_request_duration_seconds` for
the annotation read. If the server is fast, the bottleneck is the browser; report it with a
frame count — we benchmark this path and want the data.
