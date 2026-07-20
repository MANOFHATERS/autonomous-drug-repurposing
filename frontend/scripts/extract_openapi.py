#!/usr/bin/env python3
"""Extract OpenAPI schemas from the four Python FastAPI services.

Usage:
    python3 frontend/scripts/extract_openapi.py [output_path]

Defaults to writing `frontend/contracts/openapi.json`.

The script imports each service's FastAPI ``app`` object and calls
``app.openapi()`` to extract its schema. For services that cannot be
imported (missing optional deps like ``torch`` or ``gymnasium``), the
script falls back to AST-parsing the source file and synthesizing a
minimal OpenAPI schema from the ``@app.get`` / ``@app.post`` decorators.

The output is a single combined OpenAPI 3.1 document with each service's
paths namespaced under a tag matching the service name. Frontend code
generates TypeScript types from this file via ``openapi-typescript``.
"""
from __future__ import annotations

import ast
import json
import logging
import os
import sys
from pathlib import Path
from typing import Any, Dict, Optional

# Make the repo root importable so ``import phase1.service`` etc. work.
REPO_ROOT = Path(__file__).resolve().parents[2]
if str(REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(REPO_ROOT))

logger = logging.getLogger("extract_openapi")
logging.basicConfig(level=logging.INFO, format="%(levelname)s %(name)s: %(message)s")


# ─────────────────────────────────────────────────────────────────────────────
# Service registry
# ─────────────────────────────────────────────────────────────────────────────
# Each entry: (module_path, FastAPI_app_attr_name, source_file_path, tag)
# The ``tag`` becomes the OpenAPI ``tags`` value for all paths from that
# service — this lets the generated TypeScript file group endpoints by
# service, which matches how the frontend's lib/services/*.ts modules are
# organized. The tag is also prefixed to schema names in
# ``components.schemas`` to avoid name collisions across services —
# therefore the tag MUST be a valid TypeScript identifier (no hyphens).
SERVICES = [
    ("phase1.service", "app", REPO_ROOT / "phase1" / "service.py", "phase1"),
    ("phase2.service", "app", REPO_ROOT / "phase2" / "service.py", "phase2"),
    ("graph_transformer.service", "app", REPO_ROOT / "graph_transformer" / "service.py", "phase3_gt"),
    ("rl.service", "app", REPO_ROOT / "rl" / "service.py", "phase4_rl"),
]


def try_import_openapi(module_path: str, app_attr: str) -> Optional[Dict[str, Any]]:
    """Try to import a service's FastAPI app and return its OpenAPI schema.

    Returns ``None`` if the import fails (e.g., missing optional dep).
    The caller then falls back to AST parsing.
    """
    try:
        mod = __import__(module_path, fromlist=[app_attr])
    except Exception as exc:  # pragma: no cover — defensive, many failure modes
        logger.warning(
            "Could not import %s (%s: %s) — falling back to AST parsing",
            module_path, type(exc).__name__, exc,
        )
        return None
    app_obj = getattr(mod, app_attr, None)
    if app_obj is None:
        logger.warning(
            "Module %s has no attribute %s — falling back to AST parsing",
            module_path, app_attr,
        )
        return None
    # FastAPI apps expose ``.openapi()`` which returns the schema dict.
    openapi_fn = getattr(app_obj, "openapi", None)
    if openapi_fn is None or not callable(openapi_fn):
        logger.warning(
            "Module %s app object has no openapi() method — falling back", module_path,
        )
        return None
    try:
        schema = openapi_fn()
    except Exception as exc:  # pragma: no cover
        logger.warning(
            "app.openapi() raised for %s (%s: %s) — falling back",
            module_path, type(exc).__name__, exc,
        )
        return None
    if not isinstance(schema, dict) or "paths" not in schema:
        logger.warning("Schema from %s is malformed — falling back", module_path)
        return None
    return schema


# ─────────────────────────────────────────────────────────────────────────────
# AST fallback for services with heavy deps (torch, gymnasium)
# ─────────────────────────────────────────────────────────────────────────────
# When we can't import a service (e.g., ``graph_transformer`` needs torch),
# we parse its source code and extract the route decorators:
#   @app.get("/path")
#   @app.post("/path")
#   @app.put("/path")
#   @app.delete("/path")
#   @app.patch("/path")
# For each, we synthesize a minimal OpenAPI path item with the method,
# a generic 200 response, and the service tag. This gives the frontend
# type-safe URL constants even when the full Pydantic models can't be
# introspected.
#
# Two complications handled here:
#   1. Path arguments can be STRING LITERALS (``"/predict"``) or VARIABLE
#      REFERENCES (``_URL_PREDICT``). rl/service.py uses the variable form
#      so it can fall back to a string when ``shared.contracts.urls`` isn't
#      importable. We collect module-level string assignments on the first
#      pass and resolve them on the second pass.
#   2. Path arguments can also be f-strings or concatenations — we skip
#      those rather than trying to evaluate them (they're rare and usually
#      indicate dynamic routing that can't be statically typed anyway).

_ROUTE_METHODS = ("get", "post", "put", "delete", "patch", "head", "options")


def _extract_string_arg(call_node: ast.Call, const_map: Dict[str, str]) -> Optional[str]:
    """Return the first string literal OR resolved-variable arg, if any."""
    for arg in call_node.args:
        if isinstance(arg, ast.Constant) and isinstance(arg.value, str):
            return arg.value
        # ``_URL_X`` (a module-level Name) — look up in const_map.
        if isinstance(arg, ast.Name) and arg.id in const_map:
            return const_map[arg.id]
    return None


def _collect_module_string_consts(tree: ast.Module) -> Dict[str, str]:
    """Walk top-level statements and collect ``NAME = "literal"`` assignments.

    Handles both plain assignments (``_URL_X = "/x"``) and tuple unpacking
    (``_URL_X, _URL_Y = "/x", "/y"`` — rare but possible). Does NOT follow
    imports — those are resolved by the import-time path (when the service
    CAN be imported, we use the live ``app.openapi()`` output instead).
    """
    const_map: Dict[str, str] = {}
    for node in tree.body:
        if isinstance(node, ast.Assign):
            targets = node.targets
            value = node.value
            if isinstance(value, ast.Constant) and isinstance(value.value, str):
                for tgt in targets:
                    if isinstance(tgt, ast.Name):
                        const_map[tgt.id] = value.value
        # Try/except blocks (the rl/service.py fallback pattern):
        #   try:
        #       from shared.contracts.urls import URL_HEALTH as _URL_HEALTH
        #   except ImportError:
        #       _URL_HEALTH = "/health"
        # We walk the body of every ExceptHandler and collect assignments
        # there too — those are the fallback literal definitions.
        if isinstance(node, ast.Try):
            for handler in node.handlers:
                for inner in handler.body:
                    if isinstance(inner, ast.Assign):
                        value = inner.value
                        if isinstance(value, ast.Constant) and isinstance(value.value, str):
                            for tgt in inner.targets:
                                if isinstance(tgt, ast.Name):
                                    # Only set if not already present (the
                                    # imported value is preferred when the
                                    # service can be imported; for AST we
                                    # only have the fallback literal).
                                    const_map.setdefault(tgt.id, value.value)
    return const_map


def _extract_decorator_route(dec: ast.expr, const_map: Dict[str, str]) -> Optional[tuple[str, str]]:
    """If ``dec`` is ``@app.get("/path")`` etc., return (method, path).

    Returns ``None`` for non-route decorators (e.g., ``@app.middleware``).
    """
    if not isinstance(dec, ast.Call):
        return None
    func = dec.func
    # ``app.get(...)`` → ast.Attribute(value=Name("app"), attr="get")
    if not isinstance(func, ast.Attribute):
        return None
    if func.attr not in _ROUTE_METHODS:
        return None
    path = _extract_string_arg(dec, const_map)
    if path is None:
        return None
    return (func.attr, path)


def ast_extract_paths(source_path: Path, tag: str) -> Dict[str, Dict[str, Any]]:
    """Parse a Python source file and return an OpenAPI ``paths`` dict."""
    try:
        source = source_path.read_text(encoding="utf-8")
    except OSError as exc:
        logger.warning("Could not read %s: %s", source_path, exc)
        return {}
    try:
        tree = ast.parse(source, filename=str(source_path))
    except SyntaxError as exc:
        logger.warning("SyntaxError in %s: %s", source_path, exc)
        return {}

    # Pass 1: collect module-level string constants (for variable resolution).
    const_map = _collect_module_string_consts(tree)
    if const_map:
        logger.info(
            "AST: collected %d module-level string constants from %s",
            len(const_map), source_path.name,
        )

    # Pass 2: walk function defs and extract route decorators.
    paths: Dict[str, Dict[str, Any]] = {}
    for node in ast.walk(tree):
        if not isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)):
            continue
        for dec in node.decorator_list:
            route = _extract_decorator_route(dec, const_map)
            if route is None:
                continue
            method, path = route
            path_item = paths.setdefault(path, {})
            # OpenAPI method key is lowercase.
            path_item[method] = {
                "operationId": f"{tag}_{node.name}",
                "summary": f"{tag} {method.upper()} {path} (AST-extracted)",
                "description": (
                    f"Endpoint extracted from {source_path.name}::{node.name} "
                    f"via AST parsing (service could not be imported — "
                    f"likely missing optional Python deps like torch/gymnasium)."
                ),
                "tags": [tag],
                "responses": {
                    "200": {
                        "description": "Successful response",
                        "content": {
                            "application/json": {
                                "schema": {"type": "object"},
                            },
                        },
                    },
                },
            }
    return paths


# ─────────────────────────────────────────────────────────────────────────────
# Combine + emit
# ─────────────────────────────────────────────────────────────────────────────

def build_combined_openapi() -> Dict[str, Any]:
    """Build a single OpenAPI 3.1 document from all four services."""
    combined_paths: Dict[str, Dict[str, Any]] = {}
    combined_schemas: Dict[str, Any] = {}
    combined_tags: list[Dict[str, str]] = []
    seen_path_methods: set[tuple[str, str]] = set()

    for module_path, app_attr, source_path, tag in SERVICES:
        combined_tags.append({"name": tag, "description": f"{tag} service endpoints"})
        schema = try_import_openapi(module_path, app_attr)
        if schema is not None:
            logger.info("Imported %s — %d paths", module_path, len(schema.get("paths", {})))
            for path, path_item in schema.get("paths", {}).items():
                path_item_dict: Dict[str, Any] = combined_paths.setdefault(path, {})
                for method, op in path_item.items():
                    if method in ("parameters", "summary", "description"):
                        # Path-level (not method-level) keys — copy across.
                        path_item_dict.setdefault(method, op)
                        continue
                    if (path, method) in seen_path_methods:
                        # Duplicate route — keep the first one we saw.
                        continue
                    seen_path_methods.add((path, method))
                    # Ensure the tag is set so the frontend can group.
                    if isinstance(op, dict):
                        op_tags = op.setdefault("tags", [])
                        if tag not in op_tags:
                            op_tags.append(tag)
                    path_item_dict[method] = op
            # Merge component schemas (Pydantic models). Don't prefix —
            # $ref references inside the same service's schema use the
            # unprefixed name (e.g. ``#/components/schemas/HTTPValidationError``)
            # and we'd break resolution by renaming. Collisions across
            # services (e.g., both phase1 and phase2 emit HTTPValidationError)
            # are deduplicated by ``setdefault`` — the first definition wins,
            # which is fine because FastAPI generates these model classes
            # identically across services.
            components = schema.get("components", {})
            for name, definition in components.get("schemas", {}).items():
                combined_schemas.setdefault(name, definition)
        else:
            logger.info("AST-parsing %s", source_path)
            ast_paths = ast_extract_paths(source_path, tag)
            for path, path_item in ast_paths.items():
                path_item_dict = combined_paths.setdefault(path, {})
                for method, op in path_item.items():
                    if (path, method) in seen_path_methods:
                        continue
                    seen_path_methods.add((path, method))
                    path_item_dict[method] = op

    return {
        "openapi": "3.1.0",
        "info": {
            "title": "Autonomous Drug Repurposing Platform — Combined API",
            "version": "1.0.0",
            "description": (
                "Combined OpenAPI schema for all four Python services "
                "(phase1 dataset, phase2 KG, phase3 graph transformer, "
                "phase4 RL ranker). Auto-generated by "
                "frontend/scripts/extract_openapi.py — do not edit by hand."
            ),
        },
        "tags": combined_tags,
        "paths": combined_paths,
        "components": {"schemas": combined_schemas} if combined_schemas else {},
    }


def main() -> int:
    out_path_arg = sys.argv[1] if len(sys.argv) > 1 else None
    out_path = Path(out_path_arg).resolve() if out_path_arg else (
        REPO_ROOT / "frontend" / "contracts" / "openapi.json"
    )
    out_path.parent.mkdir(parents=True, exist_ok=True)
    combined = build_combined_openapi()
    out_path.write_text(json.dumps(combined, indent=2, sort_keys=True), encoding="utf-8")
    logger.info(
        "Wrote %d paths / %d schemas to %s",
        len(combined.get("paths", {})),
        len(combined.get("components", {}).get("schemas", {})),
        out_path,
    )
    return 0


if __name__ == "__main__":
    sys.exit(main())
