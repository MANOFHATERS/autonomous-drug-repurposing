"""Phase 2 test suite conftest -- pre-imports torch_geometric to avoid
circular import issues during test collection.

v61 ROOT FIX (torch_geometric 2.8.0 circular import):
When pytest collects tests from multiple files that import
``torch_geometric`` submodules, the first import triggers
``torch_geometric/__init__.py`` to execute. If a test module imports a
torch_geometric submodule (e.g. ``torch_geometric.data``) while
``__init__.py`` is still executing, the partial ``torch_geometric``
module doesn't yet have all its attributes set, raising:

    AttributeError: partially initialized module 'torch_geometric' has
    no attribute 'typing' (most likely due to a circular import)

ROOT FIX: pre-import ``torch_geometric`` (the full package) at the TOP
of this conftest.py, which pytest loads BEFORE collecting any test
module. This ensures ``torch_geometric/__init__.py`` fully executes
once, setting all attributes on the ``torch_geometric`` module, so
subsequent test-module imports of submodules don't hit the partial-
initialization window.

Teammate 8 ROOT FIX (test isolation): the pre-import was UNCONDITIONAL,
which forced every test in phase2/tests/ (including the new
integration tests that don't use torch_geometric at all) to install
the full PyTorch + PyG stack. This made CI slow and blocked running
targeted integration tests on minimal environments (e.g., a CI job
that only wants to verify the /kg/stats canonicalNodeCount fix without
installing 2GB of PyTorch).

ROOT FIX: wrap the pre-import in a try/except. If torch_geometric is
available, pre-import it (preserving the v61 circular-import fix for
tests that DO use PyG). If it's NOT available, skip the pre-import —
tests that don't import torch_geometric submodules (like the new
Teammate 8 integration tests) will still collect + run fine. Tests
that DO import torch_geometric submodules will fail at their own
import time with a clear ModuleNotFoundError, telling the operator to
install the PyG stack.
"""
try:
    import torch_geometric  # noqa: F401 -- pre-import to avoid circular import
    import torch_geometric.typing  # noqa: F401
    import torch_geometric.data  # noqa: F401
    import torch_geometric.transforms  # noqa: F401
except ImportError:
    # torch_geometric not installed — skip the pre-import. Tests that
    # don't use PyG will still run; tests that do will fail at their
    # own import time with a clear error.
    pass
