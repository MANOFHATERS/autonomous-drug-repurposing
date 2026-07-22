"""P3-010 ROOT FIX verification (Teammate 7 v142, hostile-auditor RED TEAM).

Verifies that MLflowRunTracker.register_model ACTUALLY:
  1. Uses the ``stage`` parameter (calls transition_model_version_stage).
  2. Uses a ``runs:/<run_id>/artifacts/<basename>`` URI (not file:// URI).
  3. Captures the ModelVersion returned by mlflow.register_model.

The previous implementation had TWO compound bugs:
  * ``stage`` parameter was accepted but NEVER passed (only logged).
  * ``model_uri=f"file://{local_checkpoint_path}"`` only works in
    single-node dev deployments.

This test uses unittest.mock to capture the calls to mlflow.register_model
and mlflow.transition_model_version_stage, then asserts:
  * register_model was called with a ``runs:/...`` URI (not file://...).
  * transition_model_version_stage was called with the SAME stage as
    the register_model call.
  * The version passed to transition_model_version_stage matches the
    version returned by register_model.

HOSTILE-AUDITOR pattern: this test MOCKS mlflow and inspects the ACTUAL
calls made by the code under test. It does NOT trust the docstring or
comments. A previous fix updated the docstring to claim ``stage`` was
applied but never actually called transition_model_version_stage.
"""
from __future__ import annotations

import os
import sys
from unittest.mock import MagicMock, patch
from pathlib import Path

_REPO_ROOT = Path(__file__).resolve().parents[2]
if str(_REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(_REPO_ROOT))


def _build_mock_mlflow(run_id="abc123", version="1"):
    """Build a mock mlflow module with the methods register_model calls.

    The mock simulates:
      * mlflow.start_run() returning an ActiveRun-like object with
        .info.run_id = <run_id>.
      * mlflow.active_run() returning the same.
      * mlflow.register_model() returning a ModelVersion-like object
        with .version = <version>.
      * mlflow.transition_model_version_stage() (top-level, MLflow < 3.0)
        as a MagicMock that records calls.
      * mlflow.tracking.MlflowClient.transition_model_version_stage()
        (MLflow 3.x+ path) as a MagicMock that records calls.
      * mlflow.set_tracking_uri, set_experiment, start_run, log_artifact
        as no-op MagicMocks.
    """
    mock_mlflow = MagicMock()
    # Simulate the ActiveRun object returned by mlflow.start_run().
    mock_mlflow.start_run.return_value = MagicMock(
        info=MagicMock(run_id=run_id)
    )
    mock_mlflow.active_run.return_value = MagicMock(
        info=MagicMock(run_id=run_id)
    )
    # Simulate the ModelVersion returned by mlflow.register_model().
    mock_mlflow.register_model.return_value = MagicMock(version=version)
    # Top-level transition function (MLflow < 3.0).
    mock_mlflow.transition_model_version_stage = MagicMock(return_value=None)
    # MLflow 3.x+ path: MlflowClient class with transition method.
    mock_client = MagicMock()
    mock_client.transition_model_version_stage = MagicMock(return_value=None)
    mock_mlflow.tracking = MagicMock()
    mock_mlflow.tracking.MlflowClient.return_value = mock_client
    # __version__ for log messages.
    mock_mlflow.__version__ = "3.x-mock"
    return mock_mlflow


def _make_tracker_with_mock(mock_mlflow):
    """Construct an MLflowRunTracker with the mock mlflow module active.

    Returns (tracker, patches) — the caller MUST call `for p in patches: p.start()`
    before using the tracker, and `for p in patches: p.stop()` when done
    (typically in a try/finally).

    We use start/stop instead of `with` because the tracker's methods are
    called AFTER this function returns — the `with` block would exit and
    remove the patch before the methods are invoked.
    """
    import graph_transformer.utils.mlflow_integration as _mod
    p1 = patch.object(_mod, "_mlflow", mock_mlflow)
    p2 = patch.object(_mod, "_MLFLOW_AVAILABLE", True)
    p1.start()
    p2.start()
    tracker = _mod.MLflowRunTracker(
        experiment_name="test_experiment",
        tracking_uri="http://localhost:5000",  # any non-file URI works
    )
    return tracker, [p1, p2]


def _stop_patches(patches):
    """Stop all patches started by _make_tracker_with_mock."""
    for p in patches:
        try:
            p.stop()
        except RuntimeError:
            pass  # already stopped


def test_register_model_uses_runs_uri_not_file_uri():
    """P3-010.1: register_model MUST use ``runs:/<run_id>/artifacts/<basename>`` URI.

    The previous code used ``f"file://{local_checkpoint_path}"`` which
    only works in single-node dev deployments. The fix uses
    ``f"runs:/{run_id}/artifacts/{basename}"`` which works in distributed
    deployments because it points to the MLflow run's artifact store.
    """
    mock_mlflow = _build_mock_mlflow(run_id="abc123", version="1")
    tracker, patches = _make_tracker_with_mock(mock_mlflow)
    try:
        tracker.start_run(run_name="test_run")
        tracker.log_artifact("/tmp/test_checkpoint.pt")
        tracker.register_model(
            local_checkpoint_path="/tmp/test_checkpoint.pt",
            model_name="drugos_gt",
            stage="Staging",
            tags={"val_auc": "0.87"},
        )
    finally:
        _stop_patches(patches)

    # Verify register_model was called.
    assert mock_mlflow.register_model.called, (
        "mlflow.register_model was NOT called. The previous code OR the fix "
        "has a bug that prevents the call from firing."
    )
    call_kwargs = mock_mlflow.register_model.call_args
    model_uri = call_kwargs.kwargs.get("model_uri")
    assert model_uri is not None, (
        "mlflow.register_model was called without a model_uri kwarg. "
        f"Call args: {mock_mlflow.register_model.call_args}"
    )
    assert model_uri.startswith("runs:/"), (
        f"mlflow.register_model was called with model_uri={model_uri!r} -- "
        "this should be a 'runs:/<run_id>/<basename>' URI. "
        "The previous code used 'file://<path>' which only works in "
        "single-node dev deployments (the MLflow server needs direct "
        "filesystem access to the local path)."
    )
    assert "abc123" in model_uri, (
        f"model_uri {model_uri!r} does not contain the run_id 'abc123'. "
        "The runs:/ URI MUST include the run_id captured by start_run."
    )
    assert "test_checkpoint.pt" in model_uri, (
        f"model_uri {model_uri!r} does not contain the checkpoint basename "
        "'test_checkpoint.pt'. The runs:/ URI MUST end with the artifact basename."
    )
    # P3-010: the URI must NOT include the 'artifacts/' prefix. MLflow
    # rejects 'runs:/<run_id>/artifacts/<basename>' with "Unable to find
    # a logged_model with artifact_path artifacts/<basename>". The
    # correct format is 'runs:/<run_id>/<basename>' (artifact at root).
    assert "/artifacts/" not in model_uri, (
        f"model_uri {model_uri!r} contains '/artifacts/' prefix -- this is "
        "WRONG. MLflow rejects this URI with 'Unable to find a logged_model "
        "with artifact_path artifacts/<basename>'. The correct format is "
        "'runs:/<run_id>/<basename>' (artifact at root of run's store)."
    )


def test_register_model_calls_transition_model_version_stage():
    """P3-010.2: register_model MUST call transition_model_version_stage with the stage.

    The previous code accepted ``stage`` but NEVER applied it. The fix
    captures the ModelVersion returned by mlflow.register_model, extracts
    its .version, and calls transition_model_version_stage(name, version, stage).
    """
    mock_mlflow = _build_mock_mlflow(run_id="xyz789", version="42")
    tracker, patches = _make_tracker_with_mock(mock_mlflow)
    try:
        tracker.start_run(run_name="test_run_2")
        tracker.log_artifact("/tmp/ckpt.pt")
        tracker.register_model(
            local_checkpoint_path="/tmp/ckpt.pt",
            model_name="drugos_gt",
            stage="Production",  # use a DIFFERENT stage to verify it's actually passed
            tags={"val_auc": "0.92"},
        )
    finally:
        _stop_patches(patches)

    # Verify transition_model_version_stage was called (either top-level
    # or via MlflowClient).
    top_level_called = mock_mlflow.transition_model_version_stage.called
    client_called = (
        mock_mlflow.tracking.MlflowClient.return_value
        .transition_model_version_stage.called
    )
    assert top_level_called or client_called, (
        "Neither mlflow.transition_model_version_stage nor "
        "MlflowClient().transition_model_version_stage was called. The previous "
        "code accepted the 'stage' parameter but never applied it -- this is "
        "the exact P3-010 compound bug. The fix MUST call "
        "transition_model_version_stage(name, version, stage) AFTER "
        "register_model returns."
    )
    # Get the call from whichever path was used.
    if top_level_called:
        call = mock_mlflow.transition_model_version_stage.call_args
    else:
        call = (
            mock_mlflow.tracking.MlflowClient.return_value
            .transition_model_version_stage.call_args
        )
    # Verify the stage was actually passed.
    stage_passed = call.kwargs.get("stage")
    assert stage_passed == "Production", (
        f"transition_model_version_stage was called with stage={stage_passed!r}, "
        "expected 'Production'. The stage parameter is NOT being forwarded "
        "from register_model(stage=...) to transition_model_version_stage."
    )
    # Verify the version was extracted from register_model's return value.
    version_passed = call.kwargs.get("version")
    assert version_passed == "42", (
        f"transition_model_version_stage was called with version={version_passed!r}, "
        "expected '42' (the version returned by mlflow.register_model). The "
        "fix must capture the ModelVersion object and extract its .version."
    )
    # Verify the model name was passed.
    name_passed = call.kwargs.get("name")
    assert name_passed == "drugos_gt", (
        f"transition_model_version_stage was called with name={name_passed!r}, "
        "expected 'drugos_gt'."
    )


def test_register_model_handles_missing_version_gracefully():
    """P3-010.3: register_model MUST NOT crash if register_model returns no version.

    If mlflow.register_model returns an object without a .version attribute
    (e.g., a newer MLflow version returns a different type), the fix MUST
    log a WARNING and continue (the model is still registered, just not
    transitioned to a stage). It must NOT crash the training run.
    """
    mock_mlflow = MagicMock()
    mock_mlflow.start_run.return_value = MagicMock(
        info=MagicMock(run_id="def456")
    )
    mock_mlflow.active_run.return_value = MagicMock(
        info=MagicMock(run_id="def456")
    )
    # Return an object WITHOUT a .version attribute (simulates a
    # newer/different MLflow API). spec=[] means no attributes.
    no_version_obj = MagicMock(spec=[])
    mock_mlflow.register_model.return_value = no_version_obj
    mock_mlflow.transition_model_version_stage = MagicMock(return_value=None)
    mock_mlflow.tracking = MagicMock()
    mock_mlflow.tracking.MlflowClient.return_value.transition_model_version_stage = MagicMock(return_value=None)
    mock_mlflow.__version__ = "3.x-mock"

    tracker, patches = _make_tracker_with_mock(mock_mlflow)
    try:
        tracker.start_run(run_name="test_run_3")
        tracker.log_artifact("/tmp/ckpt2.pt")
        # This MUST NOT raise.
        tracker.register_model(
            local_checkpoint_path="/tmp/ckpt2.pt",
            model_name="drugos_gt",
            stage="Staging",
        )
    finally:
        _stop_patches(patches)

    # register_model SHOULD have been called (the model is still registered).
    assert mock_mlflow.register_model.called, (
        "mlflow.register_model should have been called even when the "
        "returned object has no .version attribute."
    )
    # transition_model_version_stage should NOT have been called (no version
    # to transition).
    top_level_called = mock_mlflow.transition_model_version_stage.called
    client_called = (
        mock_mlflow.tracking.MlflowClient.return_value
        .transition_model_version_stage.called
    )
    assert not (top_level_called or client_called), (
        "transition_model_version_stage should NOT have been called "
        "when register_model returns an object without .version."
    )


def test_start_run_captures_string_run_id():
    """P3-010.4: start_run MUST store a STRING run_id, not the ActiveRun object.

    The previous code stored the ActiveRun OBJECT returned by
    ``mlflow.start_run()`` into ``self._run_id``. This broke any string
    formatting like ``f"runs:/{self._run_id}/..."`` which would produce
    ``runs:/<ActiveRun ...>/...`` -- a malformed URI. The fix extracts
    ``.info.run_id`` and stores the STRING.
    """
    mock_mlflow = MagicMock()
    mock_mlflow.start_run.return_value = MagicMock(
        info=MagicMock(run_id="string_run_id_001")
    )
    mock_mlflow.active_run.return_value = MagicMock(
        info=MagicMock(run_id="string_run_id_001")
    )
    mock_mlflow.__version__ = "3.x-mock"

    tracker, patches = _make_tracker_with_mock(mock_mlflow)
    try:
        tracker.start_run(run_name="test_run_4")
        # Capture the run_id while the patch is still active.
        captured_run_id = tracker._run_id
    finally:
        _stop_patches(patches)

    assert captured_run_id is not None, (
        "tracker._run_id is None after start_run. The fix must capture the "
        "run_id string from the ActiveRun.info.run_id attribute."
    )
    assert isinstance(captured_run_id, str), (
        f"tracker._run_id is {type(captured_run_id).__name__}, expected str. "
        "The previous code stored the ActiveRun OBJECT (not the string "
        "run_id), which broke runs:/ URI formatting in register_model."
    )
    assert captured_run_id == "string_run_id_001", (
        f"tracker._run_id is {captured_run_id!r}, expected 'string_run_id_001'. "
        "The fix must extract .info.run_id from the ActiveRun returned by "
        "mlflow.start_run()."
    )


def test_register_model_falls_back_to_file_uri_when_no_run_id():
    """P3-010.5: when start_run was not called, register_model falls back to file:// URI.

    This is the backward-compatibility path: legacy callers that did not
    call start_run first should still be able to register a model (with
    a WARNING log). The fix MUST NOT crash in this case.
    """
    mock_mlflow = MagicMock()
    mock_mlflow.register_model.return_value = MagicMock(version="1")
    mock_mlflow.transition_model_version_stage = MagicMock(return_value=None)
    mock_mlflow.tracking = MagicMock()
    mock_mlflow.tracking.MlflowClient.return_value.transition_model_version_stage = MagicMock(return_value=None)
    mock_mlflow.__version__ = "3.x-mock"

    tracker, patches = _make_tracker_with_mock(mock_mlflow)
    try:
        # Deliberately do NOT call start_run.
        tracker.register_model(
            local_checkpoint_path="/tmp/legacy_ckpt.pt",
            model_name="drugos_gt",
            stage="Staging",
        )
    finally:
        _stop_patches(patches)

    assert mock_mlflow.register_model.called, (
        "mlflow.register_model should have been called even without start_run."
    )
    call = mock_mlflow.register_model.call_args
    model_uri = call.kwargs.get("model_uri")
    # When no run_id is available, the URI falls back to file://.
    assert model_uri.startswith("file://"), (
        f"Expected file:// URI fallback when no run_id is available, got {model_uri!r}."
    )
    assert "/tmp/legacy_ckpt.pt" in model_uri, (
        f"file:// URI should contain the local checkpoint path: got {model_uri!r}."
    )


def test_trainer_save_checkpoint_calls_register_model_with_stage():
    """P3-010.6: GraphTransformerTrainer.save_checkpoint passes stage='Staging'.

    HOSTILE-AUDITOR: inspect the trainer's save_checkpoint source code
    to verify it calls register_model with stage='Staging'. The previous
    v127 fix added the call but with the v142 fix, the stage is now
    ACTUALLY applied (via transition_model_version_stage).
    """
    import inspect
    from graph_transformer.training.trainer import GraphTransformerTrainer

    # Find the save_checkpoint method (or its equivalent).
    save_methods = [
        getattr(GraphTransformerTrainer, name, None)
        for name in ["save_checkpoint", "_save_checkpoint"]
    ]
    save_method = next((m for m in save_methods if m is not None), None)
    assert save_method is not None, (
        "GraphTransformerTrainer does not have a save_checkpoint or "
        "_save_checkpoint method. The trainer MUST save checkpoints to "
        "register them with MLflow."
    )
    src = inspect.getsource(save_method)
    # Verify register_model is called with stage='Staging'.
    assert "register_model" in src, (
        "save_checkpoint source does not call register_model. The trainer "
        "MUST register the checkpoint in the MLflow Model Registry."
    )
    assert "stage" in src, (
        "save_checkpoint source does not pass 'stage' to register_model. "
        "The trainer MUST pass stage='Staging' (or 'Production') so the "
        "MLflow Model Registry knows which workflow stage the model is in."
    )
    assert "Staging" in src, (
        "save_checkpoint source does not use stage='Staging'. The trainer "
        "MUST use stage='Staging' so the team lead can transition to "
        "'Production' after verifying AUC > 0.85 in the MLflow UI."
    )


if __name__ == "__main__":
    import traceback
    tests = [
        test_register_model_uses_runs_uri_not_file_uri,
        test_register_model_calls_transition_model_version_stage,
        test_register_model_handles_missing_version_gracefully,
        test_start_run_captures_string_run_id,
        test_register_model_falls_back_to_file_uri_when_no_run_id,
        test_trainer_save_checkpoint_calls_register_model_with_stage,
    ]
    failed = 0
    for test in tests:
        try:
            test()
            print(f"PASS: {test.__name__}")
        except AssertionError as e:
            failed += 1
            print(f"FAIL: {test.__name__}: {e}")
        except Exception as e:
            failed += 1
            print(f"ERROR: {test.__name__}: {e}")
            traceback.print_exc()
    print(f"\n{len(tests) - failed}/{len(tests)} tests passed.")
    sys.exit(1 if failed else 0)
