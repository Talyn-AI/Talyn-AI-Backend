# Talyn deployment. Lives in the backend repo.

Everything here runs on one Docker host. Caddy terminates TLS and routes
straight to the services; Postgres never sees the internet.

No frontend is deployed: Caddy serves the API host only, and every
browser call is cross-origin (CORS applies - see .env.production.example).
If a UI is deployed again, put the web container back in front of Caddy;
the backend repo history shows the shape.

```
internet --443--> caddy --+--> backend --+--> postgres
                           +--> coach  --+--> redis
```

Only **caddy** binds a host port. Postgres and Redis sit on an internal
Docker network with no route out, so there is no port-forward to forget.

---

## What you need before the first deploy

| Item | Why | Where to get it |
|------|-----|-----------------|
| A server | 2 vCPU / 4 GB / 40 GB is comfortable | Hetzner, DigitalOcean, Fly, AWS |
| One DNS `A` record | `API_HOST` → the server's IP | Your registrar |
| An email address | ACME registration with Let's Encrypt | — |
| Docker + Compose v2 | Runs the stack | `docker.com` |
| `POSTGRES_PASSWORD` | Database credentials | `openssl rand -base64 32` |
| `SECRET_KEY` | JWT signing | `python -c "import secrets; print(secrets.token_hex(32))"` |
| `PAYSTACK_SECRET_KEY` | Real payments | Paystack dashboard → live keys |
| `GOOGLE_CLIENT_ID` | Sign-in | Google Cloud Console |
| `S3_*` | Video and files | AWS S3, Cloudflare R2, or MinIO |
| `SMTP_*` | Password reset | Resend, Postmark, SES |
| `ANTHROPIC_API_KEY` | Live coach | Anthropic console |

`alembic upgrade head` runs on every deploy as a one-shot container before
the app comes up, so schema changes are part of the deploy rather than a
manual step someone can forget.

---

## First deploy

```powershell
# 1. Put both repos on the server, side by side. The compose file builds
# the coach from a sibling checkout (see its header comment).
git clone <your-backend-repo> /opt/talyn/talyn-backend
git clone <your-coach-repo> /opt/talyn/talyn-ai-coach
cd /opt/talyn/talyn-backend

# 2. Create the config
cp .env.production.example .env
# Edit .env. Every line marked REQUIRED must be filled in — the compose file
# uses ${VAR:?message} so a missing value fails the deploy with a clear error
# rather than starting something half-configured.

# 3. Verify the config resolves before pulling anything
docker compose config --quiet

# 4. Build and start
docker compose build
docker compose up -d

# 5. Watch it come up
docker compose ps
docker compose logs -f caddy backend
```

First boot takes a minute: images build, migrations run, Postgres initialises
its data directory. Caddy requests a certificate within the first ~30s.

### Verify

```powershell
# TLS is live (should be your own cert, not a self-signed one)
curl.exe -I https://api.your-domain.com

# API behind TLS
curl.exe https://api.your-domain.com/health

# Can it actually serve a request? (checks the database too)
curl.exe https://api.your-domain.com/health/ready

# The app is running in production mode
docker compose exec backend python -c "from app.config import settings; print(settings.environment)"
```

`/health` and `/health/ready` answer different questions, and conflating
them causes outages:

- **`/health`** (liveness) never touches the database. Wire this to a
  restart policy. A liveness probe that fails on a database blip restarts a
  perfectly healthy process.
- **`/health/ready`** (readiness) does check the database and returns 503
  when it is unreachable. Wire this to your load balancer or uptime check, so
  traffic stops going to a process that cannot serve it.

If `/health` answers but the browser shows a certificate warning, the ACME
request failed. Check `docker compose logs caddy` — usually the DNS record
hadn't propagated yet. Wait a few minutes and it retries on its own.

---

## Render (free tier) + Supabase

The cheapest way to put a live link in front of someone. Render hosts the
API; Supabase hosts Postgres. Render's own free database is deliberately not
used — it expires 30 days after creation and is then deleted.

```powershell
# 1. Supabase: new project > Connect > direct connection string (port 5432,
#    not the pooler). The app normalizes the scheme, so paste it as-is.

# 2. Render: New > Blueprint > this repo. Render reads render.yaml and asks
#    for the values marked sync:false:
#      DATABASE_URL       the Supabase direct string from step 1
#      RESEND_API_KEY     Resend > API Keys (SMTP ports are blocked on Render
#                         free, so email goes over HTTPS — same templates)
#      PAYSTACK_SECRET_KEY  sk_test_... while wiring up; live only for real $env:SECRET_KEY is auto-generated. Never paste a secret that sat in a
#    file — this repo's history once held real keys, and they are burned.

# 3. Migrations run inside the container on boot (see Dockerfile CMD), so the
#    first deploy brings its own schema. Watch it:
#    Render dashboard > service > Logs.
```

Verify: `https://<your-service>.onrender.com/health` then `/health/ready`
(the second checks the database too).

Caveats, all accepted deliberately for a demo box:

- The service sleeps after 15 idle minutes; first request takes ~1 minute.
  Warm it before a demo. A free uptime monitor on `/health` keeps it warm.
- Supabase pauses after a week idle — one click resumes, data intact.
- No ClamAV: uploads record `unscanned`. No Redis: rate limiting is
  per-process, correct on one instance. No S3 yet: uploads fail closed.
- Backups are Supabase's, not `scripts/backup.py` (no cron here).

---

## Everyday operations

```powershell
# Logs
docker compose logs -f backend           # follow one service
docker compose logs --tail=200 coach     # recent only

# Restart after a config change
docker compose up -d --force-recreate backend

# Scale out (only valid because REDIS_URL is set)
docker compose up -d --scale backend=3

# Disk usage
docker system df
```

### Deploying a code change

```powershell
git pull                                   # in talyn-backend/
git -C ../talyn-ai-coach pull              # and in the coach repo
docker compose build
docker compose up -d              # recreates changed services, reruns migrations
```

Migrations are safe to re-run. If one fails, the app containers stay on their
previous version and the failure is in `docker compose logs migrate` — a
failed migration will not half-apply in front of a running app.

### Backing up

Postgres data lives in the `talyn_postgres-data` volume. A backup runs the
same script as the tests do, and every run is verified:

```powershell
# One-off, with a restore rehearsal
docker compose run --rm backup

# What it does
#   1. pg_dump in custom format, gzipped, timestamped
#   2. restore it into a scratch database and compare row counts
#   3. delete the dump if that fails — a dump that cannot be restored is
#      worse than none, because it looks like safety on disk
#   4. prune to --keep, and write a MANIFEST.txt with checksums
```

**Schedule it.** Cron on the host:

```cron
# /etc/cron.d/talyn-backup  (03:17, not on the hour — every deploy tool
#  runs at :00 and you would rather not compete for the disk)
17 3 * * * cd /opt/talyn/talyn-backend && docker compose run --rm backup >> /var/log/talyn-backup.log 2>&1
```

Check it actually ran. A backup job that has been failing for a month looks
exactly like one that has been succeeding:

```bash
tail -20 /var/log/talyn-backup.log   # every line should say "verified"
ls -la backups/                       # MANIFEST.txt lists what exists
```

**Restore.**

```powershell
docker compose exec -T postgres dropdb -U talyn --if-exists talyn_restore
docker compose exec -T postgres createdb -U talyn talyn_restore
# Copy the newest dump out and in as a plain file (pg_restore needs to seek,
# so a pipe will not work):
gunzip -c backups/talyn-20261001-031700.dump.gz > /tmp/r.dump
docker cp /tmp/r.dump talyn-postgres:/tmp/r.dump
docker compose exec -T postgres pg_restore -U talyn -d talyn --clean /tmp/r.dump
```

Rehearse this into a scratch database rather than over production:

```bash
docker compose run --rm backup   # does exactly this internally, every night
```

**What backups do not cover.** Object storage. A database restore without its
referenced media is a restore of a broken app. On S3, turn on versioning and
a lifecycle rule; elsewhere take periodic bucket snapshots.

**Keep dumps off the same disk.** `BACKUP_DIR` should point at a mounted
volume or a second host. A backup that dies with the machine it was
protecting is not a backup.

---

## Secrets

`.env` is gitignored and holds real credentials on the host. That's fine for
a single box, but it means anyone with shell access can read your Paystack
secret. Two ways to do better:

**1. Host-level environment (simplest).** On a systemd host, use
`systemd-creds encrypt` or an `/etc/talyn/secrets.env` file owned by root
with `chmod 600`, sourced before `docker compose up`. Compose reads the
process environment, so `${VAR}` resolves without a `.env` at all.

**2. A secrets manager (recommended).** AWS Secrets Manager, GCP Secret
Manager, Azure Key Vault, HashiCorp Vault, or a hosted option like Doppler.
Fetch values at deploy time into the environment and run compose. The
compose file needs no changes — it already reads `${VAR}`.

**Rotate a leaked key:** Paystack → dashboard → regenerate → update `.env` →
`docker compose up -d --force-recreate backend`. Google client secrets and the
S3 secret key rotate the same way. `SECRET_KEY` is the exception: rotating it
invalidates every issued JWT, so everyone is signed out.

---

## Production checklist

Run through this before pointing real learners at the box.

- [ ] `docker compose config` resolves with no missing-variable warnings
- [ ] Certificate is issued for your real domain, not self-signed
- [ ] `/health` responds over HTTPS
- [ ] `ENVIRONMENT=prod` and the app reports `prod` (the config guard would
      otherwise have refused to boot without a Paystack key)
- [ ] `PAYSTACK_SECRET_KEY` is the **live** key, not `sk_test_`
- [ ] Paystack webhook URL set to `https://api.your-domain.com/v1/payments/webhooks/paystack`
- [ ] `POSTGRES_PASSWORD` and `SECRET_KEY` are long random values, not the
      placeholders from `.env.production.example`
- [ ] `REDIS_URL` set — without it each backend replica enforces its own rate
      limit, multiplying the effective limit by the replica count
- [ ] `ANTHROPIC_API_KEY` set (otherwise learners get canned replies)
- [ ] Upload caps sized for your plan (`MAX_VIDEO_BYTES` etc.) and S3
      server-side encryption on. The cap is enforced by a
      `content-length-range` in the presigned POST policy, so it holds even
      against a client that ignores the UI
- [ ] `scripts/cleanup_orphans` scheduled — abandoned uploads are invisible
      in the app and cost storage forever
- [ ] ClamAV wired up (`CLAMAV_HOST`) or a conscious decision that uploads
      stay unscanned. Nothing is recorded as "clean" unless a scanner said so
- [ ] SMTP credentials verified — send a real reset to your own address and
      confirm it arrives, then check `GET /v1/admin/email-log?failed_only=true`
      is empty. A rejected credential shows up there and nowhere else
- [ ] S3 bucket CORS allows `PUT` from your calling origin with
      `Content-Type` in `AllowedHeaders`
- [ ] Google OAuth client has your production origin in *authorized
      JavaScript origins*
- [ ] `CORS_ORIGINS` lists only your real origins
- [ ] A real purchase completed end to end: paid, redirected back, content
      unlocked
- [ ] First automated backup taken and a restore rehearsed
- [ ] Backups **scheduled** in cron and the log actually read once. Verified
      or not, an unscheduled backup is worth nothing
- [ ] `BACKUP_DIR` on separate storage from the database volume

---

## Scaling

The backend runs one uvicorn worker per container, on purpose. Multiple
workers would each open their own connection pool, so a 4-worker container
needs 4× the Postgres connections. Scale with replicas instead:

```powershell
docker compose up -d --scale backend=3
```

Two prerequisites:

- `REDIS_URL` must be set, or rate-limit counters are per-process and each
  replica silently grants the full budget.
- Postgres `max_connections` must cover every replica's pool. The default
  pool is 5 connections + 10 overflow per container; 3 replicas is up to 45.

For real horizontal scaling across hosts, put a managed Postgres and a shared
Redis in front (RDS + ElastiCache, or Neon + Upstash) and move the API behind
a load balancer. The app code doesn't change — only the connection strings.

---

## What still needs doing before a public launch

These are tracked, not done. Flagging them so the deploy doesn't get mistaken
for "ready for the public":

| Gap | Impact |
|-----|--------|
| Automated backups | The script verifies every run, but *you* still have to schedule it and read the log. |
| ClamAV signatures | Signature-based scanning catches known malware and nothing newer. It is one layer, not a guarantee. |
| Frontend tests | Zero component coverage; a regression only shows up in a browser. |
| Legal review | Terms and Privacy are written to match the product, but **no lawyer has read them.** Every uncertain clause is tagged `[counsel: confirm]`. |
| Error tracking | Logs are JSON to stdout with no aggregation; you'll be reading `docker logs` to debug a report. |
| Monitoring and uptime checks | `/health/ready` exists for an external checker; nothing is wired to alert you when it fails. |

---

## Troubleshooting

**Cert never issued.** `docker compose logs caddy`. ACME needs DNS pointing
here and inbound 80 open (for the HTTP-01 challenge) alongside 443. If a
provider blocks 80, use the TLS-ALPN challenge on 443 — uncomment the `tls`
block in the `Caddyfile`.

**Migrations fail on deploy.** Read the error in `docker compose logs migrate`.
An `UndefinedTable` usually means a previous migration was edited after
running; check `alembic current` against the revision chain.

**Backend restarts in a loop.** `docker compose logs backend`. A
`RuntimeError` from `ensure_production_secrets` means the environment is
non-dev and is missing a real secret — the message names it.

**Browser calls fail but curl works.** CORS. There is no same-origin
frontend container, so every browser call is cross-origin and
`CORS_ORIGINS` (backend and coach) must list the calling origin. Confirm
with `docker compose exec backend python -c "from app.config import
settings; print(settings.cors_origins)"`.

**Rate limits seem too generous.** `REDIS_URL` isn't set, or is unreachable.
The limiter fails open by design rather than locking everyone out — check
`docker compose logs backend` for Redis errors.
