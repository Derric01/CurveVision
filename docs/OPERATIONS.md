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
| `minio` | Media, thumbnails, export artifacts | **yes** |
| `redis` | Cache and job broker | no — safe to lose |

Scale `api` and `worker` horizontally; they hold no local state. Redis is deliberately not
a source of truth: media chunks live in object storage, so losing Redis costs a cache, not
data.

## Backups

**Back up PostgreSQL and object storage together.** An annotation row whose media is
missing is not a usable dataset, and neither half alone is worth restoring.

```bash
# Database
docker compose exec -T postgres pg_dump -U curvevision -Fc curvevision > cv-$(date +%F).dump

# Object storage
docker compose exec -T minio mc mirror --overwrite local/curvevision /backup/curvevision
```

Take the object-storage snapshot **after** the database dump. Media is content-addressed
and immutable, so a blob written between the two is simply unreferenced — harmless. The
other order can leave a row pointing at a blob that was never captured.

### Restore

```bash
docker compose up -d postgres minio
docker compose exec -T postgres pg_restore -U curvevision -d curvevision --clean < cv-2026-01-15.dump
docker compose exec -T minio mc mirror --overwrite /backup/curvevision local/curvevision
docker compose up -d
```

**Test your restore.** An untested backup is a hypothesis. Restore into a scratch instance,
open a project, and export a dataset — that exercises the database, storage and their
relationship in one go.

## Upgrading

```bash
git pull
docker compose build
docker compose up -d
```

The `api` container runs `alembic upgrade head` on start. Take a database dump first: a
migration is the one operation that can lose data if it goes wrong.

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
