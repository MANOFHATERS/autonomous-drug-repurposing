#!/usr/bin/env python3
"""Generate ``frontend/contracts/api_contracts.ts`` from ``openapi.json``.

This is a minimal OpenAPI 3.1 → TypeScript generator. It exists because
the standard ``openapi-typescript`` npm package requires TypeScript 5
(its ``ts.factory`` API was removed in TypeScript 7, which this project
uses). When the upstream package adds TypeScript 7 support, this script
can be replaced with::

    npx openapi-typescript frontend/contracts/openapi.json \
        -o frontend/contracts/api_contracts.ts

Output structure (matches what openapi-typescript v7 would produce):

    export interface paths {
      "/health": {
        get: {
          responses: {
            200: {
              content: {
                "application/json": {
                  schema: components["schemas"]["HealthResponse"];
                };
              };
            };
          };
        };
      };
      // ...
    }

    export interface components {
      schemas: {
        HealthResponse: { status: string; service: string };
        // ...
      };
    }

    export interface operations {
      // Record<operationId, operation details> — useful for fetch wrappers
    }

The output is intentionally minimal — it captures the URL paths (the
most important contract: a URL change is now a compile error) and the
response shapes (best-effort from the JSON Schema). Pydantic models that
FastAPI emits are kept verbatim where possible.

Usage:
    python3 frontend/scripts/generate_api_contracts.py
"""
from __future__ import annotations

import json
import sys
from pathlib import Path
from typing import Any, Dict, List, Optional, Set

REPO_ROOT = Path(__file__).resolve().parents[2]
DEFAULT_OPENAPI = REPO_ROOT / "frontend" / "contracts" / "openapi.json"
DEFAULT_OUTPUT = REPO_ROOT / "frontend" / "contracts" / "api_contracts.ts"
DEFAULT_PREAMBLE = REPO_ROOT / "frontend" / "contracts" / "_api_contracts_preamble.ts"


# ─────────────────────────────────────────────────────────────────────────────
# JSON Schema → TypeScript type expression
# ─────────────────────────────────────────────────────────────────────────────
# OpenAPI 3.1 uses JSON Schema for response bodies and component schemas.
# We convert each schema to a TypeScript type expression. This is a minimal
# converter — it handles the subset of JSON Schema that FastAPI/Pydantic
# actually emits (object, array, string, number, integer, boolean, null,
# oneOf/anyOf/allOf, $ref, enum, additionalProperties).

def _ref_name(ref: str) -> str:
    """``#/components/schemas/Foo`` → ``Foo``."""
    return ref.rsplit("/", 1)[-1]


def schema_to_ts(schema: Optional[Dict[str, Any]], seen_refs: Set[str]) -> str:
    """Convert a JSON Schema dict to a TypeScript type expression."""
    if schema is None:
        return "unknown"
    if not isinstance(schema, dict):
        return "unknown"

    # $ref — reference a named schema in components.schemas.
    if "$ref" in schema:
        name = _ref_name(schema["$ref"])
        seen_refs.add(name)
        return f'components["schemas"]["{name}"]'

    # oneOf / anyOf → TypeScript union.
    for key in ("oneOf", "anyOf"):
        if key in schema:
            parts = [schema_to_ts(s, seen_refs) for s in schema[key]]
            return " | ".join(parts) if parts else "unknown"

    # allOf → TypeScript intersection.
    if "allOf" in schema:
        parts = [schema_to_ts(s, seen_refs) for s in schema["allOf"]]
        return " & ".join(f"({p})" for p in parts) if parts else "unknown"

    # enum — JSON Schema enum of literal values.
    if "enum" in schema:
        parts: List[str] = []
        for v in schema["enum"]:
            if isinstance(v, str):
                parts.append(f'"{v}"')
            elif isinstance(v, bool):
                parts.append("true" if v else "false")
            elif v is None:
                parts.append("null")
            else:
                parts.append(str(v))
        return " | ".join(parts) if parts else "unknown"

    t = schema.get("type")

    # type can be a list (e.g., ["string", "null"]) → union.
    if isinstance(t, list):
        parts = [_type_to_ts(x, schema) for x in t]
        return " | ".join(parts) if parts else "unknown"

    return _type_to_ts(t, schema, seen_refs)


def _type_to_ts(t: Optional[str], schema: Dict[str, Any], seen_refs: Optional[Set[str]] = None) -> str:
    """Convert a single JSON Schema ``type`` value to TypeScript."""
    if seen_refs is None:
        seen_refs = set()
    if t is None:
        # No type — fall back to const or unknown.
        if "const" in schema:
            v = schema["const"]
            if isinstance(v, str):
                return f'"{v}"'
            if isinstance(v, bool):
                return "true" if v else "false"
            if v is None:
                return "null"
            return str(v)
        return "unknown"
    if t == "object":
        return _object_to_ts(schema, seen_refs)
    if t == "array":
        items = schema.get("items")
        item_ts = schema_to_ts(items, seen_refs) if items else "unknown"
        return f"Array<{item_ts}>"
    if t == "string":
        return "string"
    if t in ("integer", "number"):
        return "number"
    if t == "boolean":
        return "boolean"
    if t == "null":
        return "null"
    return "unknown"


def _object_to_ts(schema: Dict[str, Any], seen_refs: Set[str]) -> str:
    """Convert a JSON Schema object type to a TypeScript object type."""
    properties = schema.get("properties") or {}
    required: List[str] = schema.get("required") or []

    # additionalProperties: true (or schema) → index signature.
    additional = schema.get("additionalProperties")
    has_index_sig = additional is not None and additional is not False

    lines: List[str] = []
    for prop_name, prop_schema in properties.items():
        is_required = prop_name in required
        ts_type = schema_to_ts(prop_schema, seen_refs)
        opt = "" if is_required else "?"
        # Quote the key if it's not a valid TS identifier.
        key = _ts_key(prop_name)
        lines.append(f"    {key}{opt}: {ts_type};")

    if has_index_sig:
        if isinstance(additional, dict):
            val_ts = schema_to_ts(additional, seen_refs)
        else:
            val_ts = "unknown"
        lines.append(f"    [key: string]: {val_ts};")

    if not lines:
        return "Record<string, never>"
    return "{\n" + "\n".join(lines) + "\n  }"


# ─────────────────────────────────────────────────────────────────────────────
# OpenAPI paths → TypeScript interface
# ─────────────────────────────────────────────────────────────────────────────

def _method_to_ts(method: str, op: Dict[str, Any], seen_refs: Set[str]) -> Optional[str]:
    """Convert a single (method, operation) pair to a TS interface body."""
    if not isinstance(op, dict):
        return None
    lines: List[str] = []
    op_id = op.get("operationId")
    if op_id:
        lines.append(f"    /** Operation ID: {op_id} */")
    summary = op.get("summary")
    if summary:
        lines.append(f"    /** {summary} */")
    # parameters
    params = op.get("parameters") or []
    if params:
        param_lines: List[str] = []
        for p in params:
            if not isinstance(p, dict):
                continue
            name = p.get("name", "param")
            required = p.get("required", False)
            schema = p.get("schema", {})
            ts_type = schema_to_ts(schema, seen_refs)
            opt = "" if required else "?"
            key = _ts_key(name)
            param_lines.append(f"      {key}{opt}: {ts_type};")
        if param_lines:
            lines.append("    parameters: {")
            lines.extend(param_lines)
            lines.append("    };")
    # requestBody
    rb = op.get("requestBody")
    if isinstance(rb, dict):
        content = rb.get("content", {})
        json_content = content.get("application/json", {})
        body_schema = json_content.get("schema") if isinstance(json_content, dict) else None
        if body_schema:
            body_ts = schema_to_ts(body_schema, seen_refs)
            lines.append(f"    requestBody: {body_ts};")
    # responses
    responses = op.get("responses") or {}
    if responses:
        lines.append("    responses: {")
        for code, resp in sorted(responses.items()):
            if not isinstance(resp, dict):
                continue
            content = resp.get("content", {})
            json_content = content.get("application/json", {})
            schema = json_content.get("schema") if isinstance(json_content, dict) else None
            resp_ts = schema_to_ts(schema, seen_refs) if schema else "unknown"
            lines.append(f"      {code}: {{ content: {{ \"application/json\": {{ schema: {resp_ts} }} }} }};")
        lines.append("    };")
    if not lines:
        return None
    return "\n".join(lines)


def paths_to_ts(paths: Dict[str, Any], seen_refs: Set[str]) -> str:
    """Convert the OpenAPI ``paths`` dict to a TypeScript ``paths`` interface."""
    out: List[str] = ["export interface paths {"]
    for path, path_item in sorted(paths.items()):
        if not isinstance(path_item, dict):
            continue
        # Quote the path key — it always contains "/" so it's never a valid
        # TS identifier.
        out.append(f'  "{path}": {{')
        for method in ("get", "post", "put", "delete", "patch", "head", "options"):
            op = path_item.get(method)
            if op is None:
                continue
            body = _method_to_ts(method, op, seen_refs)
            if body is None:
                out.append(f"    {method}: {{}};")
            else:
                out.append(f"    {method}: {{")
                out.append(body)
                out.append("    };")
        out.append("  };")
    out.append("}")
    return "\n".join(out)


# ─────────────────────────────────────────────────────────────────────────────
# Components.schemas → TypeScript interface
# ─────────────────────────────────────────────────────────────────────────────

def components_to_ts(components: Dict[str, Any], seen_refs: Set[str]) -> str:
    """Convert ``components.schemas`` to a TypeScript ``components`` interface."""
    schemas = (components or {}).get("schemas") or {}
    out: List[str] = ["export interface components {"]
    out.append("  schemas: {")
    for name, schema in sorted(schemas.items()):
        if not isinstance(schema, dict):
            continue
        ts_type = schema_to_ts(schema, seen_refs)
        # Use a multi-line layout when the type is itself multi-line.
        if "\n" in ts_type:
            out.append(f"    {name}: {ts_type};")
        else:
            out.append(f"    {name}: {ts_type};")
    out.append("  };")
    out.append("}")
    return "\n".join(out)


# ─────────────────────────────────────────────────────────────────────────────
# operations → TypeScript interface (operationId-keyed)
# ─────────────────────────────────────────────────────────────────────────────

def _is_valid_ts_identifier(name: str) -> bool:
    """Return True if ``name`` is a valid TypeScript identifier."""
    if not name:
        return False
    # First char must be letter, _, or $.
    if not (name[0].isalpha() or name[0] in "_$"):
        return False
    # Subsequent chars must be letter, digit, _, or $.
    for ch in name[1:]:
        if not (ch.isalnum() or ch in "_$"):
            return False
    return True


def _ts_key(name: str) -> str:
    """Quote ``name`` for use as a TypeScript object key if needed."""
    if _is_valid_ts_identifier(name):
        return name
    # Use JSON-style double-quoted string (handles escapes correctly).
    return json.dumps(name)


def operations_to_ts(paths: Dict[str, Any], seen_refs: Set[str]) -> str:
    """Convert paths to an ``operations`` interface keyed by ``operationId``."""
    out: List[str] = ["export interface operations {"]
    seen_ids: Set[str] = set()
    for path, path_item in sorted(paths.items()):
        if not isinstance(path_item, dict):
            continue
        for method in ("get", "post", "put", "delete", "patch", "head", "options"):
            op = path_item.get(method)
            if not isinstance(op, dict):
                continue
            op_id = op.get("operationId")
            if not op_id or op_id in seen_ids:
                continue
            seen_ids.add(op_id)
            body = _method_to_ts(method, op, seen_refs)
            key = _ts_key(op_id)
            if body is None:
                out.append(f"  {key}: {{}};")
            else:
                out.append(f"  {key}: {{")
                out.append(body)
                out.append("  };")
    out.append("}")
    return "\n".join(out)


# ─────────────────────────────────────────────────────────────────────────────
# Driver
# ─────────────────────────────────────────────────────────────────────────────

HEADER = """\
/**
 * frontend/contracts/api_contracts.ts
 * ===================================
 *
 * AUTO-GENERATED — DO NOT EDIT BY HAND.
 *
 * This file is regenerated from the four Python FastAPI services' OpenAPI
 * schemas by:
 *
 *     python3 frontend/scripts/extract_openapi.py   # builds openapi.json
 *     python3 frontend/scripts/generate_api_contracts.py
 *         # ↑ this file — converts openapi.json → api_contracts.ts
 *
 * Task 13.4 (SH-006) ROOT FIX (Teammate 13, v129):
 *   The previous file was 606 lines of hand-written TypeScript with
 *   interfaces that DIVERGED from what the Python services actually
 *   returned (the audit flagged phantom fields that Python never emitted).
 *   This file is now generated from the source of truth — the FastAPI
 *   apps' own ``app.openapi()`` output. Any change to a Python endpoint's
 *   URL, request body, or response shape is now a 2-file change: the
 *   Python service + this regenerated file. The CI check
 *   (``npm run check:contracts``) fails if the file is out of date.
 *
 * Why not use ``openapi-typescript`` (the npm package)?
 *   The upstream package requires TypeScript 5 (its ``ts.factory`` API
 *   was removed in TypeScript 7, which this project uses). When the
 *   upstream package adds TypeScript 7 support, this script can be
 *   replaced with:
 *       npx openapi-typescript frontend/contracts/openapi.json \\
 *           -o frontend/contracts/api_contracts.ts
 *   The output structure of this script matches openapi-typescript v7's
 *   output (``paths``, ``components``, ``operations`` interfaces) so the
 *   swap will be transparent to consumers.
 *
 * Coexistence with the hand-written URL constants below:
 *   The hand-written ``URL_KG_STATS`` etc. constants at the top of this
 *   file are kept for backwards compatibility with the Python contract
 *   consistency test (shared/tests/test_contract_consistency.py), which
 *   reads this file as text and verifies the URL string literals are
 *   present. They mirror shared/contracts/urls.py exactly. If you change
 *   a URL, change it in shared/contracts/urls.py AND regenerate this file.
 *
 * Generation timestamp: {timestamp}
 * OpenAPI schema source: frontend/contracts/openapi.json
 */

// ============================================================================
// CANONICAL URL PATHS — MUST match shared/contracts/urls.py exactly
// ============================================================================
// These constants are kept as plain string literals so the Python contract
// consistency test can read this file as text and verify the URLs match.
// They are NOT generated from OpenAPI — they are the canonical source that
// the Python services import from shared/contracts/urls.py.

/** Phase 2 KG service — graph stats (node/edge counts). */
export const URL_KG_STATS = "/kg/stats";

/** Phase 2 KG service — explore a node's neighborhood. */
export const URL_KG_EXPLORE = "/kg/explore";

/** Phase 3 GT service — predict drug-disease score. */
export const URL_PREDICT = "/predict";

/** Phase 3 GT service — top-k novel predictions. */
export const URL_TOP_K = "/top-k";

/** Phase 4 RL service — ranked candidates (composite score). */
export const URL_RANK = "/rank";

/** Phase 4 RL service — ranked candidates filtered by drug. */
export const URL_RANK_BY_DRUG = "/rank/{{drug}}";

/** Phase 4 RL service — validate a hypothesis (initiates writeback). */
export const URL_VALIDATE = "/validate";

/** All services — health check (liveness probe). */
export const URL_HEALTH = "/health";

/**
 * All canonical service URLs (for the contract consistency test).
 * MUST match ALL_SERVICE_URLS in shared/contracts/urls.py.
 */
export const ALL_SERVICE_URLS = [
  URL_KG_STATS,
  URL_KG_EXPLORE,
  URL_PREDICT,
  URL_TOP_K,
  URL_RANK,
  URL_RANK_BY_DRUG,
  URL_VALIDATE,
  URL_HEALTH,
] as const;

// ============================================================================
// DEFAULT SERVICE PORTS — MUST match shared/contracts/urls.py SERVICE_PORTS
// ============================================================================

export const SERVICE_PORTS = {{
  phase1_dataset: 8000,
  phase2_kg: 8001,
  phase3_gt: 8002,
  phase4_rl: 8003,
  airflow_webserver: 8080,
  mlflow_tracking: 5000,
  neo4j_bolt: 7687,
  neo4j_http: 7474,
  postgres: 5432,
  frontend: 3000,
}} as const;

export type ServiceName = keyof typeof SERVICE_PORTS;

// ============================================================================
// AUTO-GENERATED OPENAPI TYPES BELOW
// ============================================================================
// Everything from this point down is regenerated by
// ``frontend/scripts/generate_api_contracts.py``. Do not edit by hand —
// your changes will be overwritten on the next regeneration.

/* eslint-disable */
// @ts-nocheck — generated file; type errors here indicate an upstream
//               OpenAPI schema issue, not a code issue.
"""


def main() -> int:
    openapi_path = Path(sys.argv[1]).resolve() if len(sys.argv) > 1 else DEFAULT_OPENAPI
    output_path = Path(sys.argv[2]).resolve() if len(sys.argv) > 2 else DEFAULT_OUTPUT
    if not openapi_path.exists():
        print(f"ERROR: openapi.json not found at {openapi_path}", file=sys.stderr)
        print("Run `python3 frontend/scripts/extract_openapi.py` first.", file=sys.stderr)
        return 1
    spec = json.loads(openapi_path.read_text(encoding="utf-8"))
    seen_refs: Set[str] = set()

    from datetime import datetime, timezone
    timestamp = datetime.now(timezone.utc).strftime("%Y-%m-%d %H:%M:%S UTC")

    header = HEADER.format(timestamp=timestamp)
    paths_ts = paths_to_ts(spec.get("paths", {}), seen_refs)
    components_ts = components_to_ts(spec.get("components", {}), seen_refs)
    operations_ts = operations_to_ts(spec.get("paths", {}), seen_refs)

    # Footer: list of referenced schema names (for debugging).
    footer_lines = [
        "",
        "// ────────────────────────────────────────────────────────────────────",
        "// Referenced schema names (debugging aid — not used at runtime)",
        "// ────────────────────────────────────────────────────────────────────",
        f"// {', '.join(sorted(seen_refs)) if seen_refs else '(none)'}",
        "",
    ]

    output = "\n\n".join([
        header,
        paths_ts,
        "",
        components_ts,
        "",
        operations_ts,
        "\n".join(footer_lines),
    ])
    output_path.parent.mkdir(parents=True, exist_ok=True)
    output_path.write_text(output, encoding="utf-8")
    print(f"Wrote {output_path} ({len(output)} bytes, {len(seen_refs)} schema refs)")
    return 0


if __name__ == "__main__":
    sys.exit(main())
