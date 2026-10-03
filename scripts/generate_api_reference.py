"""Generate API_REFERENCE.md from the live OpenAPI schema.

The spec is the only trustworthy description of what these endpoints accept:
it is the same object FastAPI validates requests against, so it cannot drift
from the code. Writing it by hand would eventually disagree with the app in
exactly the places nobody checks.

    python -m scripts.generate_api_reference

Also annotates each endpoint with whether the frontend actually calls it
(scanned from ../talyn-web/src when that folder is present) and whether a
caller identity is required, both of which the spec alone does not say.
"""
import re
import sys
from collections import defaultdict
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from app.main import app  # noqa: E402

ROOT = Path(__file__).resolve().parent.parent
WEB_SRC = ROOT.parent / "talyn-web" / "src"

# Dependency call names -> what a caller needs.
AUTH_RULES = [
    ("admin", "admin"),
    ("optional_user", "optional"),
    ("optional", "optional"),
    ("current_user", "user"),
    ("get_user", "user"),
    ("require_user", "user"),
]
AUTH_LABEL = {
    "admin": "🔒 admin",
    "user": "🔒 user",
    "optional": "○ optional",
    "public": "— public",
}

METHOD_ORDER = ["GET", "POST", "PUT", "PATCH", "DELETE"]


# ── Schema rendering ─────────────────────────────────────────────────────────

def _schema(spec, node, depth=0):
    """A one-line human description of a type, with constraints."""
    if not isinstance(node, dict):
        return "any"
    if "$ref" in node:
        return _type_name(spec, node["$ref"].rsplit("/", 1)[-1])

    if "anyOf" in node or "oneOf" in node:
        parts = [_schema(spec, s, depth + 1) for s in node.get("anyOf", node.get("oneOf", []))]
        if "null" in {p for p in parts if p == "null"}:
            parts = [p for p in parts if p != "null"]
            return " or ".join(dict.fromkeys(parts)) + " *(nullable)*"
        return " or ".join(dict.fromkeys(parts))

    for key in ("allOf",):
        if key in node:
            merged = {}
            for sub in node[key]:
                resolved = spec["components"]["schemas"].get(
                    sub.get("$ref", "").rsplit("/", 1)[-1], {}
                ) if "$ref" in sub else sub
                merged.update(resolved)
            return _schema(spec, merged, depth + 1)

    raw = node.get("type")
    if raw == "array":
        return f"array of {_schema(spec, node.get('items', {}), depth + 1)}"
    if raw == "string":
        bits = ["string"]
        if node.get("format") in ("email", "date-time", "uri"):
            bits[0] = f"string ({node['format']})"
        limits = []
        if "min_length" in node:
            limits.append(f"min {node['min_length']} chars")
        if "max_length" in node:
            limits.append(f"max {node['max_length']} chars")
        if node.get("enum"):
            limits.append("one of " + ", ".join(f"`{e}`" for e in node["enum"]))
        return " ".join([bits[0]] + ([f"({', '.join(limits)})"] if limits else []))
    if raw in ("integer", "number"):
        bits = raw
        limits = []
        if "ge" in node:
            limits.append(f"min {node['ge']}")
        if "le" in node:
            limits.append(f"max {node['le']}")
        if node.get("exclusiveMinimum") is not None:
            limits.append(f"min {node['exclusiveMinimum']} (exclusive)")
        return " ".join([bits] + ([f"({', '.join(limits)})"] if limits else []))
    if raw == "boolean":
        return "boolean"
    if raw == "object" or "properties" in node:
        return "object"
    return raw or "any"


def _type_name(spec, name):
    return name.replace("Out", "").replace("In", "").replace("Read", "") or name


def _props(spec, schema):
    """Resolve an object schema's (name, schema, required) triples."""
    if "$ref" in schema:
        schema = spec["components"]["schemas"].get(
            schema["$ref"].rsplit("/", 1)[-1], {}
        )
    if "allOf" in schema and "properties" not in schema:
        merged = {"properties": {}, "required": []}
        for sub in schema["allOf"]:
            resolved = spec["components"]["schemas"].get(
                sub.get("$ref", "").rsplit("/", 1)[-1], {}
            ) if "$ref" in sub else sub
            merged["properties"].update(resolved.get("properties", {}))
            merged["required"] += resolved.get("required", [])
        schema = merged
    props = schema.get("properties", {})
    required = set(schema.get("required", []))
    return [
        (name, sub, name in required)
        for name, sub in props.items()
        if not name.startswith("_")
    ]


def _notes(node):
    bits = []
    if "default" in node:
        bits.append(f"default `{node['default']}`")
    if node.get("description"):
        bits.append(node["description"])
    return " ".join(bits)


def _table(spec, fields, indent=""):
    if not fields:
        return f"{indent}_no body_\n"
    out = [
        f"{indent}| Field | Type | Required | Notes |",
        f"{indent}|---|---|---|---|",
    ]
    for name, node, req in fields:
        note = _notes(node).replace("|", "\\|") or "—"
        out.append(
            f"{indent}| `{name}` | {_schema(spec, node)} | "
            f"{'yes' if req else 'no'} | {note} |"
        )
    return "\n".join(out) + "\n"


def _body_section(spec, op):
    """Render a request body: top-level fields, plus any nested objects."""
    content = op.get("requestBody", {}).get("content", {})
    json_schema = content.get("application/json", {}).get("schema")
    if not json_schema:
        if content:
            other = list(content)[0]
            return f"Body: `{other}`.\n\n"
        return ""

    parts = ["| Field | Type | Required | Notes |", "|---|---|---|---|"]
    fields = _props(spec, json_schema)
    if not fields:
        # e.g. a bare list or scalar body
        parts.append(f"| _(whole body)_ | {_schema(spec, json_schema)} | yes | — |\n")
        return "\n".join(parts) + "\n"

    for name, node, req in fields:
        note = _notes(node).replace("|", "\\|") or "—"
        parts.append(
            f"| `{name}` | {_schema(spec, node)} | {'yes' if req else 'no'} | {note} |"
        )
    out = "\n".join(parts) + "\n"

    # One level of nesting, which is where the interesting shapes live.
    for name, node, _req in fields:
        target = node
        if target.get("type") == "array":
            target = target.get("items", {})
        nested = _props(spec, target) if isinstance(target, dict) else []
        if nested:
            out += f"\n<details><summary><code>{name}</code> object</summary>\n\n"
            out += _table(spec, nested)
            out += "\n</details>\n"
    return out


def _shape(spec, schema):
    """Describe a top-level payload: may be an object, an array of objects,
    or a bare scalar. Returns (prefix, fields, scalar_text)."""
    node = schema or {}
    if node.get("type") == "array":
        items = node.get("items", {})
        fields = _props(spec, items) if isinstance(items, dict) else []
        if fields:
            return ("array of objects", fields, "")
        return ("", [], f"array of {_schema(spec, items)}")
    fields = _props(spec, node)
    if fields:
        return ("", fields, "")
    return ("", [], _schema(spec, node))


def _render_payload(spec, schema):
    prefix, fields, scalar = _shape(spec, schema)
    if fields:
        return (f"{prefix}\n\n" if prefix else "") + _table(spec, fields)
    return f"{prefix + ' ' if prefix else ''}{scalar}\n" if (prefix or scalar) else "_empty_\n"


def _response_section(spec, op):
    resp = op.get("responses", {})
    lines = []
    for code in sorted(resp):
        if code.startswith("2"):
            content = resp[code].get("content", {}).get("application/json", {})
            schema = content.get("schema")
            if not schema:
                lines.append(f"**{code}**\n\n_empty_\n")
                continue
            lines.append(f"**{code}**\n")
            lines.append(_render_payload(spec, schema))
            continue
        desc = (resp[code].get("description") or "").strip()
        lines.append(f"- **{code}** {desc}\n")
    return "\n".join(lines) if lines else ""


# ── Route introspection ──────────────────────────────────────────────────────

def _iter_routes():
    """Yield route-like objects across FastAPI route container variants."""
    for route in app.routes:
        yield route
        contexts = getattr(route, "effective_route_contexts", None)
        if callable(contexts):
            yield from contexts()


def _auth_label(route):
    names = []
    stack = list(getattr(route, "dependant", None).dependencies) if hasattr(route, "dependant") else []
    while stack:
        dep = stack.pop()
        call = dep.call
        if call is not None and getattr(call, "__name__", ""):
            names.append(call.__name__)
        stack.extend(getattr(dep, "dependencies", []) or [])
    blob = " ".join(names).lower()
    for needle, level in AUTH_RULES:
        if needle in blob:
            return AUTH_LABEL[level]
    return AUTH_LABEL["public"]


def _used_paths():
    """Paths the frontend actually calls, so 'wired' is measured not guessed."""
    if not WEB_SRC.exists():
        return set()
    frag = re.compile(r"(/v1/[A-Za-z0-9_\-/\$\{\}]*)")
    found = set()
    for f in WEB_SRC.rglob("*"):
        if f.suffix in (".ts", ".tsx"):
            text = f.read_text(encoding="utf-8", errors="ignore")
            for m in frag.finditer(text):
                p = re.sub(r"\$\{[^}]*\}", "{}", m.group(1).split("?")[0].rstrip("/"))
                if p.count("{}") > 1:
                    p = p[:p.index("{}") + 2]
                found.add(p)
    return found


def _matches(path, used):
    normalised = re.sub(r"\{[^}]+\}", "{}", path)
    if normalised in used:
        return True
    for frag in used:
        rx = "^" + re.escape(frag).replace(re.escape("{}"), "[^/]+") + "($|/)"
        if re.match(rx, normalised):
            return True
    return False


# ── Output ───────────────────────────────────────────────────────────────────

def build():
    spec = app.openapi()
    used = _used_paths()
    grouped = defaultdict(list)

    for route in _iter_routes():
        path = getattr(route, "path", None)
        methods = getattr(route, "methods", None) or set()
        if not path or not path.startswith("/v1"):
            continue
        methods = [m for m in METHOD_ORDER if m in methods]
        if not methods:
            continue
        tag = next(iter(getattr(route, "tags", None) or ["other"]), "other")
        auth = _auth_label(route)
        wired = "✓" if _matches(path, used) else "—"
        for method in methods:
            op = spec["paths"].get(path, {}).get(method.lower())
            if not op:
                continue
            grouped[tag].append(
                {
                    "method": method,
                    "path": path,
                    "op": op,
                    "auth": auth,
                    "wired": wired,
                }
            )

    lines = [
        "# Talyn API reference",
        "",
        "Generated from the application's OpenAPI schema — do not edit by hand.",
        "Regenerate with `python -m scripts.generate_api_reference`.",
        "",
        "## Conventions",
        "",
        "| Column | Meaning |",
        "|---|---|",
        "| `🔒 user` | requires a learner access token |",
        "| `🔒 admin` | requires an admin token |",
        "| `○ optional` | works signed-out; behaves differently when signed in |",
        "| `— public` | no token needed |",
        "| `✓` wired | some frontend page calls this |",
        "| `—` unwired | backend only; no page calls it yet |",
        "",
        "Base URL: add `/v1` to every path below. In development that is",
        "`http://localhost:8000`; deployed it is the backend's public origin.",
        "",
        "Auth: `Authorization: Bearer <access_token>` on every row marked 🔒 or ○.",
        "Errors are always `{\"detail\": \"...\"}`; validation errors put an array",
        "of objects in `detail` instead.",
        "",
        "Money is always integer minor units (kobo). Timestamps are UTC ISO-8601.",
        "",
        "## Signup and onboarding flow",
        "",
        "New accounts go through this order. Enforce it client-side with",
        "`GET /v1/onboarding/status` — its `next_step` is the single value the",
        "client routes on (`verify_email` → `choose_pace` → `choose_interests`",
        "→ `complete` → `done`). Do not reimplement the order in the frontend;",
        "two implementations of it will disagree.",
        "",
        "1. `POST /v1/auth/register` — creates the account and emails a",
        "   verification link (`/verify-email?token=...`). The welcome email",
        "   is held back until the address is confirmed.",
        "2. `POST /v1/auth/email-verification/confirm` — proves the address.",
        "   Links are single-use and expire after 24 hours. Re-request with",
        "   `POST /v1/auth/email-verification/request`, which answers",
        "   identically for known and unknown addresses.",
        "3. `GET /v1/onboarding/options` (public) — the pace list and the",
        "   interest list. Render pickers from this; never hardcode the",
        "   options, or the client will offer values the API rejects.",
        "4. `POST /v1/onboarding/complete` (`learning_pace` + `interests`) —",
        "   records both and seeds the study plan's daily goal from the pace.",
        "   Repeatable: changing pace later re-seeds the plan.",
        "",
        "Until onboarding completes, enrolment, lesson start/complete, quiz",
        "results, mission adoption and mission steps answer **409** with a",
        "message naming what is missing. Reads stay open. Treat 409 here as",
        "'route into onboarding', not as an error screen.",
        "",
        "Google sign-in skips verification (Google already proved the address)",
        "but still goes through pace + interests.",
        "",
        "### Where those values live",
        "",
        "`GET /v1/users/me` returns the profile only — name, email, interests,",
        "difficulty, goals, timestamps. It deliberately does **not** carry",
        "`email_verified_at`, `learning_pace` or `onboarding_completed_at`, so",
        "do not go looking for them there. `GET /v1/onboarding/status` is the",
        "single read for all of it, including `next_step`: one call decides",
        "which screen comes next.",
        "",
        "Two further 409s come from `POST /v1/me/missions`, unrelated to",
        "onboarding: `Finish or complete mission N first` (one active mission",
        "at a time) and `You have already taken this mission`. Both are normal",
        "states rather than failures — `GET /v1/missions` already reports",
        "`adopted` and `adopted_mission_id` per catalogue entry, so the adopt",
        "button can be hidden before either is ever hit.",
        "",
    ]

    total = sum(len(v) for v in grouped.values())
    lines += [
        "---",
        "",
        f"{total} endpoints across {len(grouped)} areas.",
        "",
    ]

    for tag in sorted(grouped):
        routes = sorted(grouped[tag], key=lambda r: (r["path"], r["method"]))
        lines += [f"## {tag.replace('_', ' ').title()}", ""]
        lines += ["| | Method | Path | Auth | Wired |",
                  "|---|---|---|---|---|"]
        for r in routes:
            lines.append(
                f"| | `{r['method']}` | `{r['path']}` | {r['auth']} | {r['wired']} |"
            )
        lines.append("")

        for r in routes:
            title = r["op"].get("summary") or r["path"]
            lines += [f"### {r['method']} {r['path']}", ""]
            desc = (r["op"].get("description") or "").strip()
            if desc and desc.lower() != title.lower():
                lines += [desc, ""]

            if r["method"] in ("POST", "PUT", "PATCH"):
                body = _body_section(spec, r["op"])
                if body.strip():
                    lines += ["**Request body**", "", body, ""]
                else:
                    lines += ["**Request body:** none", ""]

            responses = _response_section(spec, r["op"])
            if responses:
                lines += ["**Responses**", "", responses, ""]

    text = "\n".join(lines)
    out = ROOT / "API_REFERENCE.md"
    out.write_text(text, encoding="utf-8")
    return out, total


if __name__ == "__main__":
    path, count = build()
    print(f"wrote {path} ({count} endpoints)")