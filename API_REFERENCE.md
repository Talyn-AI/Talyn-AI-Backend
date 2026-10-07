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

126 endpoints across 20 areas.

## Admin

| | Method | Path | Auth | Wired |
|---|---|---|---|---|
| | `GET` | `/v1/admin/audit-log` | 🔒 admin | ✓ |
| | `GET` | `/v1/admin/email-log` | 🔒 admin | ✓ |
| | `GET` | `/v1/admin/users` | 🔒 admin | ✓ |
| | `PATCH` | `/v1/admin/users/{user_id}` | 🔒 admin | ✓ |

### GET /v1/admin/audit-log

Read the admin audit log, newest first.

**Responses**

**200**

array of objects

| Field | Type | Required | Notes |
|---|---|---|---|
| `id` | integer | yes | — |
| `actor_user_id` | integer *(nullable)* | yes | — |
| `actor_email` | string | yes | — |
| `target_user_id` | integer *(nullable)* | yes | — |
| `target_email` | string | yes | — |
| `action` | string | yes | — |
| `created_at` | string (date-time) | yes | — |

- **422** Validation Error


### GET /v1/admin/email-log

Read transactional email history, newest first.

Email is the failure mode that is easiest to miss: a reset that never
arrived looks identical to a learner who forgot they asked. This is where
you confirm what was actually sent and what bounced.

**Responses**

**200**

array of objects

| Field | Type | Required | Notes |
|---|---|---|---|
| `id` | integer | yes | — |
| `user_id` | integer *(nullable)* | yes | — |
| `to_email` | string | yes | — |
| `template` | string | yes | — |
| `subject` | string | yes | — |
| `status` | string | yes | — |
| `error` | string *(nullable)* | yes | — |
| `created_at` | string (date-time) | yes | — |

- **422** Validation Error


### GET /v1/admin/users

List users, newest first, optionally filtered by email.

**Responses**

**200**

array of objects

| Field | Type | Required | Notes |
|---|---|---|---|
| `id` | integer | yes | — |
| `email` | string | yes | — |
| `learner_name` | string | yes | — |
| `is_admin` | boolean | yes | — |
| `created_at` | string (date-time) | yes | — |

- **422** Validation Error


### PATCH /v1/admin/users/{user_id}

Change a user's roles. Self-demotion is rejected; changes are logged.

**Request body**

| Field | Type | Required | Notes |
|---|---|---|---|
| `is_admin` | boolean *(nullable)* | no | — |
| `is_creator` | boolean *(nullable)* | no | — |


**Responses**

**200**

| Field | Type | Required | Notes |
|---|---|---|---|
| `id` | integer | yes | — |
| `email` | string | yes | — |
| `learner_name` | string | yes | — |
| `is_admin` | boolean | yes | — |
| `created_at` | string (date-time) | yes | — |

- **422** Validation Error


## Auth

| | Method | Path | Auth | Wired |
|---|---|---|---|---|
| | `GET` | `/v1/auth/email-available` | — public | ✓ |
| | `POST` | `/v1/auth/email-verification/confirm` | — public | — |
| | `POST` | `/v1/auth/email-verification/request` | — public | — |
| | `POST` | `/v1/auth/google` | — public | ✓ |
| | `POST` | `/v1/auth/login` | — public | ✓ |
| | `POST` | `/v1/auth/password-reset/confirm` | — public | ✓ |
| | `POST` | `/v1/auth/password-reset/request` | — public | ✓ |
| | `POST` | `/v1/auth/refresh` | — public | ✓ |
| | `POST` | `/v1/auth/register` | — public | ✓ |

### GET /v1/auth/email-available

Check whether an email can register (used for instant signup feedback).

Rejects malformed addresses with 422. Like every public signup form,
this intentionally reveals whether an address is taken.

**Responses**

**200**

object

- **422** Validation Error


### POST /v1/auth/email-verification/confirm

Mark an address verified using the emailed token.

The token is spent in the same transaction that stamps the verification, so
a link that worked once cannot be replayed. Confirming an already-verified
address succeeds rather than erroring: a user clicking a second email from
their inbox should not be shown a failure.

**Request body**

| Field | Type | Required | Notes |
|---|---|---|---|
| `token` | string | yes | — |


**Responses**

**200**

object

- **422** Validation Error


### POST /v1/auth/email-verification/request

(Re)send the verification link.

Idempotent and non-disclosing: an already-verified address and an unknown
one get the same response and roughly the same work, so this cannot be used
to discover who has an account.

**Request body**

| Field | Type | Required | Notes |
|---|---|---|---|
| `email` | string | yes | — |


**Responses**

**200**

object

- **422** Validation Error


### POST /v1/auth/google

Sign in with Google: verify the ID token, find-or-create the user.

Requires GOOGLE_CLIENT_ID configured (503 otherwise). Google users get
a random unguessable password hash, so password login is effectively
disabled for them — they sign in through Google.

**Request body**

| Field | Type | Required | Notes |
|---|---|---|---|
| `id_token` | string | yes | — |


**Responses**

**200**

| Field | Type | Required | Notes |
|---|---|---|---|
| `access_token` | string | yes | — |
| `refresh_token` | string | no | default `` |
| `token_type` | string | no | default `bearer` |

- **422** Validation Error


### POST /v1/auth/login

Authenticate and receive a JWT bearer token.

**Request body**

| Field | Type | Required | Notes |
|---|---|---|---|
| `email` | string (email) | yes | — |
| `password` | string | yes | — |


**Responses**

**200**

| Field | Type | Required | Notes |
|---|---|---|---|
| `access_token` | string | yes | — |
| `refresh_token` | string | no | default `` |
| `token_type` | string | no | default `bearer` |

- **422** Validation Error


### POST /v1/auth/password-reset/confirm

Set a new password using a reset link or a reset code.

The credential is spent in the same transaction as the password change,
so one that worked once cannot work again — including if someone replays
it after the legitimate owner has already reset. Both credentials redeem
the same row, so using either spends both.

**Request body**

| Field | Type | Required | Notes |
|---|---|---|---|
| `token` | string *(nullable)* | no | Reset token from the emailed link (use either this or email + code) |
| `email` | string (email) *(nullable)* | no | Account email the code was sent to (required with code) |
| `code` | string *(nullable)* | no | 6-digit code from the reset email (required with email) |
| `new_password` | string | yes | — |


**Responses**

**200**

object

- **422** Validation Error


### POST /v1/auth/password-reset/request

Start a password reset.

Production: the reset link and code are emailed; neither ever appears in
the response. If email is not configured outside dev, this fails closed
(503) instead of leaking them.
Dev (no SMTP configured): both are returned inline with a warning.
The response shape is identical whether or not the email exists, so this
endpoint cannot be used to discover which addresses are registered.

**Request body**

| Field | Type | Required | Notes |
|---|---|---|---|
| `email` | string (email) | yes | — |


**Responses**

**200**

object

- **422** Validation Error


### POST /v1/auth/refresh

Exchange a refresh token for a new access + refresh token pair.

**Request body**

| Field | Type | Required | Notes |
|---|---|---|---|
| `refresh_token` | string | yes | — |


**Responses**

**200**

| Field | Type | Required | Notes |
|---|---|---|---|
| `access_token` | string | yes | — |
| `refresh_token` | string | no | default `` |
| `token_type` | string | no | default `bearer` |

- **422** Validation Error


### POST /v1/auth/register

Create a new learner account.

**Request body**

| Field | Type | Required | Notes |
|---|---|---|---|
| `email` | string (email) | yes | — |
| `password` | string | yes | — |
| `learner_name` | string | yes | — |
| `difficulty_level` | string (one of `beginner`, `intermediate`, `advanced`) | no | default `beginner` |
| `is_creator` | boolean | no | default `False` |
| `goals` | string | no | default `` |
| `interests` | array of string | no | — |
| `current_course` | string | no | default `` |
| `current_lesson` | string | no | default `` |
| `current_topic` | string | no | default `` |


**Responses**

**201**

| Field | Type | Required | Notes |
|---|---|---|---|
| `id` | integer | yes | — |
| `email` | string | yes | — |
| `learner_name` | string | yes | — |
| `difficulty_level` | string | yes | — |
| `goals` | string | yes | — |
| `interests` | array of string | yes | — |
| `current_course` | string | yes | — |
| `current_lesson` | string | yes | — |
| `current_topic` | string | yes | — |
| `is_admin` | boolean | no | default `False` |
| `is_creator` | boolean | no | default `False` |
| `created_at` | string (date-time) | yes | — |

- **422** Validation Error


## Buddies

| | Method | Path | Auth | Wired |
|---|---|---|---|---|
| | `GET` | `/v1/me/buddies/matches` | 🔒 user | ✓ |
| | `POST` | `/v1/me/buddies/matches` | 🔒 user | ✓ |
| | `PATCH` | `/v1/me/buddies/matches/{match_id}` | 🔒 user | ✓ |

### GET /v1/me/buddies/matches

List the learner's saved buddy matches, newest first.

**Responses**

**200**

array of objects

| Field | Type | Required | Notes |
|---|---|---|---|
| `id` | integer | yes | — |
| `buddy_user_id` | integer | yes | — |
| `match_score` | integer | yes | — |
| `status` | string | yes | — |

- **422** Validation Error


### POST /v1/me/buddies/matches

Save one AI-ranked buddy match for the learner.

**Request body**

| Field | Type | Required | Notes |
|---|---|---|---|
| `buddy_user_id` | integer | yes | — |
| `match_score` | integer | no | default `0` |


**Responses**

**201**

| Field | Type | Required | Notes |
|---|---|---|---|
| `id` | integer | yes | — |
| `buddy_user_id` | integer | yes | — |
| `match_score` | integer | yes | — |
| `status` | string | yes | — |

- **422** Validation Error


### PATCH /v1/me/buddies/matches/{match_id}

Accept or decline a saved buddy match.

**Request body**

| Field | Type | Required | Notes |
|---|---|---|---|
| `status` | string | yes | — |


**Responses**

**200**

object

- **422** Validation Error


## Community

| | Method | Path | Auth | Wired |
|---|---|---|---|---|
| | `GET` | `/v1/community/posts` | — public | ✓ |
| | `POST` | `/v1/community/posts` | 🔒 user | ✓ |
| | `DELETE` | `/v1/community/posts/{post_id}` | 🔒 user | ✓ |
| | `GET` | `/v1/community/posts/{post_id}` | — public | ✓ |
| | `POST` | `/v1/community/posts/{post_id}/replies` | 🔒 user | ✓ |
| | `DELETE` | `/v1/community/posts/{post_id}/replies/{reply_id}` | 🔒 user | ✓ |

### GET /v1/community/posts

Newest community posts with reply counts (public).

**Responses**

**200**

array of objects

| Field | Type | Required | Notes |
|---|---|---|---|
| `id` | integer | yes | — |
| `author_user_id` | integer | yes | — |
| `author_name` | string | no | default `` |
| `title` | string | yes | — |
| `body` | string | yes | — |
| `created_at` | string (date-time) | yes | — |
| `reply_count` | integer | no | default `0` |

- **422** Validation Error


### POST /v1/community/posts

Publish a community post.

**Request body**

| Field | Type | Required | Notes |
|---|---|---|---|
| `title` | string | yes | — |
| `body` | string | yes | — |


**Responses**

**201**

| Field | Type | Required | Notes |
|---|---|---|---|
| `id` | integer | yes | — |
| `author_user_id` | integer | yes | — |
| `author_name` | string | no | default `` |
| `title` | string | yes | — |
| `body` | string | yes | — |
| `created_at` | string (date-time) | yes | — |
| `reply_count` | integer | no | default `0` |

- **422** Validation Error


### DELETE /v1/community/posts/{post_id}

Delete own post (admin can delete any); replies cascade.

**Responses**

**200**

object

- **422** Validation Error


### GET /v1/community/posts/{post_id}

One post with its replies (public).

**Responses**

**200**

| Field | Type | Required | Notes |
|---|---|---|---|
| `id` | integer | yes | — |
| `author_user_id` | integer | yes | — |
| `author_name` | string | no | default `` |
| `title` | string | yes | — |
| `body` | string | yes | — |
| `created_at` | string (date-time) | yes | — |
| `reply_count` | integer | no | default `0` |
| `replies` | array of Reply | no | default `[]` |

<details><summary><code>replies</code> object</summary>

| Field | Type | Required | Notes |
|---|---|---|---|
| `id` | integer | yes | — |
| `author_user_id` | integer | yes | — |
| `author_name` | string | no | default `` |
| `body` | string | yes | — |
| `created_at` | string (date-time) | yes | — |

</details>

- **422** Validation Error


### POST /v1/community/posts/{post_id}/replies

Reply to a post.

**Request body**

| Field | Type | Required | Notes |
|---|---|---|---|
| `body` | string | yes | — |


**Responses**

**201**

| Field | Type | Required | Notes |
|---|---|---|---|
| `id` | integer | yes | — |
| `author_user_id` | integer | yes | — |
| `author_name` | string | no | default `` |
| `body` | string | yes | — |
| `created_at` | string (date-time) | yes | — |

- **422** Validation Error


### DELETE /v1/community/posts/{post_id}/replies/{reply_id}

Delete own reply (admin can delete any).

**Responses**

**200**

object

- **422** Validation Error


## Courses

| | Method | Path | Auth | Wired |
|---|---|---|---|---|
| | `GET` | `/v1/courses` | ○ optional | ✓ |
| | `POST` | `/v1/courses` | 🔒 user | ✓ |
| | `DELETE` | `/v1/courses/{course_id}` | 🔒 user | ✓ |
| | `GET` | `/v1/courses/{course_id}` | ○ optional | ✓ |
| | `PATCH` | `/v1/courses/{course_id}` | 🔒 user | ✓ |
| | `GET` | `/v1/courses/{course_id}/analytics` | 🔒 user | ✓ |
| | `GET` | `/v1/courses/{course_id}/lessons` | ○ optional | ✓ |

### GET /v1/courses

Discover courses. Defaults to published; drafts visible to owner/admin.

**Responses**

**200**

array of objects

| Field | Type | Required | Notes |
|---|---|---|---|
| `id` | integer | yes | — |
| `title` | string | yes | — |
| `description` | string | yes | — |
| `difficulty_level` | string | yes | — |
| `category` | string | no | default `` |
| `course_type` | string | no | default `free` |
| `price_naira` | integer | no | default `0` |
| `status` | string | no | default `draft` |
| `lesson_count` | integer | no | default `0` |

- **422** Validation Error


### POST /v1/courses

Create a draft course, optionally with its lessons. Creator or admin.

**Request body**

| Field | Type | Required | Notes |
|---|---|---|---|
| `title` | string | yes | — |
| `description` | string | no | default `` |
| `difficulty_level` | string (one of `beginner`, `intermediate`, `advanced`) | no | default `beginner` |
| `category` | string | no | default `` |
| `outcomes` | array of string | no | — |
| `target_audience` | string | no | default `` |
| `requirements` | string | no | default `` |
| `thumbnail_key` | string *(nullable)* | no | — |
| `course_type` | string (one of `free`, `paid`) | no | default `free` |
| `price_naira` | integer | no | default `0` |
| `lessons` | array of LessonCreate | no | — |

<details><summary><code>lessons</code> object</summary>

| Field | Type | Required | Notes |
|---|---|---|---|
| `order` | integer | yes | — |
| `title` | string | yes | — |
| `topic` | string | yes | — |
| `lesson_type` | string | no | default `lesson` |
| `estimated_minutes` | integer | no | default `15` |
| `content` | string | no | default `` |
| `is_published` | boolean | no | default `True` |

</details>


**Responses**

**201**

| Field | Type | Required | Notes |
|---|---|---|---|
| `id` | integer | yes | — |
| `title` | string | yes | — |
| `description` | string | yes | — |
| `difficulty_level` | string | yes | — |
| `creator_user_id` | integer *(nullable)* | no | — |
| `category` | string | no | default `` |
| `outcomes` | array of string | no | default `[]` |
| `target_audience` | string | no | default `` |
| `requirements` | string | no | default `` |
| `thumbnail_key` | string *(nullable)* | no | — |
| `course_type` | string | no | default `free` |
| `price_naira` | integer | no | default `0` |
| `status` | string | no | default `draft` |
| `lessons` | array of Lesson | no | default `[]` |
| `modules` | array of Module | no | default `[]` |

<details><summary><code>lessons</code> object</summary>

| Field | Type | Required | Notes |
|---|---|---|---|
| `id` | integer | yes | — |
| `order` | integer | yes | — |
| `module_id` | integer *(nullable)* | no | — |
| `title` | string | yes | — |
| `topic` | string | yes | — |
| `description` | string | no | default `` |
| `lesson_type` | string | yes | — |
| `estimated_minutes` | integer | yes | — |
| `content` | string *(nullable)* | no | — |
| `is_published` | boolean | yes | — |

</details>

<details><summary><code>modules</code> object</summary>

| Field | Type | Required | Notes |
|---|---|---|---|
| `id` | integer | yes | — |
| `title` | string | yes | — |
| `order` | integer | yes | — |
| `lessons` | array of Lesson | no | default `[]` |

</details>

- **422** Validation Error


### DELETE /v1/courses/{course_id}

Delete a course with its lessons, progress, quiz results, enrollments.

**Responses**

**200**

object

- **422** Validation Error


### GET /v1/courses/{course_id}

Get a course with its lessons. Drafts visible to owner/admin only.

**Responses**

**200**

| Field | Type | Required | Notes |
|---|---|---|---|
| `id` | integer | yes | — |
| `title` | string | yes | — |
| `description` | string | yes | — |
| `difficulty_level` | string | yes | — |
| `creator_user_id` | integer *(nullable)* | no | — |
| `category` | string | no | default `` |
| `outcomes` | array of string | no | default `[]` |
| `target_audience` | string | no | default `` |
| `requirements` | string | no | default `` |
| `thumbnail_key` | string *(nullable)* | no | — |
| `course_type` | string | no | default `free` |
| `price_naira` | integer | no | default `0` |
| `status` | string | no | default `draft` |
| `lessons` | array of Lesson | no | default `[]` |
| `modules` | array of Module | no | default `[]` |

<details><summary><code>lessons</code> object</summary>

| Field | Type | Required | Notes |
|---|---|---|---|
| `id` | integer | yes | — |
| `order` | integer | yes | — |
| `module_id` | integer *(nullable)* | no | — |
| `title` | string | yes | — |
| `topic` | string | yes | — |
| `description` | string | no | default `` |
| `lesson_type` | string | yes | — |
| `estimated_minutes` | integer | yes | — |
| `content` | string *(nullable)* | no | — |
| `is_published` | boolean | yes | — |

</details>

<details><summary><code>modules</code> object</summary>

| Field | Type | Required | Notes |
|---|---|---|---|
| `id` | integer | yes | — |
| `title` | string | yes | — |
| `order` | integer | yes | — |
| `lessons` | array of Lesson | no | default `[]` |

</details>

- **422** Validation Error


### PATCH /v1/courses/{course_id}

Edit a course. Owner or admin (status changes go through publish flow).

**Request body**

| Field | Type | Required | Notes |
|---|---|---|---|
| `title` | string *(nullable)* | no | — |
| `description` | string *(nullable)* | no | — |
| `difficulty_level` | string (one of `beginner`, `intermediate`, `advanced`) *(nullable)* | no | — |
| `category` | string *(nullable)* | no | — |
| `outcomes` | array of string *(nullable)* | no | — |
| `target_audience` | string *(nullable)* | no | — |
| `requirements` | string *(nullable)* | no | — |
| `course_type` | string (one of `free`, `paid`) *(nullable)* | no | — |
| `price_naira` | integer *(nullable)* | no | — |
| `thumbnail_key` | string *(nullable)* | no | — |


**Responses**

**200**

| Field | Type | Required | Notes |
|---|---|---|---|
| `id` | integer | yes | — |
| `title` | string | yes | — |
| `description` | string | yes | — |
| `difficulty_level` | string | yes | — |
| `creator_user_id` | integer *(nullable)* | no | — |
| `category` | string | no | default `` |
| `outcomes` | array of string | no | default `[]` |
| `target_audience` | string | no | default `` |
| `requirements` | string | no | default `` |
| `thumbnail_key` | string *(nullable)* | no | — |
| `course_type` | string | no | default `free` |
| `price_naira` | integer | no | default `0` |
| `status` | string | no | default `draft` |
| `lessons` | array of Lesson | no | default `[]` |
| `modules` | array of Module | no | default `[]` |

<details><summary><code>lessons</code> object</summary>

| Field | Type | Required | Notes |
|---|---|---|---|
| `id` | integer | yes | — |
| `order` | integer | yes | — |
| `module_id` | integer *(nullable)* | no | — |
| `title` | string | yes | — |
| `topic` | string | yes | — |
| `description` | string | no | default `` |
| `lesson_type` | string | yes | — |
| `estimated_minutes` | integer | yes | — |
| `content` | string *(nullable)* | no | — |
| `is_published` | boolean | yes | — |

</details>

<details><summary><code>modules</code> object</summary>

| Field | Type | Required | Notes |
|---|---|---|---|
| `id` | integer | yes | — |
| `title` | string | yes | — |
| `order` | integer | yes | — |
| `lessons` | array of Lesson | no | default `[]` |

</details>

- **422** Validation Error


### GET /v1/courses/{course_id}/analytics

Lesson funnel + quiz aggregates for a course. Owner or admin.

**Responses**

**200**

| Field | Type | Required | Notes |
|---|---|---|---|
| `course_id` | integer | yes | — |
| `lessons` | array of LessonFunnel | yes | — |
| `quiz_topics` | array of QuizTopicStats | yes | — |
| `total_views` | integer | yes | — |
| `total_enrollments` | integer | yes | — |
| `total_completions` | integer | yes | — |

<details><summary><code>lessons</code> object</summary>

| Field | Type | Required | Notes |
|---|---|---|---|
| `lesson_id` | integer | yes | — |
| `title` | string | yes | — |
| `order` | integer | yes | — |
| `views` | integer | yes | — |
| `starts` | integer | yes | — |
| `completions` | integer | yes | — |
| `completion_rate` | number | yes | — |

</details>

<details><summary><code>quiz_topics</code> object</summary>

| Field | Type | Required | Notes |
|---|---|---|---|
| `topic` | string | yes | — |
| `attempts` | integer | yes | — |
| `avg_score` | number | yes | — |
| `best_score` | number | yes | — |

</details>

- **422** Validation Error


### GET /v1/courses/{course_id}/lessons

List published lessons (structure always; content gated for paid).

**Responses**

**200**

array of objects

| Field | Type | Required | Notes |
|---|---|---|---|
| `id` | integer | yes | — |
| `order` | integer | yes | — |
| `module_id` | integer *(nullable)* | no | — |
| `title` | string | yes | — |
| `topic` | string | yes | — |
| `description` | string | no | default `` |
| `lesson_type` | string | yes | — |
| `estimated_minutes` | integer | yes | — |
| `content` | string *(nullable)* | no | — |
| `is_published` | boolean | yes | — |

- **422** Validation Error


## Creator Missions

| | Method | Path | Auth | Wired |
|---|---|---|---|---|
| | `GET` | `/v1/creator/missions` | 🔒 user | ✓ |
| | `POST` | `/v1/creator/missions` | 🔒 user | ✓ |
| | `DELETE` | `/v1/creator/missions/{template_id}` | 🔒 user | ✓ |
| | `GET` | `/v1/creator/missions/{template_id}` | 🔒 user | ✓ |
| | `PATCH` | `/v1/creator/missions/{template_id}` | 🔒 user | ✓ |
| | `GET` | `/v1/creator/missions/{template_id}/adoption` | 🔒 user | ✓ |

### GET /v1/creator/missions

Every template the signed-in creator has written, published or not.

**Responses**

**200**

array of objects

| Field | Type | Required | Notes |
|---|---|---|---|
| `id` | integer | yes | — |
| `title` | string | yes | — |
| `description` | string | yes | — |
| `purpose` | string | yes | — |
| `reward_xp` | integer | yes | — |
| `badge` | string *(nullable)* | yes | — |
| `published` | boolean | yes | — |
| `steps` | array of MissionTemplateStep | no | default `[]` |

<details><summary><code>steps</code> object</summary>

| Field | Type | Required | Notes |
|---|---|---|---|
| `id` | integer | yes | — |
| `title` | string | yes | — |
| `description` | string | yes | — |
| `order` | integer | yes | — |

</details>


### POST /v1/creator/missions

Create a mission template. Starts unpublished — set `published` to list it.

**Request body**

| Field | Type | Required | Notes |
|---|---|---|---|
| `title` | string | yes | — |
| `description` | string | no | default `` |
| `purpose` | string | no | default `` |
| `reward_xp` | integer | no | default `100` |
| `badge` | string *(nullable)* | no | — |
| `steps` | array of MissionStepCreate | no | — |

<details><summary><code>steps</code> object</summary>

| Field | Type | Required | Notes |
|---|---|---|---|
| `title` | string | yes | — |
| `description` | string | no | default `` |
| `order` | integer | yes | — |

</details>


**Responses**

**201**

| Field | Type | Required | Notes |
|---|---|---|---|
| `id` | integer | yes | — |
| `title` | string | yes | — |
| `description` | string | yes | — |
| `purpose` | string | yes | — |
| `reward_xp` | integer | yes | — |
| `badge` | string *(nullable)* | yes | — |
| `published` | boolean | yes | — |
| `steps` | array of MissionTemplateStep | no | default `[]` |

<details><summary><code>steps</code> object</summary>

| Field | Type | Required | Notes |
|---|---|---|---|
| `id` | integer | yes | — |
| `title` | string | yes | — |
| `description` | string | yes | — |
| `order` | integer | yes | — |

</details>

- **422** Validation Error


### DELETE /v1/creator/missions/{template_id}

Withdraw a template from the catalogue.

Learners who already adopted it keep their mission, because adoption
copies the content — nobody's work in progress is cancelled.

**Responses**

**200**

object

- **422** Validation Error


### GET /v1/creator/missions/{template_id}

One of the creator's own templates.

**Responses**

**200**

| Field | Type | Required | Notes |
|---|---|---|---|
| `id` | integer | yes | — |
| `title` | string | yes | — |
| `description` | string | yes | — |
| `purpose` | string | yes | — |
| `reward_xp` | integer | yes | — |
| `badge` | string *(nullable)* | yes | — |
| `published` | boolean | yes | — |
| `steps` | array of MissionTemplateStep | no | default `[]` |

<details><summary><code>steps</code> object</summary>

| Field | Type | Required | Notes |
|---|---|---|---|
| `id` | integer | yes | — |
| `title` | string | yes | — |
| `description` | string | yes | — |
| `order` | integer | yes | — |

</details>

- **422** Validation Error


### PATCH /v1/creator/missions/{template_id}

Edit a template.

Existing adopted missions keep the wording they were adopted with: a
learner part-way through "Build a button" should not find it renamed
tomorrow. Only later adopters see the new text.

**Request body**

| Field | Type | Required | Notes |
|---|---|---|---|
| `title` | string *(nullable)* | no | — |
| `description` | string *(nullable)* | no | — |
| `purpose` | string *(nullable)* | no | — |
| `reward_xp` | integer *(nullable)* | no | — |
| `badge` | string *(nullable)* | no | — |
| `published` | boolean *(nullable)* | no | — |
| `steps` | array of MissionStepCreate *(nullable)* | no | — |


**Responses**

**200**

| Field | Type | Required | Notes |
|---|---|---|---|
| `id` | integer | yes | — |
| `title` | string | yes | — |
| `description` | string | yes | — |
| `purpose` | string | yes | — |
| `reward_xp` | integer | yes | — |
| `badge` | string *(nullable)* | yes | — |
| `published` | boolean | yes | — |
| `steps` | array of MissionTemplateStep | no | default `[]` |

<details><summary><code>steps</code> object</summary>

| Field | Type | Required | Notes |
|---|---|---|---|
| `id` | integer | yes | — |
| `title` | string | yes | — |
| `description` | string | yes | — |
| `order` | integer | yes | — |

</details>

- **422** Validation Error


### GET /v1/creator/missions/{template_id}/adoption

How many learners took this mission, and how many finished it.

**Responses**

**200**

object

- **422** Validation Error


## Creators

| | Method | Path | Auth | Wired |
|---|---|---|---|---|
| | `GET` | `/v1/creators/{user_id}/profile` | — public | — |
| | `GET` | `/v1/me/creator/activity` | 🔒 user | ✓ |
| | `GET` | `/v1/me/creator/analytics/overview` | 🔒 user | ✓ |
| | `GET` | `/v1/me/creator/courses` | 🔒 user | ✓ |
| | `GET` | `/v1/me/creator/dashboard` | 🔒 user | ✓ |
| | `GET` | `/v1/me/creator/profile` | 🔒 user | ✓ |
| | `PUT` | `/v1/me/creator/profile` | 🔒 user | ✓ |

### GET /v1/creators/{user_id}/profile

Public creator profile for course pages.

**Responses**

**200**

| Field | Type | Required | Notes |
|---|---|---|---|
| `user_id` | integer | yes | — |
| `display_name` | string | yes | — |
| `bio` | string | no | default `` |
| `image_key` | string *(nullable)* | no | — |

- **422** Validation Error


### GET /v1/me/creator/activity

Recent analytics events across the creator's own courses.

**Responses**

**200**

array of objects

| Field | Type | Required | Notes |
|---|---|---|---|
| `event` | string | yes | — |
| `course_id` | integer *(nullable)* | no | — |
| `lesson_id` | integer *(nullable)* | no | — |
| `created_at` | string (date-time) | yes | — |

- **422** Validation Error


### GET /v1/me/creator/analytics/overview

Daily enrollments/purchases/revenue/completions across own courses.

**Responses**

**200**

| Field | Type | Required | Notes |
|---|---|---|---|
| `days` | integer | yes | — |
| `total_enrollments` | integer | yes | — |
| `total_purchases` | integer | yes | — |
| `total_revenue_naira` | integer | yes | — |
| `total_completions` | integer | yes | — |
| `active_learners` | integer | yes | — |
| `daily` | array of DailyPoint | yes | — |

<details><summary><code>daily</code> object</summary>

| Field | Type | Required | Notes |
|---|---|---|---|
| `date` | string | yes | — |
| `enrollments` | integer | yes | — |
| `purchases` | integer | yes | — |
| `revenue_naira` | integer | yes | — |
| `completions` | integer | yes | — |

</details>

- **422** Validation Error


### GET /v1/me/creator/courses

The creator's own courses in any status (drafts included).

**Responses**

**200**

array of object


### GET /v1/me/creator/dashboard

Creator stats: courses, learners, revenue, recent activity.

**Responses**

**200**

| Field | Type | Required | Notes |
|---|---|---|---|
| `total_courses` | integer | yes | — |
| `published_courses` | integer | yes | — |
| `draft_courses` | integer | yes | — |
| `total_learners` | integer | yes | — |
| `total_revenue_naira` | integer | no | default `0` |
| `recent_activity` | array of ActivityEntry | no | default `[]` |

<details><summary><code>recent_activity</code> object</summary>

| Field | Type | Required | Notes |
|---|---|---|---|
| `event` | string | yes | — |
| `course_id` | integer *(nullable)* | no | — |
| `lesson_id` | integer *(nullable)* | no | — |
| `created_at` | string (date-time) | yes | — |

</details>


### GET /v1/me/creator/profile

Get the creator's own profile.

**Responses**

**200**

| Field | Type | Required | Notes |
|---|---|---|---|
| `user_id` | integer | yes | — |
| `display_name` | string | yes | — |
| `bio` | string | no | default `` |
| `image_key` | string *(nullable)* | no | — |


### PUT /v1/me/creator/profile

Create or replace the creator's public profile.

**Request body**

| Field | Type | Required | Notes |
|---|---|---|---|
| `display_name` | string | yes | — |
| `bio` | string | no | default `` |
| `image_key` | string *(nullable)* | no | — |


**Responses**

**200**

| Field | Type | Required | Notes |
|---|---|---|---|
| `user_id` | integer | yes | — |
| `display_name` | string | yes | — |
| `bio` | string | no | default `` |
| `image_key` | string *(nullable)* | no | — |

- **422** Validation Error


## Curriculum

| | Method | Path | Auth | Wired |
|---|---|---|---|---|
| | `POST` | `/v1/courses/{course_id}/archive` | 🔒 user | ✓ |
| | `POST` | `/v1/courses/{course_id}/lessons` | 🔒 user | ✓ |
| | `PUT` | `/v1/courses/{course_id}/lessons/reorder` | 🔒 user | ✓ |
| | `POST` | `/v1/courses/{course_id}/modules` | 🔒 user | ✓ |
| | `PUT` | `/v1/courses/{course_id}/modules/reorder` | 🔒 user | ✓ |
| | `DELETE` | `/v1/courses/{course_id}/modules/{module_id}` | 🔒 user | ✓ |
| | `PATCH` | `/v1/courses/{course_id}/modules/{module_id}` | 🔒 user | ✓ |
| | `GET` | `/v1/courses/{course_id}/preview` | 🔒 user | ✓ |
| | `POST` | `/v1/courses/{course_id}/publish` | 🔒 user | ✓ |
| | `GET` | `/v1/courses/{course_id}/publish-check` | 🔒 user | ✓ |
| | `POST` | `/v1/courses/{course_id}/unpublish` | 🔒 user | ✓ |
| | `DELETE` | `/v1/lessons/{lesson_id}` | 🔒 user | ✓ |
| | `GET` | `/v1/lessons/{lesson_id}` | ○ optional | ✓ |
| | `PATCH` | `/v1/lessons/{lesson_id}` | 🔒 user | ✓ |
| | `GET` | `/v1/lessons/{lesson_id}/assets` | ○ optional | ✓ |
| | `POST` | `/v1/lessons/{lesson_id}/assets` | 🔒 user | ✓ |
| | `DELETE` | `/v1/lessons/{lesson_id}/assets/{asset_id}` | 🔒 user | ✓ |

### POST /v1/courses/{course_id}/archive

Archive a course (hidden from discovery, kept for records).

**Request body:** none

**Responses**

**200**

| Field | Type | Required | Notes |
|---|---|---|---|
| `id` | integer | yes | — |
| `title` | string | yes | — |
| `description` | string | yes | — |
| `difficulty_level` | string | yes | — |
| `creator_user_id` | integer *(nullable)* | no | — |
| `category` | string | no | default `` |
| `outcomes` | array of string | no | default `[]` |
| `target_audience` | string | no | default `` |
| `requirements` | string | no | default `` |
| `thumbnail_key` | string *(nullable)* | no | — |
| `course_type` | string | no | default `free` |
| `price_naira` | integer | no | default `0` |
| `status` | string | no | default `draft` |
| `lessons` | array of Lesson | no | default `[]` |
| `modules` | array of Module | no | default `[]` |

<details><summary><code>lessons</code> object</summary>

| Field | Type | Required | Notes |
|---|---|---|---|
| `id` | integer | yes | — |
| `order` | integer | yes | — |
| `module_id` | integer *(nullable)* | no | — |
| `title` | string | yes | — |
| `topic` | string | yes | — |
| `description` | string | no | default `` |
| `lesson_type` | string | yes | — |
| `estimated_minutes` | integer | yes | — |
| `content` | string *(nullable)* | no | — |
| `is_published` | boolean | yes | — |

</details>

<details><summary><code>modules</code> object</summary>

| Field | Type | Required | Notes |
|---|---|---|---|
| `id` | integer | yes | — |
| `title` | string | yes | — |
| `order` | integer | yes | — |
| `lessons` | array of Lesson | no | default `[]` |

</details>

- **422** Validation Error


### POST /v1/courses/{course_id}/lessons

Add a lesson to a module (or the unassigned pool); order defaults to last.

**Request body**

| Field | Type | Required | Notes |
|---|---|---|---|
| `module_id` | integer *(nullable)* | no | — |
| `order` | integer *(nullable)* | no | — |
| `title` | string | yes | — |
| `topic` | string | yes | — |
| `description` | string | no | default `` |
| `lesson_type` | string | no | default `lesson` |
| `estimated_minutes` | integer | no | default `15` |
| `content` | string | no | default `` |
| `is_published` | boolean | no | default `True` |


**Responses**

**201**

| Field | Type | Required | Notes |
|---|---|---|---|
| `id` | integer | yes | — |
| `order` | integer | yes | — |
| `module_id` | integer *(nullable)* | no | — |
| `title` | string | yes | — |
| `topic` | string | yes | — |
| `description` | string | no | default `` |
| `lesson_type` | string | yes | — |
| `estimated_minutes` | integer | yes | — |
| `content` | string *(nullable)* | no | — |
| `is_published` | boolean | yes | — |

- **422** Validation Error


### PUT /v1/courses/{course_id}/lessons/reorder

Order lessons within a module (or the unassigned pool when omitted).

**Request body**

| Field | Type | Required | Notes |
|---|---|---|---|
| `ordered_ids` | array of integer | yes | — |


**Responses**

**200**

object

- **422** Validation Error


### POST /v1/courses/{course_id}/modules

Append a module to the end of the course curriculum.

**Request body**

| Field | Type | Required | Notes |
|---|---|---|---|
| `title` | string | yes | — |


**Responses**

**201**

| Field | Type | Required | Notes |
|---|---|---|---|
| `id` | integer | yes | — |
| `title` | string | yes | — |
| `order` | integer | yes | — |
| `lessons` | array of Lesson | no | default `[]` |

<details><summary><code>lessons</code> object</summary>

| Field | Type | Required | Notes |
|---|---|---|---|
| `id` | integer | yes | — |
| `order` | integer | yes | — |
| `module_id` | integer *(nullable)* | no | — |
| `title` | string | yes | — |
| `topic` | string | yes | — |
| `description` | string | no | default `` |
| `lesson_type` | string | yes | — |
| `estimated_minutes` | integer | yes | — |
| `content` | string *(nullable)* | no | — |
| `is_published` | boolean | yes | — |

</details>

- **422** Validation Error


### PUT /v1/courses/{course_id}/modules/reorder

Set module order; ordered_ids must match the course's modules exactly.

**Request body**

| Field | Type | Required | Notes |
|---|---|---|---|
| `ordered_ids` | array of integer | yes | — |


**Responses**

**200**

object

- **422** Validation Error


### DELETE /v1/courses/{course_id}/modules/{module_id}

Delete a module; its lessons become unassigned (not deleted).

**Responses**

**200**

object

- **422** Validation Error


### PATCH /v1/courses/{course_id}/modules/{module_id}

Rename a module.

**Request body**

| Field | Type | Required | Notes |
|---|---|---|---|
| `title` | string *(nullable)* | no | — |


**Responses**

**200**

| Field | Type | Required | Notes |
|---|---|---|---|
| `id` | integer | yes | — |
| `title` | string | yes | — |
| `order` | integer | yes | — |
| `lessons` | array of Lesson | no | default `[]` |

<details><summary><code>lessons</code> object</summary>

| Field | Type | Required | Notes |
|---|---|---|---|
| `id` | integer | yes | — |
| `order` | integer | yes | — |
| `module_id` | integer *(nullable)* | no | — |
| `title` | string | yes | — |
| `topic` | string | yes | — |
| `description` | string | no | default `` |
| `lesson_type` | string | yes | — |
| `estimated_minutes` | integer | yes | — |
| `content` | string *(nullable)* | no | — |
| `is_published` | boolean | yes | — |

</details>

- **422** Validation Error


### GET /v1/courses/{course_id}/preview

Student-view preview of a course (works on drafts; editing resumes after).

**Responses**

**200**

| Field | Type | Required | Notes |
|---|---|---|---|
| `id` | integer | yes | — |
| `title` | string | yes | — |
| `description` | string | yes | — |
| `difficulty_level` | string | yes | — |
| `creator_user_id` | integer *(nullable)* | no | — |
| `category` | string | no | default `` |
| `outcomes` | array of string | no | default `[]` |
| `target_audience` | string | no | default `` |
| `requirements` | string | no | default `` |
| `thumbnail_key` | string *(nullable)* | no | — |
| `course_type` | string | no | default `free` |
| `price_naira` | integer | no | default `0` |
| `status` | string | no | default `draft` |
| `lessons` | array of Lesson | no | default `[]` |
| `modules` | array of Module | no | default `[]` |

<details><summary><code>lessons</code> object</summary>

| Field | Type | Required | Notes |
|---|---|---|---|
| `id` | integer | yes | — |
| `order` | integer | yes | — |
| `module_id` | integer *(nullable)* | no | — |
| `title` | string | yes | — |
| `topic` | string | yes | — |
| `description` | string | no | default `` |
| `lesson_type` | string | yes | — |
| `estimated_minutes` | integer | yes | — |
| `content` | string *(nullable)* | no | — |
| `is_published` | boolean | yes | — |

</details>

<details><summary><code>modules</code> object</summary>

| Field | Type | Required | Notes |
|---|---|---|---|
| `id` | integer | yes | — |
| `title` | string | yes | — |
| `order` | integer | yes | — |
| `lessons` | array of Lesson | no | default `[]` |

</details>

- **422** Validation Error


### POST /v1/courses/{course_id}/publish

Publish a course after validation; invalid courses get 422 + errors.

**Request body:** none

**Responses**

**200**

| Field | Type | Required | Notes |
|---|---|---|---|
| `id` | integer | yes | — |
| `title` | string | yes | — |
| `description` | string | yes | — |
| `difficulty_level` | string | yes | — |
| `creator_user_id` | integer *(nullable)* | no | — |
| `category` | string | no | default `` |
| `outcomes` | array of string | no | default `[]` |
| `target_audience` | string | no | default `` |
| `requirements` | string | no | default `` |
| `thumbnail_key` | string *(nullable)* | no | — |
| `course_type` | string | no | default `free` |
| `price_naira` | integer | no | default `0` |
| `status` | string | no | default `draft` |
| `lessons` | array of Lesson | no | default `[]` |
| `modules` | array of Module | no | default `[]` |

<details><summary><code>lessons</code> object</summary>

| Field | Type | Required | Notes |
|---|---|---|---|
| `id` | integer | yes | — |
| `order` | integer | yes | — |
| `module_id` | integer *(nullable)* | no | — |
| `title` | string | yes | — |
| `topic` | string | yes | — |
| `description` | string | no | default `` |
| `lesson_type` | string | yes | — |
| `estimated_minutes` | integer | yes | — |
| `content` | string *(nullable)* | no | — |
| `is_published` | boolean | yes | — |

</details>

<details><summary><code>modules</code> object</summary>

| Field | Type | Required | Notes |
|---|---|---|---|
| `id` | integer | yes | — |
| `title` | string | yes | — |
| `order` | integer | yes | — |
| `lessons` | array of Lesson | no | default `[]` |

</details>

- **422** Validation Error


### GET /v1/courses/{course_id}/publish-check

Dry-run the publish validation (powers UI validation states).

**Responses**

**200**

| Field | Type | Required | Notes |
|---|---|---|---|
| `publishable` | boolean | yes | — |
| `errors` | array of string | no | default `[]` |

- **422** Validation Error


### POST /v1/courses/{course_id}/unpublish

Return a course to draft (hides it from discovery).

**Request body:** none

**Responses**

**200**

| Field | Type | Required | Notes |
|---|---|---|---|
| `id` | integer | yes | — |
| `title` | string | yes | — |
| `description` | string | yes | — |
| `difficulty_level` | string | yes | — |
| `creator_user_id` | integer *(nullable)* | no | — |
| `category` | string | no | default `` |
| `outcomes` | array of string | no | default `[]` |
| `target_audience` | string | no | default `` |
| `requirements` | string | no | default `` |
| `thumbnail_key` | string *(nullable)* | no | — |
| `course_type` | string | no | default `free` |
| `price_naira` | integer | no | default `0` |
| `status` | string | no | default `draft` |
| `lessons` | array of Lesson | no | default `[]` |
| `modules` | array of Module | no | default `[]` |

<details><summary><code>lessons</code> object</summary>

| Field | Type | Required | Notes |
|---|---|---|---|
| `id` | integer | yes | — |
| `order` | integer | yes | — |
| `module_id` | integer *(nullable)* | no | — |
| `title` | string | yes | — |
| `topic` | string | yes | — |
| `description` | string | no | default `` |
| `lesson_type` | string | yes | — |
| `estimated_minutes` | integer | yes | — |
| `content` | string *(nullable)* | no | — |
| `is_published` | boolean | yes | — |

</details>

<details><summary><code>modules</code> object</summary>

| Field | Type | Required | Notes |
|---|---|---|---|
| `id` | integer | yes | — |
| `title` | string | yes | — |
| `order` | integer | yes | — |
| `lessons` | array of Lesson | no | default `[]` |

</details>

- **422** Validation Error


### DELETE /v1/lessons/{lesson_id}

Delete a lesson with its learner progress rows.

**Responses**

**200**

object

- **422** Validation Error


### GET /v1/lessons/{lesson_id}

Lesson detail. Structure is public for published courses; paid
content needs a purchase enrollment (owner/admin always pass).

**Responses**

**200**

| Field | Type | Required | Notes |
|---|---|---|---|
| `id` | integer | yes | — |
| `order` | integer | yes | — |
| `module_id` | integer *(nullable)* | no | — |
| `title` | string | yes | — |
| `topic` | string | yes | — |
| `description` | string | no | default `` |
| `lesson_type` | string | yes | — |
| `estimated_minutes` | integer | yes | — |
| `content` | string *(nullable)* | no | — |
| `is_published` | boolean | yes | — |

- **422** Validation Error


### PATCH /v1/lessons/{lesson_id}

Edit a lesson; module moves must stay inside the same course.

**Request body**

| Field | Type | Required | Notes |
|---|---|---|---|
| `module_id` | integer *(nullable)* | no | — |
| `order` | integer *(nullable)* | no | — |
| `title` | string *(nullable)* | no | — |
| `topic` | string *(nullable)* | no | — |
| `description` | string *(nullable)* | no | — |
| `lesson_type` | string *(nullable)* | no | — |
| `estimated_minutes` | integer *(nullable)* | no | — |
| `content` | string *(nullable)* | no | — |
| `is_published` | boolean *(nullable)* | no | — |


**Responses**

**200**

| Field | Type | Required | Notes |
|---|---|---|---|
| `id` | integer | yes | — |
| `order` | integer | yes | — |
| `module_id` | integer *(nullable)* | no | — |
| `title` | string | yes | — |
| `topic` | string | yes | — |
| `description` | string | no | default `` |
| `lesson_type` | string | yes | — |
| `estimated_minutes` | integer | yes | — |
| `content` | string *(nullable)* | no | — |
| `is_published` | boolean | yes | — |

- **422** Validation Error


### GET /v1/lessons/{lesson_id}/assets

List a lesson's assets. Paid content needs a purchase enrollment.

**Responses**

**200**

array of objects

| Field | Type | Required | Notes |
|---|---|---|---|
| `id` | integer | yes | — |
| `kind` | string | yes | — |
| `storage_key` | string *(nullable)* | no | — |
| `url` | string *(nullable)* | no | — |
| `filename` | string | no | default `` |
| `size_bytes` | integer | no | default `0` |
| `scan_status` | string | no | default `unscanned` |
| `scan_detail` | string | no | default `` |

- **422** Validation Error


### POST /v1/lessons/{lesson_id}/assets

Attach a video/resource/link to a lesson. Owner or admin.

For files, this is where the upload is checked: the real size comes from
the storage provider rather than the request, the malware scan verdict is
recorded, and an object that fails either is deleted and refused. A
client-supplied size_bytes is no longer accepted at all — it was never
more than a claim.

**Request body**

| Field | Type | Required | Notes |
|---|---|---|---|
| `kind` | string | yes | — |
| `storage_key` | string *(nullable)* | no | — |
| `url` | string *(nullable)* | no | — |
| `filename` | string | no | default `` |


**Responses**

**201**

| Field | Type | Required | Notes |
|---|---|---|---|
| `id` | integer | yes | — |
| `kind` | string | yes | — |
| `storage_key` | string *(nullable)* | no | — |
| `url` | string *(nullable)* | no | — |
| `filename` | string | no | default `` |
| `size_bytes` | integer | no | default `0` |
| `scan_status` | string | no | default `unscanned` |
| `scan_detail` | string | no | default `` |

- **422** Validation Error


### DELETE /v1/lessons/{lesson_id}/assets/{asset_id}

Detach an asset from a lesson (object cleanup in S3 is out of scope).

**Responses**

**200**

object

- **422** Validation Error


## Learning Paths

| | Method | Path | Auth | Wired |
|---|---|---|---|---|
| | `GET` | `/v1/me/paths` | 🔒 user | — |
| | `POST` | `/v1/me/paths` | 🔒 user | — |
| | `DELETE` | `/v1/me/paths/{path_id}` | 🔒 user | — |
| | `GET` | `/v1/me/paths/{path_id}` | 🔒 user | — |
| | `PUT` | `/v1/me/paths/{path_id}` | 🔒 user | — |

### GET /v1/me/paths

The learner's own paths, newest first, each with progress.

**Responses**

**200**

array of objects

| Field | Type | Required | Notes |
|---|---|---|---|
| `id` | integer | yes | — |
| `title` | string | yes | — |
| `description` | string | no | default `` |
| `steps` | array of PathStep | no | default `[]` |
| `courses_total` | integer | no | default `0` |
| `courses_completed` | integer | no | default `0` |
| `completion_percent` | number | no | default `0.0` |
| `created_at` | string (date-time) *(nullable)* | no | — |
| `updated_at` | string (date-time) *(nullable)* | no | — |

<details><summary><code>steps</code> object</summary>

| Field | Type | Required | Notes |
|---|---|---|---|
| `position` | integer | yes | — |
| `course_id` | integer | yes | — |
| `title` | string | yes | — |
| `difficulty_level` | string | no | default `` |
| `lessons_total` | integer | no | default `0` |
| `status` | string (one of `completed`, `in_progress`, `not_started`) | yes | — |

</details>


### POST /v1/me/paths

Save an ordered path over published courses.

**Request body**

| Field | Type | Required | Notes |
|---|---|---|---|
| `title` | string | yes | — |
| `description` | string | no | default `` |
| `course_ids` | array of integer | yes | — |


**Responses**

**201**

| Field | Type | Required | Notes |
|---|---|---|---|
| `id` | integer | yes | — |
| `title` | string | yes | — |
| `description` | string | no | default `` |
| `steps` | array of PathStep | no | default `[]` |
| `courses_total` | integer | no | default `0` |
| `courses_completed` | integer | no | default `0` |
| `completion_percent` | number | no | default `0.0` |
| `created_at` | string (date-time) *(nullable)* | no | — |
| `updated_at` | string (date-time) *(nullable)* | no | — |

<details><summary><code>steps</code> object</summary>

| Field | Type | Required | Notes |
|---|---|---|---|
| `position` | integer | yes | — |
| `course_id` | integer | yes | — |
| `title` | string | yes | — |
| `difficulty_level` | string | no | default `` |
| `lessons_total` | integer | no | default `0` |
| `status` | string (one of `completed`, `in_progress`, `not_started`) | yes | — |

</details>

- **422** Validation Error


### DELETE /v1/me/paths/{path_id}

Delete a path. Steps go with it through the CASCADE; enrollments and
progress are untouched — a plan is not the work.

**Responses**

**200**

object

- **422** Validation Error


### GET /v1/me/paths/{path_id}

One path with per-step status.

**Responses**

**200**

| Field | Type | Required | Notes |
|---|---|---|---|
| `id` | integer | yes | — |
| `title` | string | yes | — |
| `description` | string | no | default `` |
| `steps` | array of PathStep | no | default `[]` |
| `courses_total` | integer | no | default `0` |
| `courses_completed` | integer | no | default `0` |
| `completion_percent` | number | no | default `0.0` |
| `created_at` | string (date-time) *(nullable)* | no | — |
| `updated_at` | string (date-time) *(nullable)* | no | — |

<details><summary><code>steps</code> object</summary>

| Field | Type | Required | Notes |
|---|---|---|---|
| `position` | integer | yes | — |
| `course_id` | integer | yes | — |
| `title` | string | yes | — |
| `difficulty_level` | string | no | default `` |
| `lessons_total` | integer | no | default `0` |
| `status` | string (one of `completed`, `in_progress`, `not_started`) | yes | — |

</details>

- **422** Validation Error


### PUT /v1/me/paths/{path_id}

Rename, re-describe, or replace the course list wholesale.

**Request body**

| Field | Type | Required | Notes |
|---|---|---|---|
| `title` | string *(nullable)* | no | — |
| `description` | string *(nullable)* | no | — |
| `course_ids` | array of integer *(nullable)* | no | — |


**Responses**

**200**

| Field | Type | Required | Notes |
|---|---|---|---|
| `id` | integer | yes | — |
| `title` | string | yes | — |
| `description` | string | no | default `` |
| `steps` | array of PathStep | no | default `[]` |
| `courses_total` | integer | no | default `0` |
| `courses_completed` | integer | no | default `0` |
| `completion_percent` | number | no | default `0.0` |
| `created_at` | string (date-time) *(nullable)* | no | — |
| `updated_at` | string (date-time) *(nullable)* | no | — |

<details><summary><code>steps</code> object</summary>

| Field | Type | Required | Notes |
|---|---|---|---|
| `position` | integer | yes | — |
| `course_id` | integer | yes | — |
| `title` | string | yes | — |
| `difficulty_level` | string | no | default `` |
| `lessons_total` | integer | no | default `0` |
| `status` | string (one of `completed`, `in_progress`, `not_started`) | yes | — |

</details>

- **422** Validation Error


## Library

| | Method | Path | Auth | Wired |
|---|---|---|---|---|
| | `GET` | `/v1/me/materials` | 🔒 user | — |
| | `POST` | `/v1/me/materials` | 🔒 user | — |
| | `POST` | `/v1/me/materials/presigned` | 🔒 user | — |
| | `DELETE` | `/v1/me/materials/{material_id}` | 🔒 user | — |
| | `GET` | `/v1/me/materials/{material_id}/analysis` | 🔒 user | — |
| | `POST` | `/v1/me/materials/{material_id}/analyze` | 🔒 user | — |
| | `POST` | `/v1/me/materials/{material_id}/days/{day_number}/complete` | 🔒 user | — |
| | `GET` | `/v1/me/materials/{material_id}/payment` | 🔒 user | — |
| | `POST` | `/v1/me/materials/{material_id}/plan` | 🔒 user | — |
| | `POST` | `/v1/me/materials/{material_id}/purchase` | 🔒 user | — |
| | `GET` | `/v1/me/materials/{material_id}/schedule` | 🔒 user | — |
| | `POST` | `/v1/me/materials/{material_id}/verify` | 🔒 user | — |

### GET /v1/me/materials

The learner's own library, newest first.

**Responses**

**200**

array of objects

| Field | Type | Required | Notes |
|---|---|---|---|
| `id` | integer | yes | — |
| `filename` | string | no | default `` |
| `storage_key` | string | no | default `` |
| `content_type` | string | no | default `` |
| `size_bytes` | integer | no | default `0` |
| `scan_status` | string | no | default `unscanned` |
| `scan_detail` | string | no | default `` |


### POST /v1/me/materials

Claim a finished upload into the library.

`claim` enforces the purpose prefix, so a key minted for any other use
cannot end up here. Claiming is what stops the orphan sweep from deleting
the object as abandoned.

**Request body**

| Field | Type | Required | Notes |
|---|---|---|---|
| `storage_key` | string | yes | — |


**Responses**

**201**

| Field | Type | Required | Notes |
|---|---|---|---|
| `id` | integer | yes | — |
| `filename` | string | no | default `` |
| `storage_key` | string | no | default `` |
| `content_type` | string | no | default `` |
| `size_bytes` | integer | no | default `0` |
| `scan_status` | string | no | default `unscanned` |
| `scan_detail` | string | no | default `` |

- **422** Validation Error


### POST /v1/me/materials/presigned

Mint a presigned upload form for one library document.

Same guarantees as the creator form: the server chooses the key and the
provider enforces the size cap, so a client holding this URL cannot exceed
it.

**Request body**

| Field | Type | Required | Notes |
|---|---|---|---|
| `filename` | string | yes | — |
| `content_type` | string | yes | — |
| `size_bytes` | integer | no | default `0` |


**Responses**

**201**

| Field | Type | Required | Notes |
|---|---|---|---|
| `upload_url` | string | yes | — |
| `fields` | object | yes | — |
| `storage_key` | string | yes | — |
| `expires_in` | integer | yes | — |
| `max_bytes` | integer | yes | — |
| `method` | string | no | default `POST` |

- **422** Validation Error


### DELETE /v1/me/materials/{material_id}

Remove a library entry. 404 for anyone else's: the existence of
another learner's files is not something to confirm.

Object cleanup in storage is out of scope, matching lesson assets — the
orphan sweep collects the unreferenced key within a day.

**Responses**

**200**

object

- **422** Validation Error


### GET /v1/me/materials/{material_id}/analysis

Re-read the preview (step 5 is a screen the learner returns to).

**Responses**

**200**

| Field | Type | Required | Notes |
|---|---|---|---|
| `topics` | array of string | no | default `[]` |
| `objectives` | array of string | no | default `[]` |
| `estimated_minutes` | integer | no | default `0` |
| `summary` | string | no | default `` |
| `purpose` | string | no | default `` |
| `timeline_days` | integer *(nullable)* | no | — |

- **422** Validation Error


### POST /v1/me/materials/{material_id}/analyze

Read the document and preview what it contains: topics, objectives,
study time. Free — this is the "here's what we found" screen, and the
paywall comes after it, not before it.

Repeatable: a fresh analysis replaces the previous one.

**Request body:** none

**Responses**

**200**

| Field | Type | Required | Notes |
|---|---|---|---|
| `topics` | array of string | no | default `[]` |
| `objectives` | array of string | no | default `[]` |
| `estimated_minutes` | integer | no | default `0` |
| `summary` | string | no | default `` |
| `purpose` | string | no | default `` |
| `timeline_days` | integer *(nullable)* | no | — |

- **422** Validation Error


### POST /v1/me/materials/{material_id}/days/{day_number}/complete

Mark one day done. Idempotent: re-completing is a no-op, not an error.

**Request body:** none

**Responses**

**200**

object

- **422** Validation Error


### GET /v1/me/materials/{material_id}/payment

Current state of the unlock payment. The callback page polls this.

**Responses**

**200**

| Field | Type | Required | Notes |
|---|---|---|---|
| `reference` | string | yes | — |
| `status` | string | yes | — |
| `unlocked` | boolean | yes | — |
| `amount_naira` | integer | yes | — |

- **422** Validation Error


### POST /v1/me/materials/{material_id}/plan

Steps 3+4 of the loop: purpose and timeline. Requires the analysis
first — intent without a preview has nothing to attach to. Repeatable:
changing your mind re-shapes the schedule generated later, not the
preview itself.

**Request body**

| Field | Type | Required | Notes |
|---|---|---|---|
| `purpose` | string | yes | — |
| `days` | integer | yes | — |


**Responses**

**200**

| Field | Type | Required | Notes |
|---|---|---|---|
| `topics` | array of string | no | default `[]` |
| `objectives` | array of string | no | default `[]` |
| `estimated_minutes` | integer | no | default `0` |
| `summary` | string | no | default `` |
| `purpose` | string | no | default `` |
| `timeline_days` | integer *(nullable)* | no | — |

- **422** Validation Error


### POST /v1/me/materials/{material_id}/purchase

Begin unlocking the 14-day schedule for a material.

Analysis comes first (409 otherwise): the paywall follows the preview by
design, and generation needs the analysis row anyway. Mirrors the course
purchase flow otherwise — pending payment plus checkout URL on Paystack,
inline settlement on the stub.

**Request body:** none

**Responses**

**200**

| Field | Type | Required | Notes |
|---|---|---|---|
| `reference` | string | yes | — |
| `checkout_url` | string *(nullable)* | no | — |
| `unlocked` | boolean | no | default `False` |
| `amount_naira` | integer | yes | — |

- **422** Validation Error


### GET /v1/me/materials/{material_id}/schedule

The unlocked plan, generating it on first read after payment.

402 until paid: the schedule is the product, and reads stay honest
about that. Permanent once unlocked — the 14 days shape the plan,
never gate it, so no expiry is checked here.

**Responses**

**200**

| Field | Type | Required | Notes |
|---|---|---|---|
| `id` | integer | yes | — |
| `material_id` | integer | yes | — |
| `title` | string | no | default `` |
| `purpose` | string | no | default `` |
| `days` | array of ScheduleDay | no | default `[]` |
| `days_total` | integer | no | default `0` |
| `days_completed` | integer | no | default `0` |
| `completion_percent` | number | no | default `0.0` |
| `created_at` | string (date-time) *(nullable)* | no | — |

<details><summary><code>days</code> object</summary>

| Field | Type | Required | Notes |
|---|---|---|---|
| `day` | integer | yes | — |
| `title` | string | no | default `` |
| `objectives` | array of string | no | default `[]` |
| `tasks` | array of string | no | default `[]` |
| `completed` | boolean | no | default `False` |
| `completed_at` | string (date-time) *(nullable)* | no | — |

</details>

- **422** Validation Error


### POST /v1/me/materials/{material_id}/verify

Ask Paystack whether this unlock actually completed.

The reference from the URL is a hint only — the provider is the
authority, and the payment must belong to the signed-in learner.

**Request body:** none

**Responses**

**200**

| Field | Type | Required | Notes |
|---|---|---|---|
| `reference` | string | yes | — |
| `status` | string | yes | — |
| `unlocked` | boolean | yes | — |
| `amount_naira` | integer | yes | — |

- **422** Validation Error


## Live

| | Method | Path | Auth | Wired |
|---|---|---|---|---|
| | `GET` | `/v1/courses/{course_id}/live` | ○ optional | ✓ |
| | `POST` | `/v1/courses/{course_id}/live` | 🔒 user | ✓ |
| | `GET` | `/v1/live/{session_id}` | ○ optional | ✓ |
| | `POST` | `/v1/live/{session_id}/cancel` | 🔒 user | ✓ |
| | `POST` | `/v1/live/{session_id}/end` | 🔒 user | ✓ |
| | `POST` | `/v1/live/{session_id}/start` | 🔒 user | ✓ |

### GET /v1/courses/{course_id}/live

Upcoming/live classes. Draft courses visible to owner/admin only.

**Responses**

**200**

array of objects

| Field | Type | Required | Notes |
|---|---|---|---|
| `id` | integer | yes | — |
| `course_id` | integer | yes | — |
| `title` | string | yes | — |
| `scheduled_at` | string (date-time) | yes | — |
| `duration_minutes` | integer | yes | — |
| `meeting_url` | string *(nullable)* | no | — |
| `status` | string | yes | — |
| `is_recorded` | boolean | no | default `False` |

- **422** Validation Error


### POST /v1/courses/{course_id}/live

Schedule a live class on a course. Owner or admin.

**Request body**

| Field | Type | Required | Notes |
|---|---|---|---|
| `title` | string | yes | — |
| `scheduled_at` | string (date-time) | yes | — |
| `duration_minutes` | integer | no | default `60` |
| `meeting_url` | string | no | default `` |


**Responses**

**201**

| Field | Type | Required | Notes |
|---|---|---|---|
| `id` | integer | yes | — |
| `course_id` | integer | yes | — |
| `title` | string | yes | — |
| `scheduled_at` | string (date-time) | yes | — |
| `duration_minutes` | integer | yes | — |
| `meeting_url` | string *(nullable)* | no | — |
| `status` | string | yes | — |
| `is_recorded` | boolean | no | default `False` |

- **422** Validation Error


### GET /v1/live/{session_id}

Live class detail; the join link is hidden unless entitled.

**Responses**

**200**

| Field | Type | Required | Notes |
|---|---|---|---|
| `id` | integer | yes | — |
| `course_id` | integer | yes | — |
| `title` | string | yes | — |
| `scheduled_at` | string (date-time) | yes | — |
| `duration_minutes` | integer | yes | — |
| `meeting_url` | string *(nullable)* | no | — |
| `status` | string | yes | — |
| `is_recorded` | boolean | no | default `False` |

- **422** Validation Error


### POST /v1/live/{session_id}/cancel

Cancel a scheduled class.

**Request body:** none

**Responses**

**200**

| Field | Type | Required | Notes |
|---|---|---|---|
| `id` | integer | yes | — |
| `course_id` | integer | yes | — |
| `title` | string | yes | — |
| `scheduled_at` | string (date-time) | yes | — |
| `duration_minutes` | integer | yes | — |
| `meeting_url` | string *(nullable)* | no | — |
| `status` | string | yes | — |
| `is_recorded` | boolean | no | default `False` |

- **422** Validation Error


### POST /v1/live/{session_id}/end

End the class (live -> ended).

**Request body:** none

**Responses**

**200**

| Field | Type | Required | Notes |
|---|---|---|---|
| `id` | integer | yes | — |
| `course_id` | integer | yes | — |
| `title` | string | yes | — |
| `scheduled_at` | string (date-time) | yes | — |
| `duration_minutes` | integer | yes | — |
| `meeting_url` | string *(nullable)* | no | — |
| `status` | string | yes | — |
| `is_recorded` | boolean | no | default `False` |

- **422** Validation Error


### POST /v1/live/{session_id}/start

Go live (scheduled -> live).

**Request body:** none

**Responses**

**200**

| Field | Type | Required | Notes |
|---|---|---|---|
| `id` | integer | yes | — |
| `course_id` | integer | yes | — |
| `title` | string | yes | — |
| `scheduled_at` | string (date-time) | yes | — |
| `duration_minutes` | integer | yes | — |
| `meeting_url` | string *(nullable)* | no | — |
| `status` | string | yes | — |
| `is_recorded` | boolean | no | default `False` |

- **422** Validation Error


## Messages

| | Method | Path | Auth | Wired |
|---|---|---|---|---|
| | `POST` | `/v1/messages` | 🔒 user | ✓ |
| | `GET` | `/v1/messages/threads` | 🔒 user | ✓ |
| | `GET` | `/v1/messages/with/{user_id}` | 🔒 user | ✓ |
| | `POST` | `/v1/messages/with/{user_id}/read` | 🔒 user | ✓ |
| | `DELETE` | `/v1/messages/{message_id}` | 🔒 user | ✓ |

### POST /v1/messages

Send a direct message to another learner.

**Request body**

| Field | Type | Required | Notes |
|---|---|---|---|
| `recipient_id` | integer | yes | — |
| `body` | string | yes | — |


**Responses**

**201**

| Field | Type | Required | Notes |
|---|---|---|---|
| `id` | integer | yes | — |
| `sender_id` | integer | yes | — |
| `recipient_id` | integer | yes | — |
| `body` | string | yes | — |
| `read_at` | string (date-time) *(nullable)* | no | — |
| `created_at` | string (date-time) | yes | — |

- **422** Validation Error


### GET /v1/messages/threads

Conversation threads, most recent first, with unread counts.

**Responses**

**200**

array of objects

| Field | Type | Required | Notes |
|---|---|---|---|
| `other_user_id` | integer | yes | — |
| `other_name` | string | yes | — |
| `last_body` | string | yes | — |
| `last_at` | string (date-time) | yes | — |
| `unread_count` | integer | yes | — |


### GET /v1/messages/with/{user_id}

Message history with one learner, chronological.

**Responses**

**200**

array of objects

| Field | Type | Required | Notes |
|---|---|---|---|
| `id` | integer | yes | — |
| `sender_id` | integer | yes | — |
| `recipient_id` | integer | yes | — |
| `body` | string | yes | — |
| `read_at` | string (date-time) *(nullable)* | no | — |
| `created_at` | string (date-time) | yes | — |

- **422** Validation Error


### POST /v1/messages/with/{user_id}/read

Mark all messages from a learner as read.

**Request body:** none

**Responses**

**200**

object

- **422** Validation Error


### DELETE /v1/messages/{message_id}

Delete own sent message (admin can delete any).

**Responses**

**200**

object

- **422** Validation Error


## Missions

| | Method | Path | Auth | Wired |
|---|---|---|---|---|
| | `GET` | `/v1/me/missions` | 🔒 user | ✓ |
| | `POST` | `/v1/me/missions` | 🔒 user | ✓ |
| | `DELETE` | `/v1/me/missions/{mission_id}` | 🔒 user | ✓ |
| | `GET` | `/v1/me/missions/{mission_id}` | 🔒 user | ✓ |
| | `PATCH` | `/v1/me/missions/{mission_id}` | 🔒 user | ✓ |
| | `POST` | `/v1/me/missions/{mission_id}/steps/{step_id}/complete` | 🔒 user | ✓ |
| | `GET` | `/v1/missions` | ○ optional | ✓ |

### GET /v1/me/missions

List the learner's adopted missions, optionally filtered by status.

**Responses**

**200**

array of objects

| Field | Type | Required | Notes |
|---|---|---|---|
| `id` | integer | yes | — |
| `title` | string | yes | — |
| `description` | string | yes | — |
| `purpose` | string | yes | — |
| `reward_xp` | integer | yes | — |
| `badge` | string *(nullable)* | yes | — |
| `status` | string | yes | — |
| `template_id` | integer *(nullable)* | no | — |
| `steps` | array of MissionStep | no | default `[]` |

<details><summary><code>steps</code> object</summary>

| Field | Type | Required | Notes |
|---|---|---|---|
| `id` | integer | yes | — |
| `title` | string | yes | — |
| `description` | string | yes | — |
| `order` | integer | yes | — |
| `completed` | boolean | yes | — |

</details>

- **422** Validation Error


### POST /v1/me/missions

Adopt a published mission from the catalogue.

Copies the template's content rather than linking to it, because progress
is per learner: two learners on the same mission must not share step
completion. The copy also means editing the template later cannot rewrite
a mission someone is already halfway through.

One active mission at a time — the product treats a mission as the single
thing a learner is working on, which is what makes "finish or complete
mission N first" a useful nudge rather than an obstacle.

**Request body**

| Field | Type | Required | Notes |
|---|---|---|---|
| `template_id` | integer | yes | — |


**Responses**

**201**

| Field | Type | Required | Notes |
|---|---|---|---|
| `id` | integer | yes | — |
| `title` | string | yes | — |
| `description` | string | yes | — |
| `purpose` | string | yes | — |
| `reward_xp` | integer | yes | — |
| `badge` | string *(nullable)* | yes | — |
| `status` | string | yes | — |
| `template_id` | integer *(nullable)* | no | — |
| `steps` | array of MissionStep | no | default `[]` |

<details><summary><code>steps</code> object</summary>

| Field | Type | Required | Notes |
|---|---|---|---|
| `id` | integer | yes | — |
| `title` | string | yes | — |
| `description` | string | yes | — |
| `order` | integer | yes | — |
| `completed` | boolean | yes | — |

</details>

- **422** Validation Error


### DELETE /v1/me/missions/{mission_id}

Delete one of the learner's missions with its steps.

**Responses**

**200**

object

- **422** Validation Error


### GET /v1/me/missions/{mission_id}

Get one of the learner's missions with its steps.

**Responses**

**200**

| Field | Type | Required | Notes |
|---|---|---|---|
| `id` | integer | yes | — |
| `title` | string | yes | — |
| `description` | string | yes | — |
| `purpose` | string | yes | — |
| `reward_xp` | integer | yes | — |
| `badge` | string *(nullable)* | yes | — |
| `status` | string | yes | — |
| `template_id` | integer *(nullable)* | no | — |
| `steps` | array of MissionStep | no | default `[]` |

<details><summary><code>steps</code> object</summary>

| Field | Type | Required | Notes |
|---|---|---|---|
| `id` | integer | yes | — |
| `title` | string | yes | — |
| `description` | string | yes | — |
| `order` | integer | yes | — |
| `completed` | boolean | yes | — |

</details>

- **422** Validation Error


### PATCH /v1/me/missions/{mission_id}

Change a mission's status. Setting completed awards XP + badge.

**Request body**

| Field | Type | Required | Notes |
|---|---|---|---|
| `status` | string | yes | — |


**Responses**

**200**

object

- **422** Validation Error


### POST /v1/me/missions/{mission_id}/steps/{step_id}/complete

Complete one mission step. Finishing the last step completes the mission.

**Request body:** none

**Responses**

**200**

object

- **422** Validation Error


### GET /v1/missions

Published missions available to adopt.

Public on purpose: the catalogue is how a learner decides what to learn
next, and browsing it should not require an account. Unpublished templates
never appear here.

**Responses**

**200**

array of objects

| Field | Type | Required | Notes |
|---|---|---|---|
| `id` | integer | yes | — |
| `title` | string | yes | — |
| `description` | string | yes | — |
| `purpose` | string | yes | — |
| `reward_xp` | integer | yes | — |
| `badge` | string *(nullable)* | yes | — |
| `published` | boolean | yes | — |
| `steps` | array of MissionTemplateStep | no | default `[]` |
| `creator_name` | string | no | default `` |
| `adopted` | boolean | no | default `False` |
| `adopted_mission_id` | integer *(nullable)* | no | — |

<details><summary><code>steps</code> object</summary>

| Field | Type | Required | Notes |
|---|---|---|---|
| `id` | integer | yes | — |
| `title` | string | yes | — |
| `description` | string | yes | — |
| `order` | integer | yes | — |

</details>

- **422** Validation Error


## Onboarding

| | Method | Path | Auth | Wired |
|---|---|---|---|---|
| | `POST` | `/v1/onboarding/complete` | 🔒 user | — |
| | `GET` | `/v1/onboarding/options` | — public | — |
| | `GET` | `/v1/onboarding/status` | 🔒 user | — |

### POST /v1/onboarding/complete

Record pace and interests, and seed the study plan.

Requires a verified address: pace and interests are personal choices about
how a specific person learns, and letting an unproven address set them
would mean anyone who can be spammed into signing up can shape someone's
plan.

Repeatable — changing your pace later is normal, and an account that had to
ask a support question to change a setting would be a support question.

**Request body**

| Field | Type | Required | Notes |
|---|---|---|---|
| `learning_pace` | string | yes | — |
| `interests` | array of string | yes | — |


**Responses**

**200**

| Field | Type | Required | Notes |
|---|---|---|---|
| `email_verified` | boolean | yes | — |
| `onboarding_completed` | boolean | yes | — |
| `learning_pace` | string *(nullable)* | yes | — |
| `interests` | array of string | yes | — |
| `next_step` | string | yes | — |

- **422** Validation Error


### GET /v1/onboarding/options

Pace and interest options, server-side.

The pickers read this rather than hardcoding a list: a client with its own
copy will eventually offer something the API rejects.

**Responses**

**200**

| Field | Type | Required | Notes |
|---|---|---|---|
| `paces` | array of PaceOption | yes | — |
| `interests` | array of terestOption | yes | — |
| `default_pace` | string | yes | — |

<details><summary><code>paces</code> object</summary>

| Field | Type | Required | Notes |
|---|---|---|---|
| `key` | string | yes | — |
| `label` | string | yes | — |
| `min_minutes` | integer | yes | — |
| `max_minutes` | integer *(nullable)* | yes | — |
| `daily_goal_minutes` | integer | yes | — |
| `description` | string | yes | — |

</details>

<details><summary><code>interests</code> object</summary>

| Field | Type | Required | Notes |
|---|---|---|---|
| `key` | string | yes | — |
| `label` | string | yes | — |

</details>


### GET /v1/onboarding/status

Where this account is in onboarding, and what comes next.

**Responses**

**200**

| Field | Type | Required | Notes |
|---|---|---|---|
| `email_verified` | boolean | yes | — |
| `onboarding_completed` | boolean | yes | — |
| `learning_pace` | string *(nullable)* | yes | — |
| `interests` | array of string | yes | — |
| `next_step` | string | yes | — |


## Payments

| | Method | Path | Auth | Wired |
|---|---|---|---|---|
| | `POST` | `/v1/payments/webhooks/paystack` | — public | — |

### POST /v1/payments/webhooks/paystack

Paystack's server-to-server confirmation. HMAC-SHA512 signed.

No auth: Paystack authenticates with the signature header, and the
signature is checked against the raw body before anything is trusted.
Unsigned or mis-signed requests are rejected without touching the DB.

**Request body:** none

**Responses**

**200**

object


## Progress

| | Method | Path | Auth | Wired |
|---|---|---|---|---|
| | `GET` | `/v1/me/badges` | 🔒 user | ✓ |
| | `GET` | `/v1/me/context` | 🔒 user | ✓ |
| | `DELETE` | `/v1/me/conversation` | 🔒 user | — |
| | `GET` | `/v1/me/conversation` | 🔒 user | — |
| | `POST` | `/v1/me/conversation` | 🔒 user | — |
| | `POST` | `/v1/me/enroll/{course_id}` | 🔒 user | ✓ |
| | `GET` | `/v1/me/enrollments` | 🔒 user | ✓ |
| | `POST` | `/v1/me/lessons/{lesson_id}/complete` | 🔒 user | ✓ |
| | `POST` | `/v1/me/lessons/{lesson_id}/start` | 🔒 user | — |
| | `GET` | `/v1/me/path-profile` | 🔒 user | — |
| | `GET` | `/v1/me/personalization-profile` | 🔒 user | — |
| | `POST` | `/v1/me/quiz-results` | 🔒 user | ✓ |
| | `GET` | `/v1/me/revision-profile` | 🔒 user | — |
| | `DELETE` | `/v1/me/study-plan` | 🔒 user | ✓ |
| | `GET` | `/v1/me/study-plan` | 🔒 user | ✓ |
| | `PUT` | `/v1/me/study-plan` | 🔒 user | ✓ |
| | `GET` | `/v1/me/xp` | 🔒 user | ✓ |
| | `POST` | `/v1/me/xp/award` | 🔒 user | ✓ |

### GET /v1/me/badges

List the learner's earned badges, newest first.

**Responses**

**200**

array of objects

| Field | Type | Required | Notes |
|---|---|---|---|
| `id` | integer | yes | — |
| `badge_id` | string | yes | — |
| `name` | string | yes | — |
| `description` | string | yes | — |
| `icon` | string *(nullable)* | no | — |
| `earned_date` | string (date-time) | yes | — |

- **422** Validation Error


### GET /v1/me/context

Assemble the LearnerContext object the AI coach expects, from real DB data.

**Responses**

**200**

| Field | Type | Required | Notes |
|---|---|---|---|
| `learner_id` | string | yes | — |
| `learner_name` | string | yes | — |
| `current_course` | string | no | default `` |
| `current_lesson` | string | no | default `` |
| `current_topic` | string | no | default `` |
| `interests` | array of string | no | default `[]` |
| `difficulty_level` | string | no | default `beginner` |
| `goals` | string | no | default `` |
| `xp_total` | integer | no | default `0` |
| `xp_this_week` | integer | no | default `0` |
| `streak_days` | integer | no | default `0` |
| `lessons_completed` | integer | no | default `0` |
| `lessons_total` | integer | no | default `0` |
| `completion_percent` | number | no | default `0.0` |
| `quiz_performance` | array of QuizPerformance | no | default `[]` |
| `level` | object *(nullable)* | no | — |
| `badges` | array of object | no | default `[]` |
| `xp_breakdown` | array of XpEntry | no | default `[]` |
| `missions` | object *(nullable)* | no | — |
| `study_plan` | object *(nullable)* | no | — |
| `conversation_history` | array of object | no | default `[]` |

<details><summary><code>quiz_performance</code> object</summary>

| Field | Type | Required | Notes |
|---|---|---|---|
| `topic` | string | yes | — |
| `score_percent` | number | yes | — |
| `attempts` | integer | yes | — |
| `last_attempt_date` | string | yes | — |

</details>

<details><summary><code>xp_breakdown</code> object</summary>

| Field | Type | Required | Notes |
|---|---|---|---|
| `activity` | string | yes | — |
| `amount` | integer | yes | — |
| `note` | string *(nullable)* | no | — |
| `earned_date` | string (date-time) | yes | — |

</details>


### DELETE /v1/me/conversation

Delete all stored chat turns for the learner.

**Responses**

**200**

object


### GET /v1/me/conversation

Read back chat turns in chronological order (oldest first).

**Responses**

**200**

array of objects

| Field | Type | Required | Notes |
|---|---|---|---|
| `id` | integer | yes | — |
| `role` | string | yes | — |
| `content` | string | yes | — |
| `created_at` | string (date-time) | yes | — |

- **422** Validation Error


### POST /v1/me/conversation

Persist one chat turn (learner or coach) for later context.

**Request body**

| Field | Type | Required | Notes |
|---|---|---|---|
| `role` | string | yes | — |
| `content` | string | yes | — |


**Responses**

**201**

| Field | Type | Required | Notes |
|---|---|---|---|
| `id` | integer | yes | — |
| `role` | string | yes | — |
| `content` | string | yes | — |
| `created_at` | string (date-time) | yes | — |

- **422** Validation Error


### POST /v1/me/enroll/{course_id}

Enroll the authenticated learner in a course.

**Request body:** none

**Responses**

**201**

object

- **422** Validation Error


### GET /v1/me/enrollments

The learner's enrollments with per-course progress aggregation.

**Responses**

**200**

array of objects

| Field | Type | Required | Notes |
|---|---|---|---|
| `course_id` | integer | yes | — |
| `title` | string | yes | — |
| `difficulty_level` | string | yes | — |
| `lessons_total` | integer | yes | — |
| `lessons_completed` | integer | yes | — |
| `completion_percent` | number | yes | — |
| `completed` | boolean | yes | — |
| `enrolled_at` | string (date-time) | yes | — |

- **422** Validation Error


### POST /v1/me/lessons/{lesson_id}/complete

Mark a lesson complete; awards lesson XP (once per lesson).

The response carries a `next` block naming the following lesson and whether
it is a quiz — route the learner off that rather than deciding client-side.
A first completion 409s while an earlier quiz lesson is unpassed (the
detail names the quiz); re-completing an already-finished lesson never
gates, so history stays reachable.

**Request body**

| Field | Type | Required | Notes |
|---|---|---|---|
| _(whole body)_ | LessonCompleteRequest | yes | — |



**Responses**

**200**

| Field | Type | Required | Notes |
|---|---|---|---|
| `completed` | boolean | yes | — |
| `lesson_id` | integer | yes | — |
| `xp_awarded` | integer | yes | — |
| `already_completed` | boolean | yes | — |
| `next` | NextStep | yes | — |

<details><summary><code>next</code> object</summary>

| Field | Type | Required | Notes |
|---|---|---|---|
| `type` | string (one of `quiz`, `lesson`, `course_complete`) | yes | — |
| `lesson_id` | integer *(nullable)* | no | — |
| `title` | string *(nullable)* | no | — |
| `topic` | string *(nullable)* | no | — |
| `quiz_required` | boolean | no | default `False` |

</details>

- **409** Blocked by an unpassed earlier quiz lesson. The detail names the quiz to pass — submit a passing score with its lesson_id via POST /v1/me/quiz-results first.

- **422** Validation Error


### POST /v1/me/lessons/{lesson_id}/start

Record that the learner started a lesson (idempotent, no XP).

409s exactly like completing it does: starting the lesson after an
unpassed quiz is refused, so skipping `start` cannot skip the gate.

**Request body:** none

**Responses**

**200**

| Field | Type | Required | Notes |
|---|---|---|---|
| `started` | boolean | yes | — |
| `lesson_id` | integer | yes | — |
| `first_time` | boolean | yes | — |

- **409** Blocked by an unpassed earlier quiz lesson. The detail names the quiz to pass.

- **422** Validation Error


### GET /v1/me/path-profile

Profile for learning-path generation: completed + available courses.

**Responses**

**200**

object


### GET /v1/me/personalization-profile

Minimal profile for content personalization.

**Responses**

**200**

object


### POST /v1/me/quiz-results

Record a quiz attempt and award quiz XP.

`lesson_id` is what makes the attempt count toward clearing a quiz lesson.
Omit it for a standalone practice run: the score is still recorded and still
earns XP, it just does not unlock the next lesson.

**Request body**

| Field | Type | Required | Notes |
|---|---|---|---|
| `course_id` | integer *(nullable)* | no | — |
| `topic` | string | yes | — |
| `score_percent` | number | yes | — |
| `attempts` | integer | no | default `1` |
| `lesson_id` | integer *(nullable)* | no | — |


**Responses**

**200**

| Field | Type | Required | Notes |
|---|---|---|---|
| `course_id` | integer *(nullable)* | no | — |
| `topic` | string | yes | — |
| `score_percent` | number | yes | — |
| `attempts` | integer | no | default `1` |
| `lesson_id` | integer *(nullable)* | no | — |

- **422** Validation Error


### GET /v1/me/revision-profile

Revision profile: one record per encountered topic.

**Responses**

**200**

object


### DELETE /v1/me/study-plan

Clear the learner's study plan.

**Responses**

**200**

object


### GET /v1/me/study-plan

Get the learner's study plan, or 404 if they haven't set one.

**Responses**

**200**

| Field | Type | Required | Notes |
|---|---|---|---|
| `id` | integer | yes | — |
| `daily_goal_minutes` | integer | yes | — |
| `weekly_target_lessons` | integer | yes | — |
| `focus_topics` | array of string | no | default `[]` |
| `deadline` | string *(nullable)* | no | — |


### PUT /v1/me/study-plan

Create or replace the learner's study plan.

**Request body**

| Field | Type | Required | Notes |
|---|---|---|---|
| `daily_goal_minutes` | integer | no | default `30` |
| `weekly_target_lessons` | integer | no | default `3` |
| `focus_topics` | array of string | no | — |
| `deadline` | string *(nullable)* | no | — |


**Responses**

**200**

| Field | Type | Required | Notes |
|---|---|---|---|
| `id` | integer | yes | — |
| `daily_goal_minutes` | integer | yes | — |
| `weekly_target_lessons` | integer | yes | — |
| `focus_topics` | array of string | no | default `[]` |
| `deadline` | string *(nullable)* | no | — |

- **422** Validation Error


### GET /v1/me/xp

The learner's XP totals, level, and recent breakdown.

**Responses**

**200**

| Field | Type | Required | Notes |
|---|---|---|---|
| `xp_total` | integer | yes | — |
| `xp_this_week` | integer | yes | — |
| `level` | integer | yes | — |
| `level_title` | string | yes | — |
| `xp_this_level` | integer | yes | — |
| `level_up_xp` | integer | yes | — |
| `next_level_title` | string | yes | — |
| `breakdown` | array of XpEntry | yes | — |

<details><summary><code>breakdown</code> object</summary>

| Field | Type | Required | Notes |
|---|---|---|---|
| `activity` | string | yes | — |
| `amount` | integer | yes | — |
| `note` | string *(nullable)* | no | — |
| `earned_date` | string (date-time) | yes | — |

</details>


### POST /v1/me/xp/award

Award XP for revision, challenge, live participation, or streaks.

lesson/quiz/mission are rejected here — they have dedicated endpoints
(lesson completion, quiz submit, mission complete) so each XP source
keeps a single source of truth.

**Request body**

| Field | Type | Required | Notes |
|---|---|---|---|
| `activity` | string | yes | — |
| `note` | string *(nullable)* | no | — |


**Responses**

**201**

| Field | Type | Required | Notes |
|---|---|---|---|
| `activity` | string | yes | — |
| `amount` | integer | yes | — |
| `note` | string *(nullable)* | no | — |
| `earned_date` | string (date-time) | yes | — |

- **422** Validation Error


## Purchases

| | Method | Path | Auth | Wired |
|---|---|---|---|---|
| | `GET` | `/v1/courses/{course_id}/payment` | 🔒 user | ✓ |
| | `POST` | `/v1/courses/{course_id}/purchase` | 🔒 user | ✓ |
| | `POST` | `/v1/courses/{course_id}/verify` | 🔒 user | ✓ |

### GET /v1/courses/{course_id}/payment

Current state of the learner's payment for this course.

The callback page polls this while it waits, so a slow or slow-to-arrive
webhook shows "confirming…" rather than an error.

**Responses**

**200**

| Field | Type | Required | Notes |
|---|---|---|---|
| `reference` | string | yes | — |
| `status` | string | yes | — |
| `course_id` | integer | yes | — |
| `amount_naira` | integer | yes | — |
| `enrolled` | boolean | yes | — |

- **422** Validation Error


### POST /v1/courses/{course_id}/purchase

Begin a purchase.

With Paystack configured this creates a pending payment and returns a
hosted checkout URL — the caller redirects the browser there and no
content is unlocked yet. Unconfigured (dev/tests) settles immediately.

**Request body:** none

**Responses**

**200**

| Field | Type | Required | Notes |
|---|---|---|---|
| `payment` | Payment | yes | — |
| `checkout_url` | string *(nullable)* | no | — |
| `enrolled` | boolean | yes | — |

<details><summary><code>payment</code> object</summary>

| Field | Type | Required | Notes |
|---|---|---|---|
| `id` | integer | yes | — |
| `course_id` | integer | yes | — |
| `amount_naira` | integer | yes | — |
| `currency` | string | yes | — |
| `status` | string | yes | — |
| `provider` | string | yes | — |
| `reference` | string | yes | — |
| `created_at` | string (date-time) | yes | — |

</details>

- **422** Validation Error


### POST /v1/courses/{course_id}/verify

Ask Paystack whether this purchase actually completed.

Called by the checkout callback page after Paystack redirects back. The
reference from the URL is treated as a hint only — the provider is the
authority, and the payment must belong to the signed-in learner.

**Request body:** none

**Responses**

**200**

| Field | Type | Required | Notes |
|---|---|---|---|
| `reference` | string | yes | — |
| `status` | string | yes | — |
| `course_id` | integer | yes | — |
| `amount_naira` | integer | yes | — |
| `enrolled` | boolean | yes | — |

- **422** Validation Error


## Uploads

| | Method | Path | Auth | Wired |
|---|---|---|---|---|
| | `GET` | `/v1/files/url` | ○ optional | ✓ |
| | `POST` | `/v1/uploads/presigned` | 🔒 user | ✓ |

### GET /v1/files/url

Resolve a storage key to a presigned download URL.

Public when the key belongs to a published course (thumbnail/assets)
or a creator profile image; otherwise owner/admin only. Learner library
files are never public: the owner or an admin, and nobody else.

**Responses**

**200**

object

- **422** Validation Error


### POST /v1/uploads/presigned

Mint a presigned upload form. Server chooses the key; clients POST there.

The form carries a content-length-range, so the storage provider enforces
the size cap — a client holding this URL cannot exceed it. The declared
size is checked here too, purely to fail fast with a clear message.

**Request body**

| Field | Type | Required | Notes |
|---|---|---|---|
| `purpose` | string | yes | — |
| `filename` | string | yes | — |
| `content_type` | string | yes | — |
| `size_bytes` | integer | no | default `0` |


**Responses**

**201**

| Field | Type | Required | Notes |
|---|---|---|---|
| `upload_url` | string | yes | — |
| `fields` | object | yes | — |
| `storage_key` | string | yes | — |
| `expires_in` | integer | yes | — |
| `max_bytes` | integer | yes | — |
| `method` | string | no | default `POST` |

- **422** Validation Error


## Users

| | Method | Path | Auth | Wired |
|---|---|---|---|---|
| | `DELETE` | `/v1/users/me` | 🔒 user | ✓ |
| | `GET` | `/v1/users/me` | 🔒 user | ✓ |
| | `PATCH` | `/v1/users/me` | 🔒 user | ✓ |
| | `PATCH` | `/v1/users/me/password` | 🔒 user | ✓ |

### DELETE /v1/users/me

Delete the learner's account and all of their data.

**Responses**

**200**

object


### GET /v1/users/me

Get the authenticated learner's profile.

**Responses**

**200**

| Field | Type | Required | Notes |
|---|---|---|---|
| `id` | integer | yes | — |
| `email` | string | yes | — |
| `learner_name` | string | yes | — |
| `difficulty_level` | string | yes | — |
| `goals` | string | yes | — |
| `interests` | array of string | yes | — |
| `current_course` | string | yes | — |
| `current_lesson` | string | yes | — |
| `current_topic` | string | yes | — |
| `is_admin` | boolean | no | default `False` |
| `is_creator` | boolean | no | default `False` |
| `created_at` | string (date-time) | yes | — |


### PATCH /v1/users/me

Update the authenticated learner's profile fields.

**Request body**

| Field | Type | Required | Notes |
|---|---|---|---|
| `learner_name` | string *(nullable)* | no | — |
| `difficulty_level` | string (one of `beginner`, `intermediate`, `advanced`) *(nullable)* | no | — |
| `goals` | string *(nullable)* | no | — |
| `interests` | array of string *(nullable)* | no | — |
| `current_course` | string *(nullable)* | no | — |
| `current_lesson` | string *(nullable)* | no | — |
| `current_topic` | string *(nullable)* | no | — |


**Responses**

**200**

| Field | Type | Required | Notes |
|---|---|---|---|
| `id` | integer | yes | — |
| `email` | string | yes | — |
| `learner_name` | string | yes | — |
| `difficulty_level` | string | yes | — |
| `goals` | string | yes | — |
| `interests` | array of string | yes | — |
| `current_course` | string | yes | — |
| `current_lesson` | string | yes | — |
| `current_topic` | string | yes | — |
| `is_admin` | boolean | no | default `False` |
| `is_creator` | boolean | no | default `False` |
| `created_at` | string (date-time) | yes | — |

- **422** Validation Error


### PATCH /v1/users/me/password

Change the learner's password (requires the current one).

**Request body**

| Field | Type | Required | Notes |
|---|---|---|---|
| `current_password` | string | yes | — |
| `new_password` | string | yes | — |


**Responses**

**200**

object

- **422** Validation Error


## Waitlist

| | Method | Path | Auth | Wired |
|---|---|---|---|---|
| | `POST` | `/v1/waitlist` | — public | — |

### POST /v1/waitlist

Add someone to the early-access waitlist. No auth — they have no account.

Repeat submissions update the existing row rather than creating a second
one, so someone who fixes a typo and resubmits is not counted twice. The
status code is the only thing that differs: 201 the first time, 200 after.

Deliberately does not send a confirmation email. Mail is only configured
for one address, so every other signup would see a bounce and conclude the
form is broken — a worse outcome than no mail at all.

**Request body**

| Field | Type | Required | Notes |
|---|---|---|---|
| `email` | string (email) | yes | — |
| `name` | string | yes | — |
| `role` | string (one of `learner`, `creator`) | yes | — |
| `interests` | array of string | no | — |
| `course` | string | no | default `` |


**Responses**

**201**

| Field | Type | Required | Notes |
|---|---|---|---|
| `message` | string | yes | — |
| `email` | string | yes | — |
| `position` | integer | yes | — |
| `created` | boolean | yes | — |

- **422** Validation Error

