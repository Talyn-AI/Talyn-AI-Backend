# Talyn Backend

Data + auth service that feeds the Talyn AI coach. FastAPI + PostgreSQL
(Docker) + SQLAlchemy + Alembic.

## Prerequisites

- Docker Desktop running (PostgreSQL 16 runs in a container)
- Python 3.14

## First-time setup

```powershell
# 1. Start PostgreSQL
docker run -d --name talyn-postgres `
  -e POSTGRES_USER=talyn `
  -e POSTGRES_PASSWORD=talyn_dev_password `
  -e POSTGRES_DB=talyn `
  -p 5432:5432 `
  postgres:16-alpine

# 2. Install dependencies
python -m pip install -r requirements.txt

# 3. Copy env config
Copy-Item .env.example .env

# 4. Create the test database (used by pytest)
docker exec talyn-postgres psql -U talyn -d talyn -c "CREATE DATABASE talyn_test;"

# 5. Apply migrations
python -m alembic upgrade head

# 6. (Optional) Seed sample content + demo learner
python scripts/seed.py

# 7. Run the server (structured JSON access logs; uvicorn's own access
#    log off to keep a single stream; every response carries X-Request-ID)
python -m uvicorn app.main:app --reload --no-access-log
```

Demo learner (from the seed script): `demo@talyn.dev` / `demo12345`

API docs: http://localhost:8000/docs

## Tests

```powershell
python -m pytest -v
```

Uses the isolated `talyn_test` database (created in step 4 above).
Rate limiting is disabled for the suite (`RATE_LIMIT_ENABLED=false` in
`tests/conftest.py`); limiter behaviour itself is covered in
`tests/test_reliability.py` with it enabled. Paystack and SMTP credentials
are cleared in `conftest.py` too — the suite must never depend on whether a
developer has finished configuring their machine.

`tests/test_backup.py` runs real `pg_dump`/`pg_restore`, so it skips unless
the Postgres client tools are on `PATH`. It is not a mocked test on purpose:
whether a dump can be restored is the only property that matters, and a mock
would prove nothing about it. CI installs `postgresql-client-16` so it runs
there.

```powershell
# Windows: pg_dump ships with Postgres, not with Docker. Either add
# "C:\Program Files\PostgreSQL\16\bin" to PATH, or run the suite in the
# container, which is what CI does.
```

## API versioning

All API routes live under `/v1` (e.g. `/v1/me/context`). `/health` stays
unversioned (load-balancer convention) and is joined by `/health/ready`:

| Probe | Touches the database | Wire it to |
|-------|----------------------|-----------|
| `GET /health` | No | Restart policy. A liveness probe that fails on a database blip restarts a healthy process. |
| `GET /health/ready` | Yes | Load balancer / uptime check. 503 when the database is unreachable, so traffic stops going to a process that cannot serve it. |

## Rate limiting

Sliding-window, per IP, 60s windows: auth endpoints
(`RATE_LIMIT_AUTH_PER_MINUTE`, default 20) and everything else
(`RATE_LIMIT_PER_MINUTE`, default 300). Exhausted callers get 429 +
`Retry-After: 60`. In-memory by default; set `REDIS_URL` to share
counters across workers/hosts (sorted-set window; Redis outages fail
open with a logged warning so a cache blip never takes the API down).

## Endpoints

| Method | Path             | What it does                     | Auth |
|--------|------------------|----------------------------------|------|
| `GET`  | `/health`        | Service health check             | no   |
| `POST` | `/v1/auth/register` | Create a learner account         | no   |
| `POST` | `/v1/auth/login`    | Login -> access + refresh JWTs       | no   |
| `POST` | `/v1/auth/refresh`  | Exchange refresh token for a new pair | no  |
| `POST` | `/v1/auth/google`   | Google ID-token sign-in (find-or-create account) | no |
| `GET`  | `/v1/auth/email-available` | Instant signup check (`?email=`) | no |
| `POST` | `/v1/auth/password-reset/request` | Start reset (emails a single-use link; DEV: token in response) | no |
| `POST` | `/v1/auth/password-reset/confirm` | Set new password with a reset token (single use) | no |
| `GET`  | `/v1/users/me`      | Get the authenticated learner's profile | yes |
| `PATCH`| `/v1/users/me`      | Update the learner's profile     | yes  |
| `PATCH`| `/users/me/password` | Change password (needs current) | yes |
| `DELETE` | `/v1/users/me`    | Delete account + all data        | yes  |
| `POST` | `/v1/courses`       | Create a course (+ lessons)      | admin |
| `GET`  | `/v1/courses`       | List courses with lesson counts (`?limit&offset`) | no |
| `GET`  | `/v1/courses/{id}`  | Get a course with its lessons    | no   |
| `GET`  | `/v1/courses/{id}/lessons` | List published lessons (`?limit&offset`) | no |
| `DELETE` | `/v1/courses/{id}` | Delete course + content         | admin |
| `POST` | `/v1/me/enroll/{course_id}` | Enroll the learner in a course | yes |
| `POST` | `/v1/me/lessons/{lesson_id}/complete` | Mark a lesson complete (+XP, once) | yes |
| `POST` | `/v1/me/quiz-results` | Record a quiz attempt (+XP)     | yes |
| `GET`  | `/v1/me/xp`         | XP total / this week / level / breakdown | yes |
| `POST` | `/v1/me/xp/award`   | Award revision/challenge/live/streak XP | yes |
| `GET`  | `/v1/me/badges`     | List earned badges (`?limit&offset`) | yes |
| `GET`  | `/v1/me/context`    | Full `LearnerContext` for the AI coach | yes |
| `GET`  | `/v1/me/path-profile` | Path profile (completed + available courses) | yes |
| `GET`  | `/v1/me/personalization-profile` | Minimal profile for content personalization | yes |
| `GET`  | `/v1/me/revision-profile` | One record per encountered topic | yes |
| `POST` | `/v1/me/conversation` | Store a chat turn (user/assistant) | yes |
| `GET`  | `/v1/me/conversation` | Read chat turns, chronological (`?limit&offset`) | yes |
| `DELETE` | `/v1/me/conversation` | Clear stored chat turns        | yes |
| `GET`  | `/v1/missions`      | Browse the published mission catalogue (public) | no |
| `POST` | `/v1/me/missions`   | Adopt a catalogue mission (`{template_id}`; one active at a time) | yes |
| `GET`  | `/v1/me/missions`   | List my adopted missions (`?status&limit&offset`) | yes |
| `GET`  | `/v1/me/missions/{id}` | Get one of my missions with steps            | yes |
| `PATCH`| `/v1/me/missions/{id}` | Change status (completed awards XP + badge) | yes |
| `POST` | `/v1/me/missions/{id}/steps/{step_id}/complete` | Complete a step (last step completes mission) | yes |
| `DELETE` | `/v1/me/missions/{id}` | Drop a mission + steps       | yes |
| `POST` | `/v1/creator/missions` | Write a mission template (draft until published) | creator |
| `GET`  | `/v1/creator/missions` | My templates                | creator |
| `GET`  | `/v1/creator/missions/{id}` | One of my templates      | creator |
| `PATCH`| `/v1/creator/missions/{id}` | Edit / publish / withdraw | creator |
| `DELETE`| `/v1/creator/missions/{id}` | Withdraw from the catalogue (adopted copies survive) | creator |
| `GET`  | `/v1/creator/missions/{id}/adoption` | How many learners took it | creator |
| `POST` | `/v1/me/buddies/matches` | Save an AI-ranked buddy match      | yes |
| `GET`  | `/v1/me/buddies/matches` | List saved buddy matches (`?limit&offset`) | yes |
| `PATCH`| `/v1/me/buddies/matches/{id}` | Accept/decline a match          | yes |
| `PUT`  | `/v1/me/study-plan`   | Create/replace the learner's study plan (one per learner) | yes |
| `GET`  | `/v1/me/study-plan`   | Get the study plan (also feeds `/v1/me/context`) | yes |
| `DELETE` | `/v1/me/study-plan` | Clear the study plan             | yes |
| `GET`  | `/v1/me/enrollments`  | Enrollments with per-course progress (`?limit&offset`); finishing all lessons flips `completed` | yes |
| `PUT`  | `/v1/me/creator/profile` | Create/replace creator profile | creator |
| `GET`  | `/v1/me/creator/profile` | Own creator profile | creator |
| `GET`  | `/v1/creators/{id}/profile` | Public creator profile (course pages) | no |
| `GET`  | `/v1/me/creator/dashboard` | Course counts, learners, revenue, recent activity | creator |
| `GET`  | `/v1/me/creator/courses` | Own courses in any status | creator |
| `GET`  | `/v1/me/creator/activity` | Recent events on own courses (`?limit&offset`) | creator |
| `POST` | `/v1/courses/{id}/modules` | Append a curriculum module | creator(owner) |
| `PATCH` | `/v1/courses/{id}/modules/{mid}` | Rename a module | creator(owner) |
| `DELETE` | `/v1/courses/{id}/modules/{mid}` | Delete module (lessons unassigned) | creator(owner) |
| `PUT`  | `/v1/courses/{id}/modules/reorder` | Set module order | creator(owner) |
| `POST` | `/v1/courses/{id}/lessons` | Add a lesson (module optional) | creator(owner) |
| `PUT`  | `/v1/courses/{id}/lessons/reorder` | Order lessons (`?module_id` omitted = unassigned) | creator(owner) |
| `GET`  | `/v1/lessons/{id}` | Lesson detail (paid content gated) | no* |
| `PATCH` | `/v1/lessons/{id}` | Edit/move a lesson | creator(owner) |
| `DELETE` | `/v1/lessons/{id}` | Delete a lesson + progress | creator(owner) |
| `GET`  | `/v1/courses/{id}/publish-check` | Dry-run publish validation | creator(owner) |
| `POST` | `/v1/courses/{id}/publish` | Publish after validation | creator(owner) |
| `POST` | `/v1/courses/{id}/unpublish` | Back to draft | creator(owner) |
| `POST` | `/v1/courses/{id}/archive` | Archive course | creator(owner) |
| `GET`  | `/v1/courses/{id}/preview` | Student-view preview (drafts OK) | creator(owner) |
| `POST` | `/v1/uploads/presigned` | Mint an S3 upload form (server key, size-capped) | creator |
| `POST` | `/v1/lessons/{id}/assets` | Attach video/resource/link (verifies size, scans) | creator(owner) |
| `GET`  | `/v1/lessons/{id}/assets` | List assets (paid gated) | no* |
| `DELETE` | `/v1/lessons/{id}/assets/{aid}` | Detach an asset | creator(owner) |
| `GET`  | `/v1/files/url?key=` | Presigned download (published public, else owner) | no* |
| `POST` | `/v1/courses/{id}/purchase` | Start checkout (paid). Returns Paystack `checkout_url`; unlocks nothing yet | yes |
| `POST` | `/v1/courses/{id}/verify?reference=` | Confirm with Paystack, then enroll. Idempotent | yes |
| `GET`  | `/v1/courses/{id}/payment` | Current payment state for this course | yes |
| `POST` | `/v1/payments/webhooks/paystack` | Paystack webhook (HMAC-SHA512 signed) | signature |
| `POST` | `/v1/me/lessons/{id}/start` | Record lesson start (idempotent) | yes |
| `POST` | `/v1/community/posts` | Publish a community post | yes |
| `GET`  | `/v1/community/posts` | Newest posts + reply counts (`?limit&offset`) | no |
| `GET`  | `/v1/community/posts/{id}` | Post with replies | no |
| `POST` | `/v1/community/posts/{id}/replies` | Reply to a post | yes |
| `DELETE` | `/v1/community/posts/{id}` | Delete own post (admin any) | yes |
| `DELETE` | `/v1/community/posts/{pid}/replies/{rid}` | Delete own reply (admin any) | yes |
| `POST` | `/v1/messages` | Send a direct message | yes |
| `GET`  | `/v1/messages/threads` | Threads with unread counts | yes |
| `GET`  | `/v1/messages/with/{id}` | History, chronological (`?limit&offset`) | yes |
| `POST` | `/v1/messages/with/{id}/read` | Mark thread read | yes |
| `DELETE` | `/v1/messages/{id}` | Delete own sent message (admin any) | yes |
| `POST` | `/v1/courses/{id}/live` | Schedule a live class (future date) | creator(owner) |
| `GET`  | `/v1/courses/{id}/live` | Class list (drafts owner-only) | no* |
| `GET`  | `/v1/live/{id}` | Detail; join link hidden unless enrolled/owner | no* |
| `POST` | `/v1/live/{id}/start` | Go live (scheduled→live) | creator(owner) |
| `POST` | `/v1/live/{id}/end` | End class (live→ended) | creator(owner) |
| `POST` | `/v1/live/{id}/cancel` | Cancel scheduled class | creator(owner) |
| `GET`  | `/v1/courses/{id}/analytics` | Lesson funnel + quiz aggregates | creator(owner) |
| `GET`  | `/v1/me/creator/analytics/overview` | Daily enrollments/purchases/revenue/completions (`?days`, max 90) | creator |

\* Public structure for published courses; paid *content* needs a purchase
enrollment (owner/admin always pass). Direct enrollments cover preview —
only `purchase` enrollments unlock paid content. Starting/completing
lessons needs any enrollment (managers bypass for preview).
| `GET`  | `/v1/admin/users`     | List users, email search (`?q&limit&offset`) | admin |
| `PATCH`| `/v1/admin/users/{id}` | Promote/demote (self-demote rejected, logged) | admin |
| `GET`  | `/v1/admin/audit-log` | Admin audit log (`?limit&offset`)  | admin |

## Admin setup

Course creation/deletion and `/v1/admin/*` are admin-only. Every role change
is written to `admin_audit_log` (actor, target, action, timestamp).
Bootstrap the first admin (the script stays for this — panels can't
create the first admin):

```powershell
python scripts/make_admin.py admin@example.com
```

## Email (password reset)

Configure SMTP (works with Resend / SendGrid / SES / Gmail SMTP):

```powershell
# .env
SMTP_HOST=smtp.resend.com
SMTP_PORT=587
SMTP_USERNAME=resend
SMTP_PASSWORD=<api-key>
SMTP_FROM="Talyn <no-reply@yourdomain.com>"
FRONTEND_URL=https://app.yourdomain.com
```

With SMTP configured, `/v1/auth/password-reset/request` emails the reset
link and never returns the token. Without SMTP outside dev it fails
closed (503); in dev the token is returned inline with a warning.

### Reset tokens are single-use

The reset link carries a random 256-bit token of which only the SHA-256 is
stored (`password_reset_tokens`). Redemption marks it spent in the same
transaction as the password change, so:

- a database dump does not hand over working reset links
- a link that worked once is dead immediately, including for whoever saw it
  in a proxy log or a forwarded email
- requesting a new link invalidates every earlier one, so a leaked old link
  stops working once the owner asks for a fresh one

Account deletion cascades the table, so a deleted user leaves no live token
behind.

### What gets emailed

| Template | When | Fails the request? |
|----------|------|--------------------|
| `welcome` | Registration | No |
| `password_reset` | Reset requested | **Yes** — an undelivered link leaves the learner stuck |
| `purchase_receipt` | Payment confirmed | No — they already paid |

Every attempt is recorded in `email_log`, including failures, readable at
`GET /v1/admin/email-log?failed_only=true` and in the admin UI. Email is the
failure mode most likely to go unnoticed: a reset that never arrived looks
exactly like a learner who forgot they asked.

The welcome and receipt templates carry the ink/ember brand in both an HTML
and a plain-text part — some clients refuse to render HTML, and those users
need the text.

## Google sign-in

1. Create a Web OAuth client at Google Cloud Console > APIs & Services >
   Credentials; add `http://localhost:3000` to authorized JavaScript origins.
2. Set `GOOGLE_CLIENT_ID` in the backend `.env` **and**
   `VITE_GOOGLE_CLIENT_ID` in `talyn-web/.env` (same value).
3. The frontend sends the Google ID token to `POST /v1/auth/google`; the
   backend verifies it server-side, creates/links the account (unverified
   Google emails are rejected), and returns Talyn session tokens. Google
   users get a random password hash, so password login stays disabled for
   them. Unset IDs fail closed on both sides with a clear message.

## Payments (Paystack)

Buying a paid course sends the learner to Paystack's hosted checkout. No card
data touches Talyn servers.

```powershell
# .env  (use test keys while integrating)
PAYSTACK_SECRET_KEY=sk_test_xxxxxxxx
```

Flow:

1. `POST /v1/courses/{id}/purchase` creates a **pending** payment and returns
   `checkout_url`. Nothing is unlocked. Repeat calls reuse the in-flight
   payment, so a double-clicked button can't create two transactions.
2. The browser redirects to Paystack. The learner pays by card, bank, USSD,
   or transfer.
3. Paystack redirects back to `/checkout/callback?course_id=…&reference=…`,
   which calls `POST /v1/courses/{id}/verify`. The backend re-confirms with
   Paystack — the URL is a hint, never the authority — and only a confirmed
   charge for the **exact** amount settles it and upgrades the enrollment.
4. In parallel Paystack POSTs `charge.success` to
   `/v1/payments/webhooks/paystack`, signed with HMAC-SHA512 over the raw
   body. This is what settles purchases when the learner closes the tab, so
   don't rely on step 3 alone.

Both paths call the same idempotent settle, so a webhook/verify race grants
access exactly once. Unsigned or mis-signed webhooks are rejected before
touching the database; a confirmed charge for the wrong amount is flagged
rather than granted, so money is never silently taken for nothing.

Point the webhook at your public URL and set the same `PAYSTACK_SECRET_KEY`:

```powershell
# Paystack dashboard > Settings > API Keys & Webhooks
# Webhook URL: https://api.your-domain.com/v1/payments/webhooks/paystack
PAYSTACK_WEBHOOK_SECRET=   # empty = reuse PAYSTACK_SECRET_KEY
PAYMENT_RETURN_URL=https://your-domain.com   # empty = FRONTEND_URL
```

**Stub provider.** With no `PAYSTACK_SECRET_KEY`, purchases auto-succeed
locally so dev and tests need no account. That collects no money, so
`ensure_production_secrets()` refuses to boot in staging/prod without a real
key — set `ALLOW_STUB_PAYMENTS=true` only for a money-free demo.

## AI coach (separate service)

```powershell
cd ../talyn-ai-coach
$env:TALYN_MOCK = "1"   # canned responses; or set ANTHROPIC_API_KEY for real calls
python -m uvicorn app.main:app --port 8001
```

Then set `VITE_COACH_URL=http://localhost:8001` in `talyn-web/.env`. The
frontend passes the backend JWT as `backend_token` so the coach loads
live learner data itself.

## Object storage

Configure an S3-compatible bucket:

```powershell
# .env
S3_ENDPOINT=https://s3.amazonaws.com   # or MinIO / R2 endpoint
S3_BUCKET=talyn-prod
S3_ACCESS_KEY=<key>
S3_SECRET_KEY=<secret>
S3_REGION=us-east-1
```

For local development, `moto` emulates S3 (`python -m moto.server -p 5000`,
bucket `talyn-dev`, endpoint `http://localhost:5000`).

Browser uploads go directly to the bucket via a presigned **POST** form, so
the bucket needs a CORS policy allowing `POST` and `GET` from the app
origin:

```json
[{ "AllowedOrigins": ["https://app.yourdomain.com"],
   "AllowedMethods": ["POST", "GET", "HEAD"],
   "AllowedHeaders": ["*"],
   "ExposeHeaders": ["ETag"] }]
```

Also enable server-side encryption (`S3_SERVER_side_ENCRYPTION`, default
AES256) and set a lifecycle rule that expires anything under `video/` and
`resource/` after a few days as a second line of defence.

### Upload hardening

Uploads used to be a presigned PUT, which has two problems worth stating
plainly:

- **A presigned PUT cannot cap size.** Whoever holds the URL can send as much
  as they like and storage pays for it.
- **`size_bytes` on an asset was whatever the client claimed**, so it was
  never evidence of anything.

What replaced it:

| Concern | How it is handled |
|---------|-------------------|
| Size | A `content-length-range` in the presigned POST policy, enforced by the storage provider before it accepts a byte. Not a client promise. |
| Size (defence in depth) | On attach, the real `ContentLength` is read back. Over the cap means something bypassed the form, so the object is deleted and refused. |
| Recorded size | `size_bytes` is now the provider's number. `AssetIn` no longer accepts a client-supplied size at all. |
| File type | Content-Type **and** extension are both checked. Content-Type is client-chosen, so `.exe` is refused even when the header claims `video/mp4`. |
| Malware | Optional ClamAV scan on attach. Infected files are deleted from storage and refused. |
| Storage at rest | Server-side encryption on every object. |
| Abandoned files | `scripts/cleanup_orphans.py` deletes objects nothing references. |

Per-purpose caps (`MAX_*_BYTES`, default 5 MB thumbnails, 512 MB video,
100 MB resources, 2 MB profile images) live in `.env`.

#### Malware scanning

Set `CLAMAV_HOST` to scan. The scanner speaks ClamAV's `clamd` INSTREAM
protocol over a plain socket — no extra Python dependency to keep current.

```powershell
# .env
CLAMAV_HOST=clamav          # container or hostname
CLAMAV_PORT=3310
UPLOAD_SCANNING_REQUIRED=false
```

Every outcome is recorded on the asset as `scan_status`:

| Status | Meaning |
|--------|---------|
| `clean` | Scanned, nothing found. |
| `unscanned` | No scanner configured, or it was unreachable and strict mode is off. |
| `failed` | Scanned but no verdict — too large for the scanner, or the reply was unrecognised. |
| `infected` | Refused. The object is deleted before the request returns. |

`unscanned` is deliberately not recorded as `clean`. An operator can tell
the difference between a file that was looked at and one that never was.

`UPLOAD_SCANNING_REQUIRED=true` refuses uploads when the scanner cannot give
an answer. That keeps uploads working through a scanner outage at the cost of
accepting unscanned files, so only turn it on once the scanner host is
reliable.

ClamAV is signature-based: it catches known malware well and novel threats
not at all. It is one layer, not a guarantee.

#### Cleaning up abandoned uploads

Every presign mints a key, and most keys never get used — the creator
closes the tab, picks a different file, or the upload fails halfway. Those
objects cost storage forever.

```powershell
python -m scripts.cleanup_orphans --dry-run    # report only
python -m scripts.cleanup_orphans              # delete past the TTL
python -m scripts.cleanup_orphans --max-age 1  # testing
```

Only keys matching the server-generated pattern are considered, so a backup
or anything else sharing the bucket is left alone, and objects with no
readable timestamp are kept rather than assumed old. Run it daily from cron:

```cron
17 4 * * * cd /opt/talyn && docker compose exec -T backend \
  python -m scripts.cleanup_orphans >> /var/log/talyn-orphans.log 2>&1
```

## Configuration (`.env`)

| Key | Default | What it does |
|-----|---------|--------------|
| `SECRET_KEY` | *(required, no default)* | JWT signing key — generate with `python -c "import secrets; print(secrets.token_hex(32))"`; non-dev refuses secrets shorter than 32 chars |
| `ENVIRONMENT` | `dev` | `dev` / `staging` / `prod` |
| `CORS_ORIGINS` | `http://localhost:3000,http://localhost:8000,http://127.0.0.1:8000` | Allowed browser origins (`*` = all, dev only) |
| `RATE_LIMIT_ENABLED` | `true` | Set `false` to disable limiting |
| `RATE_LIMIT_AUTH_PER_MINUTE` | `20` | Per-IP budget for login/register/refresh/reset |
| `RATE_LIMIT_PER_MINUTE` | `300` | Per-IP budget for everything else |
| `REDIS_URL` | *(empty = in-memory)* | e.g. `redis://localhost:6379/0`; shares counters across workers |
| `GOOGLE_CLIENT_ID` | *(empty = Google sign-in disabled)* | Web client ID; must match frontend `VITE_GOOGLE_CLIENT_ID` |
| `PAYSTACK_SECRET_KEY` | *(empty = stub provider)* | Paystack live/test secret key; non-dev refuses to boot without it |
| `PAYSTACK_WEBHOOK_SECRET` | *(empty = reuse API key)* | Key used for webhook HMAC verification |
| `PAYSTACK_API_URL` | *(empty = Paystack)* | Override for a self-hosted provider mock |
| `PAYMENT_RETURN_URL` | *(empty = `FRONTEND_URL`)* | Origin Paystack redirects the learner back to |
| `ALLOW_STUB_PAYMENTS` | `false` | Lets a non-dev environment boot with the money-free stub |
| `MAX_THUMBNAIL_BYTES` | `5242880` (5 MB) | Ceiling enforced by the presigned POST policy |
| `MAX_VIDEO_BYTES` | `536870912` (512 MB) | Ceiling enforced by the presigned POST policy |
| `MAX_RESOURCE_BYTES` | `104857600` (100 MB) | Ceiling enforced by the presigned POST policy |
| `MAX_PROFILE_IMAGE_BYTES` | `2097152` (2 MB) | Ceiling enforced by the presigned POST policy |
| `S3_SERVER_SIDE_ENCRYPTION` | `AES256` | Applied to every uploaded object |
| `ORPHAN_UPLOAD_TTL_HOURS` | `24` | Age at which unattached objects are swept |
| `CLAMAV_HOST` | *(empty = scanning off)* | ClamAV host; enables malware scanning |
| `CLAMAV_PORT` | `3310` | ClamAV `clamd` port |
| `UPLOAD_SCANNING_REQUIRED` | `false` | Refuse uploads when the scanner cannot give a verdict |

## Operations runbook

For the containerized production stack (Dockerfiles, compose, TLS, backups,
scaling, secret rotation) see **[DEPLOYMENT.md](DEPLOYMENT.md)**.

## Backups

```powershell
# Dump, then rehearse the restore, then prune. Exits non-zero and deletes the
# dump if the rehearsal fails — a backup that cannot be restored is worse
# than none, because it looks like safety on disk.
python -m scripts.backup --dest ./backups --keep 7

python -m scripts.backup --help              # all flags
python -m scripts.backup --no-verify         # faster, weaker
```

Requires `pg_dump` and `pg_restore` on `PATH`, and credentials come from
`DATABASE_URL` rather than ambient `PG*` variables. The Docker image pins
`postgresql-client-16` to match the server: a newer `pg_dump` against an
older server emits settings that server rejects, so every rehearsal would
fail for reasons unrelated to your data.

What this does **not** cover: object storage. Enable S3 versioning, or take
periodic bucket snapshots — a database restore without its referenced media
is a restore of a broken app.

Scheduling and the restore procedure: **[DEPLOYMENT.md](DEPLOYMENT.md)**.

## Host-run operations

For running the backend directly on a host rather than in compose.

```powershell
# Backup (Postgres custom format, timestamped)
docker exec talyn-postgres pg_dump -U talyn -Fc talyn > talyn-backup-$(Get-Date -Format yyyyMMdd).dump

# Restore into a fresh container
docker exec -i talyn-postgres pg_restore -U talyn -d talyn --clean < talyn-backup-YYYYMMDD.dump

# Upgrade deploy
git pull
python -m pip install -r requirements.txt
python -m alembic upgrade head
python -m pytest -q            # must be green before restart
# restart the process manager / container afterwards
```

Back up alongside the DB dump: the `.env` file (secrets live only there)
and the Postgres data volume. Test restores by restoring into `talyn_test`
and running the suite against it.

## Migrations

```powershell
python -m alembic revision --autogenerate -m "describe change"
python -m alembic upgrade head
```

## Project structure

```
talyn-backend/
├── app/
│   ├── main.py            # FastAPI app
│   ├── config.py          # Settings from .env
│   ├── database.py        # Engine, session, Base
│   ├── core/              # security (bcrypt, JWT), auth deps
│   ├── models/            # SQLAlchemy ORM models
│   ├── schemas/           # Pydantic request/response
│   ├── services/          # xp ledger, learner-context builder
│   └── routers/           # auth, users, courses, progress
├── scripts/               # seed.py (sample content + demo learner)
├── alembic/               # migrations
└── tests/                 # pytest suite (uses talyn_test DB)
```