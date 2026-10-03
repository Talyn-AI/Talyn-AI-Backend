# Talyn API reference

Generated from the application's OpenAPI schema — do not edit by hand.
Regenerate with `python -m scripts.generate_api_reference`.

## Conventions

| Column | Meaning |
|---|---|
| `🔒 user` | requires a learner access token |
| `🔒 admin` | requires an admin token |
| `○ optional` | works signed-out; behaves differently when signed in |
| `— public` | no token needed |
| `✓` wired | some frontend page calls this |
| `—` unwired | backend only; no page calls it yet |

Base URL: add `/v1` to every path below. In development that is
`http://localhost:8000`; deployed it is the backend's public origin.

Auth: `Authorization: Bearer <access_token>` on every row marked 🔒 or ○.
Errors are always `{"detail": "..."}`; validation errors put an array
of objects in `detail` instead.

Money is always integer minor units (kobo). Timestamps are UTC ISO-8601.

## Signup and onboarding flow

New accounts go through this order. Enforce it client-side with
`GET /v1/onboarding/status` — its `next_step` is the single value the
client routes on (`verify_email` → `choose_pace` → `choose_interests`
→ `complete` → `done`). Do not reimplement the order in the frontend;
two implementations of it will disagree.

1. `POST /v1/auth/register` — creates the account and emails a
   verification link (`/verify-email?token=...`). The welcome email
   is held back until the address is confirmed.
2. `POST /v1/auth/email-verification/confirm` — proves the address.
   Links are single-use and expire after 24 hours. Re-request with
   `POST /v1/auth/email-verification/request`, which answers
   identically for known and unknown addresses.
3. `GET /v1/onboarding/options` (public) — the pace list and the
   interest list. Render pickers from this; never hardcode the
   options, or the client will offer values the API rejects.
4. `POST /v1/onboarding/complete` (`learning_pace` + `interests`) —
   records both and seeds the study plan's daily goal from the pace.
   Repeatable: changing pace later re-seeds the plan.

Until onboarding completes, enrolment, lesson start/complete, quiz
results, mission adoption and mission steps answer **409** with a
message naming what is missing. Reads stay open. Treat 409 here as
'route into onboarding', not as an error screen.

Google sign-in skips verification (Google already proved the address)
but still goes through pace + interests.

### Where those values live

`GET /v1/users/me` returns the profile only — name, email, interests,
difficulty, goals, timestamps. It deliberately does **not** carry
`email_verified_at`, `learning_pace` or `onboarding_completed_at`, so
do not go looking for them there. `GET /v1/onboarding/status` is the
single read for all of it, including `next_step`: one call decides
which screen comes next.

Two further 409s come from `POST /v1/me/missions`, unrelated to
onboarding: `Finish or complete mission N first` (one active mission
at a time) and `You have already taken this mission`. Both are normal
states rather than failures — `GET /v1/missions` already reports
`adopted` and `adopted_mission_id` per catalogue entry, so the adopt
button can be hidden before either is ever hit.

---

0 endpoints across 0 areas.
