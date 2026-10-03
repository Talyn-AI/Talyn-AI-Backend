"""Validate render.yaml against Render's published JSON schema.

Render's Blueprint error message is famously unhelpful ("a Blueprint file was
found, but there was an issue") — it does not say which key. Validating locally
against the same schema turns a guess into an answer.

    python -m scripts.check_render_yaml
"""
import json
import sys
import urllib.request
from pathlib import Path

import yaml

SCHEMA_URL = "https://render.com/schema/render.yaml.json"

# Root-level keys the schema permits, mirroring
# https://render.com/docs/blueprint-spec.
ROOT_KEYS = {
    "services", "databases", "envVarGroups", "projects", "ungrouped",
    "previews", "previewsEnabled", "previewsExpireAfterDays", "version",
    "buildSources",
}
SERVICE_KEYS = {
    "type", "name", "region", "plan", "runtime", "repo", "branch", "image",
    "rootDir", "dockerCommand", "dockerContext", "dockerfilePath",
    "numInstances", "healthCheckPath", "scaling", "buildCommand",
    "startCommand", "preDeployCommand", "registryCredential", "domain",
    "domains", "envVars", "autoDeploy", "autoDeployTrigger",
    "initialDeployHook", "disk", "buildFilter", "previews",
    "pullRequestPreviewsEnabled", "previewPlan", "maintenanceMode",
    "maxShutdownDelaySeconds", "ipAllowList", "renderSubdomainPolicy",
}


def check_structure(path: Path) -> list[str]:
    """Catch the mistakes that actually happen, without a network call."""
    problems: list[str] = []
    text = path.read_text(encoding="utf-8")

    # A Blueprint must be a single YAML document. Trailing prose after --- is
    # the classic way to produce an unparseable one.
    try:
        docs = list(yaml.safe_load_all(text))
    except yaml.YAMLError as e:
        return [f"not valid YAML: {str(e).splitlines()[0]}"]
    if len(docs) > 1:
        problems.append(
            f"{len(docs)} YAML documents; a Blueprint must be exactly one "
            "(no trailing prose after ---)"
        )
        return problems
    data = docs[0] or {}

    unknown = set(data) - ROOT_KEYS
    if unknown:
        problems.append(
            f"root-level key(s) not allowed by Render's schema: "
            f"{', '.join(sorted(unknown))}"
        )

    if not data.get("services") and not data.get("databases"):
        problems.append("no services and no databases defined")

    for service in data.get("services") or []:
        label = service.get("name", "<unnamed>")
        bad = set(service) - SERVICE_KEYS
        if bad:
            problems.append(
                f"service {label}: unsupported key(s) "
                f"{', '.join(sorted(bad))}"
            )
        if service.get("type") == "web" and "runtime" not in service:
            problems.append(f"service {label}: web services need a runtime")
        hc = service.get("healthCheckPath")
        if hc is not None and not hc.startswith("/"):
            problems.append(
                f"service {label}: healthCheckPath must start with '/'"
            )
    return problems


def check_against_schema(path: Path) -> str | None:
    """Authoritative check, if jsonschema and the network are both available."""
    try:
        import jsonschema  # noqa: F401
    except ImportError:
        return "skipped (jsonschema not installed)"
    try:
        with urllib.request.urlopen(SCHEMA_URL, timeout=20) as response:
            schema = json.load(response)
    except Exception as e:  # network, etc.
        return f"skipped (schema unreachable: {type(e).__name__})"

    import jsonschema as js
    try:
        js.validate(instance=yaml.safe_load(path.read_text(encoding="utf-8")),
                    schema=schema)
        return "schema: valid"
    except js.ValidationError as e:
        return f"schema: INVALID at {list(e.absolute_path)} — {e.message}"


def main() -> int:
    targets = sorted(Path(".").glob("**/render.yaml"))
    if not targets:
        print("no render.yaml found")
        return 1

    failed = False
    for path in targets:
        print(f"\n{path}")
        problems = check_structure(path)
        for problem in problems:
            print(f"  FAIL {problem}")
        verdict = check_against_schema(path)
        if verdict:
            print(f"  {verdict}")
        if not problems and verdict == "schema: valid":
            print("  OK")
        elif problems:
            failed = True
    return 1 if failed else 0


if __name__ == "__main__":
    raise SystemExit(main())