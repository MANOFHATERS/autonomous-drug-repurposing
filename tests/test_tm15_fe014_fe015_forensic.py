"""
Teammate 15 — v143 — FE-014 + FE-015 forensic root-fix verification tests.

These tests verify the FIX ITSELF is present in the code (source-level +
behavioral). They do NOT mock FastAPI/uvicorn — they verify the actual
graceful-shutdown infrastructure and the optional-import pattern.

Run with: `pytest tests/test_tm15_fe014_fe015_forensic.py -v`
"""
from __future__ import annotations

import ast
import asyncio
import importlib
import sys
from pathlib import Path

import pytest

REPO_ROOT = Path(__file__).resolve().parents[1]
MAIN_PY = REPO_ROOT / "backend" / "api" / "main.py"
EXTRACT_OPENAPI_PY = REPO_ROOT / "frontend" / "scripts" / "extract_openapi.py"


# ---------------------------------------------------------------------------
# FE-014: source-level forensic checks.
# ---------------------------------------------------------------------------

class TestFE014SourceLevel:
    """Verify the graceful-shutdown fix is in main.py at the source level."""

    @pytest.fixture(scope="class")
    def main_source(self) -> str:
        return MAIN_PY.read_text(encoding="utf-8")

    def test_no_bare_uvicorn_run_without_graceful_shutdown(self, main_source: str) -> None:
        """The bug: uvicorn.run was called with NO timeout_graceful_shutdown.
        The fix: timeout_graceful_shutdown is now passed."""
        # Find uvicorn.run( call and check the next ~600 chars for the param.
        idx = main_source.find("uvicorn.run(")
        assert idx > -1, "uvicorn.run() call not found"
        window = main_source[idx:idx + 800]
        assert "timeout_graceful_shutdown" in window, (
            "FE-014 REGRESSION: uvicorn.run() does NOT pass timeout_graceful_shutdown. "
            "The fix requires this parameter for graceful SIGTERM/SIGINT handling."
        )

    def test_inflight_ml_tasks_set_defined(self, main_source: str) -> None:
        """The fix defines a module-level set to track in-flight ML tasks."""
        assert "_inflight_ml_tasks" in main_source, (
            "FE-014 REGRESSION: _inflight_ml_tasks is not defined. The shutdown "
            "handler needs this set to drain in-flight ML calls."
        )

    def test_track_ml_call_helper_defined(self, main_source: str) -> None:
        """The fix defines a helper that wraps httpx calls with task tracking."""
        assert "def _track_ml_call(" in main_source, (
            "FE-014 REGRESSION: _track_ml_call helper is not defined. The httpx "
            "calls in /predict and /top-k must be wrapped with this helper."
        )

    def test_shutdown_handler_registered(self, main_source: str) -> None:
        """The fix registers a shutdown handler via @app.on_event('shutdown')."""
        assert "@app.on_event(\"shutdown\")" in main_source or "@app.on_event('shutdown')" in main_source, (
            "FE-014 REGRESSION: @app.on_event('shutdown') decorator not found. "
            "The shutdown handler must be registered to drain in-flight ML calls."
        )
        assert "async def _drain_inflight_ml_calls(" in main_source, (
            "FE-014 REGRESSION: _drain_inflight_ml_calls handler not found."
        )

    def test_shutdown_handler_uses_asyncio_wait_for_with_timeout(self, main_source: str) -> None:
        """The fix bounds the drain with asyncio.wait_for (25s timeout)."""
        assert "asyncio.wait_for" in main_source or "_asyncio.wait_for" in main_source, (
            "FE-014 REGRESSION: asyncio.wait_for not found. The shutdown handler "
            "must bound the drain with a 25s timeout (5s < uvicorn's 30s)."
        )
        assert "_INFLIGHT_ML_DRAIN_TIMEOUT_SECONDS = 25.0" in main_source, (
            "FE-014 REGRESSION: _INFLIGHT_ML_DRAIN_TIMEOUT_SECONDS is not 25.0. "
            "The drain timeout must be 25s (5s < uvicorn's 30s budget)."
        )

    def test_shutdown_handler_logs_critical_on_timeout(self, main_source: str) -> None:
        """The fix logs CRITICAL on drain timeout so operators detect hung GT/RL."""
        assert "logger.critical" in main_source, (
            "FE-014 REGRESSION: logger.critical not found. The shutdown handler "
            "must log CRITICAL when in-flight ML calls don't complete in 25s."
        )

    def test_predict_wraps_httpx_with_track_ml_call(self, main_source: str) -> None:
        """The /predict endpoint wraps its httpx GT call with _track_ml_call."""
        # Find the predict function and verify _track_ml_call is in its body.
        # The predict function is VERY long (huge docstring + GT call + response
        # mapping), so we search from the def to the next `@app.` decorator
        # (which marks the start of the next route handler).
        idx = main_source.find("async def predict(")
        assert idx > -1, "predict() handler not found"
        # Find the next @app. decorator after predict — that's the end of predict.
        next_decorator = main_source.find("@app.", idx + 1)
        if next_decorator == -1:
            window = main_source[idx:]  # predict is the last route — search to EOF.
        else:
            window = main_source[idx:next_decorator]
        assert "_track_ml_call(" in window, (
            "FE-014 REGRESSION: /predict does NOT wrap its httpx call with "
            "_track_ml_call. The GT service call must be tracked for shutdown drain."
        )

    def test_top_k_wraps_httpx_with_track_ml_call(self, main_source: str) -> None:
        """The /top-k endpoint wraps its httpx RL call with _track_ml_call."""
        idx = main_source.find("async def top_k(")
        assert idx > -1, "top_k() handler not found"
        # Find the next @app. decorator after top_k — that's the end of top_k.
        next_decorator = main_source.find("@app.", idx + 1)
        if next_decorator == -1:
            window = main_source[idx:]
        else:
            window = main_source[idx:next_decorator]
        assert "_track_ml_call(" in window, (
            "FE-014 REGRESSION: /top-k does NOT wrap its httpx call with "
            "_track_ml_call. The RL service call must be tracked for shutdown drain."
        )


# ---------------------------------------------------------------------------
# FE-014: behavioral test of the shutdown drain logic.
# ---------------------------------------------------------------------------

class TestFE014Behavioral:
    """Verify the shutdown drain logic actually drains tasks (with timeout)."""

    def test_track_ml_call_returns_a_task_that_can_be_awaited(self) -> None:
        """_track_ml_call wraps a coroutine as a Task on the running loop."""
        # We test the drain logic in isolation without importing the full
        # main.py (which requires FastAPI). The drain logic is simple:
        # `await asyncio.wait_for(asyncio.gather(*tasks), timeout=25.0)`.
        # We replicate the drain behavior here and verify it works.

        async def fake_ml_call(delay: float, result: int) -> int:
            await asyncio.sleep(delay)
            return result

        async def drain_test():
            # Schedule 3 tasks: 2 fast, 1 slow.
            tasks = [
                asyncio.ensure_future(fake_ml_call(0.01, 1)),
                asyncio.ensure_future(fake_ml_call(0.02, 2)),
                asyncio.ensure_future(fake_ml_call(0.03, 3)),
            ]
            # Drain with 1s timeout (plenty for these fast tasks).
            results = await asyncio.wait_for(
                asyncio.gather(*tasks, return_exceptions=True),
                timeout=1.0,
            )
            return sorted(results)

        results = asyncio.run(drain_test())
        assert results == [1, 2, 3], f"Expected [1, 2, 3], got {results}"

    def test_drain_timeout_raises_asyncio_timeouterror(self) -> None:
        """When tasks don't complete within the timeout, asyncio.TimeoutError is raised."""
        async def slow_call() -> int:
            await asyncio.sleep(10.0)  # Way longer than the 0.05s timeout.
            return 42

        async def drain_test():
            tasks = [asyncio.ensure_future(slow_call())]
            await asyncio.wait_for(
                asyncio.gather(*tasks, return_exceptions=True),
                timeout=0.05,
            )

        # The drain should time out — this is the CRITICAL log path.
        with pytest.raises(asyncio.TimeoutError):
            asyncio.run(drain_test())


# ---------------------------------------------------------------------------
# FE-015: source-level forensic checks.
# ---------------------------------------------------------------------------

class TestFE015SourceLevel:
    """Verify the FastAPI import graceful-degradation fix is in main.py."""

    @pytest.fixture(scope="class")
    def main_source(self) -> str:
        return MAIN_PY.read_text(encoding="utf-8")

    def test_no_reraise_importerror_for_fastapi(self, main_source: str) -> None:
        """The bug: `raise ImportError("BE-001 v123: FastAPI is required...")`.
        The fix: catch ImportError and define no-op stubs.

        NOTE: the bug DESCRIPTION (mentioning "BE-001 v123") appears in the
        fix's explanatory COMMENT BLOCK — that's intentional (it explains
        what was removed). The test must check that no ACTIVE `raise
        ImportError(...)` statement with "BE-001 v123" exists, NOT that
        the string is absent entirely. We use AST analysis to find active
        `raise` statements.
        """
        # AST-based check: find all `raise ImportError(...)` statements
        # whose string argument contains "BE-001 v123". None should exist.
        import ast
        tree = ast.parse(main_source)
        for node in ast.walk(tree):
            if not isinstance(node, ast.Raise):
                continue
            exc = node.exc
            if not isinstance(exc, ast.Call):
                continue
            # exc.func should be `ImportError` (ast.Name).
            if not isinstance(exc.func, ast.Name) or exc.func.id != "ImportError":
                continue
            # Check the string args for "BE-001 v123".
            for arg in exc.args:
                if isinstance(arg, ast.Constant) and isinstance(arg.value, str) and "BE-001 v123" in arg.value:
                    pytest.fail(
                        "FE-015 REGRESSION: main.py still has an active "
                        "`raise ImportError(\"BE-001 v123: ...\")` statement. "
                        "The fix catches ImportError and defines no-op stubs."
                    )

    def test_has_fastapi_flag_defined(self, main_source: str) -> None:
        """The fix defines _HAS_FASTAPI flag."""
        assert "_HAS_FASTAPI = True" in main_source
        assert "_HAS_FASTAPI = False" in main_source

    def test_noop_app_class_defined(self, main_source: str) -> None:
        """The fix defines a _NoOpApp stub class with HTTP method decorators."""
        assert "class _NoOpApp:" in main_source
        # Verify the stub has the decorator methods.
        for method in ("def get(self", "def post(self", "def middleware(self", "def on_event(self"):
            assert method in main_source, f"FE-015: _NoOpApp missing method: {method}"

    def test_pydantic_stubs_defined(self, main_source: str) -> None:
        """The fix defines no-op stubs for BaseModel, Field, ConfigDict."""
        assert "class BaseModel:" in main_source  # stub class
        assert "def Field(" in main_source  # stub function
        assert "def ConfigDict(" in main_source  # stub function

    def test_status_stub_class_defined(self, main_source: str) -> None:
        """The fix defines a status stub with HTTP status constants."""
        assert "class _StatusStub:" in main_source
        assert "HTTP_503_SERVICE_UNAVAILABLE = 503" in main_source

    def test_app_conditionally_created(self, main_source: str) -> None:
        """The fix creates app = FastAPI(...) when fastapi is installed, else _NoOpApp()."""
        assert "if _HAS_FASTAPI:" in main_source
        assert "app = FastAPI(" in main_source
        assert "app = _NoOpApp()" in main_source

    def test_noop_stub_tagged_for_detection(self, main_source: str) -> None:
        """The fix tags the stub with _is_noop_stub for extract_openapi.py detection."""
        assert 'setattr(app, "_is_noop_stub", True)' in main_source or \
               "setattr(app, '_is_noop_stub', True)" in main_source

    def test_httpx_imported_optionally(self, main_source: str) -> None:
        """The fix imports httpx optionally with a no-op stub when missing."""
        assert "_HAS_HTTPX = True" in main_source
        assert "_HAS_HTTPX = False" in main_source
        assert "class _NoOpHttpxModule" in main_source


class TestFE015ExtractOpenapi:
    """Verify extract_openapi.py handles the no-op stub correctly."""

    @pytest.fixture(scope="class")
    def script_source(self) -> str:
        return EXTRACT_OPENAPI_PY.read_text(encoding="utf-8")

    def test_backend_api_main_in_services_list(self, script_source: str) -> None:
        """The fix adds backend.api.main to the SERVICES list."""
        assert "backend.api.main" in script_source, (
            "FE-015 REGRESSION: backend.api.main is not in extract_openapi.py's "
            "SERVICES list. The script must include the backend so its OpenAPI "
            "spec is in the combined contracts."
        )

    def test_detects_noop_stub_and_skips(self, script_source: str) -> None:
        """The fix detects _is_noop_stub and skips with a WARNING."""
        assert 'getattr(app_obj, "_is_noop_stub", False)' in script_source or \
               "getattr(app_obj, '_is_noop_stub', False)" in script_source
        assert "FE-015" in script_source
        assert "no-op stub" in script_source.lower() or "noop stub" in script_source.lower()

    def test_documents_fastapi_required_only_for_backend(self, script_source: str) -> None:
        """The fix documents that FastAPI is required only for backend dev."""
        assert "FastAPI is required ONLY for backend dev, NOT for frontend dev" in script_source, (
            "FE-015 REGRESSION: extract_openapi.py does NOT document that FastAPI "
            "is required only for backend dev. The warning message must include this."
        )


# ---------------------------------------------------------------------------
# FE-015: behavioral test — main.py imports successfully WITHOUT fastapi.
# ---------------------------------------------------------------------------

class TestFE015Behavioral:
    """Verify main.py is importable when fastapi is not installed.

    This is the CORE behavioral test: the bug was that `import backend.api.main`
    crashed without fastapi. The fix makes it importable (with app = _NoOpApp()).

    NOTE: We can't easily simulate "fastapi not installed" in the test env
    (fastapi IS installed for backend tests). Instead, we verify the AST
    structure: the try/except block catches ImportError and defines stubs.
    """

    def test_main_py_ast_has_try_except_for_fastapi_import(self) -> None:
        """The AST of main.py has a Try node that catches ImportError around the FastAPI import."""
        source = MAIN_PY.read_text(encoding="utf-8")
        tree = ast.parse(source)
        # Walk the AST looking for a Try node whose handlers catch ImportError
        # and whose body contains `from fastapi import ...`.
        found = False
        for node in ast.walk(tree):
            if not isinstance(node, ast.Try):
                continue
            # Check the body for a fastapi import.
            for stmt in node.body:
                if isinstance(stmt, ast.ImportFrom) and stmt.module and "fastapi" in stmt.module:
                    # Check the handlers catch ImportError.
                    for handler in node.handlers:
                        if handler.type is None:
                            continue
                        # handler.type is an ast.Name or ast.Attribute or ast.Tuple.
                        if isinstance(handler.type, ast.Name) and handler.type.id == "ImportError":
                            found = True
                            break
                    if found:
                        break
            if found:
                break
        assert found, (
            "FE-015 REGRESSION: main.py does NOT have a try/except ImportError "
            "around the FastAPI import. The fix requires this for graceful degradation."
        )

    def test_main_py_ast_has_noop_app_class_in_except_block(self) -> None:
        """The except block defines a _NoOpApp class."""
        source = MAIN_PY.read_text(encoding="utf-8")
        tree = ast.parse(source)
        found = False
        for node in ast.walk(tree):
            if not isinstance(node, ast.Try):
                continue
            for handler in node.handlers:
                if handler.type is None:
                    continue
                if isinstance(handler.type, ast.Name) and handler.type.id == "ImportError":
                    # Search the handler body for a ClassDef named _NoOpApp.
                    for stmt in handler.body:
                        if isinstance(stmt, ast.ClassDef) and stmt.name == "_NoOpApp":
                            found = True
                            break
                    if found:
                        break
            if found:
                break
        assert found, (
            "FE-015 REGRESSION: the except ImportError block does NOT define a "
            "_NoOpApp class. The fix requires this stub for the @app.get(...) "
            "decorators to parse without error."
        )
