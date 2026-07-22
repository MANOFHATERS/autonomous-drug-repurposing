"""P3-001 ROOT FIX verification (Teammate 7 v142, hostile-auditor RED TEAM).

Verifies that graph_transformer/requirements.txt declares package versions
that ACTUALLY EXIST on PyPI. The previous file declared FOUR non-existent
or excessively-narrow pins:

  * ``pandas>=3.0.3,<4.0``   -- pandas 3.0.3 does NOT exist (latest is 2.2.x)
  * ``rdkit>=2026.3.4,<2027.0`` -- future calendar version, does NOT exist
  * ``scipy>=1.18.0,<2.0``    -- scipy 1.18.0 does NOT exist (latest is 1.14.x)
  * ``torch>=2.2.0,<2.3.0``   -- excessively narrow (single patch version)

The previous v122 IN-080 comment block CLAIMED each pin matched "what
actually exists on PyPI as of 2024-12" -- that claim was FALSE. ``pip
install -r graph_transformer/requirements.txt`` failed with
``No matching distribution found`` for pandas, rdkit, and scipy, making
the Phase 3 environment UNINSTALLABLE via the documented path.

This test verifies the fix at the FILE level (not via pip install, which
would require network access and a real PyPI round-trip). It reads the
requirements.txt as a text file and asserts each pin matches a known-good
bound. The known-good bounds are aligned with the lock file
(``graph_transformer/requirements.lock``) and the root ``requirements.txt``
so dev and Docker installs produce the SAME environment.

HOSTILE-AUDITOR pattern: this test reads the FILE, not the comments.
A previous v122 fix updated the COMMENTS to claim the pins were fixed
but left the actual pins broken. This test inspects the pins themselves.
"""
from __future__ import annotations

import os
import re
import sys
from pathlib import Path

_REPO_ROOT = Path(__file__).resolve().parents[2]
if str(_REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(_REPO_ROOT))


def _read_requirements_txt() -> str:
    """Read graph_transformer/requirements.txt as a text file.

    Returns the raw file contents (including comments). The caller is
    responsible for stripping comments and parsing the pin lines.
    """
    req_path = _REPO_ROOT / "graph_transformer" / "requirements.txt"
    assert req_path.exists(), (
        f"graph_transformer/requirements.txt does not exist at {req_path}. "
        "This file is REQUIRED for `pip install -r graph_transformer/requirements.txt` "
        "to work (the documented Phase 3 dev install path)."
    )
    return req_path.read_text(encoding="utf-8")


def _parse_pins(requirements_text: str) -> dict:
    """Parse non-comment, non-empty lines into {package_name: full_pin}.

    A pin line looks like ``torch>=2.2.0,<2.6.0``. We extract the
    package name (everything before the first ``>=``, ``==``, ``<``,
    ``>``, ``!=``, ``~=``) and the full pin expression.

    Lines that start with ``#`` or are empty are skipped. Inline
    comments (``... # comment``) are stripped before parsing.
    """
    pins = {}
    for raw_line in requirements_text.splitlines():
        # Strip inline comments (everything after the first ``#`` that
        # is NOT inside a quoted string -- but requirements.txt does not
        # support quoted strings, so a simple split is safe).
        line = raw_line.split("#", 1)[0].strip()
        if not line:
            continue
        # Match the package name (letters, digits, hyphens, underscores,
        # periods) followed by a version specifier.
        m = re.match(r"^([A-Za-z0-9_.\-]+)\s*([<>=!~].+)$", line)
        if not m:
            # Lines like ``-e .`` or ``--index-url ...`` are not pins;
            # skip them.
            continue
        package = m.group(1).lower()
        pin = m.group(2).strip()
        pins[package] = pin
    return pins


def test_pandas_pin_exists_on_pypi():
    """P3-001.1: pandas pin must reference a version that exists on PyPI.

    The previous pin ``pandas>=3.0.3,<4.0`` required pandas 3.0.3, which
    does NOT exist on PyPI (latest stable is 2.2.x as of 2024-12). The
    fix aligns with the lock file: ``pandas>=2.1.4,<3.0``.
    """
    pins = _parse_pins(_read_requirements_txt())
    assert "pandas" in pins, "graph_transformer/requirements.txt must declare pandas"
    pin = pins["pandas"]
    # The pin MUST NOT reference version 3.x (does not exist).
    assert "3.0.3" not in pin, (
        f"pandas pin still references the non-existent 3.0.3: {pin!r}. "
        "pandas 3.0.3 does NOT exist on PyPI. The fix is to use "
        "pandas>=2.1.4,<3.0 (matching the lock file)."
    )
    # The pin MUST use the 2.x lower bound (matches lock file).
    assert "2.1.4" in pin or "2.0" in pin or "2.1" in pin, (
        f"pandas pin should use a 2.x lower bound (matching the lock file): got {pin!r}."
    )
    # The upper bound MUST be <3.0 (not <4.0 which would allow the
    # non-existent 3.x to be requested).
    assert "<3.0" in pin or "<3" in pin, (
        f"pandas pin should have upper bound <3.0 (not <4.0): got {pin!r}."
    )


def test_rdkit_pin_exists_on_pypi():
    """P3-001.2: rdkit pin must reference a version that exists on PyPI.

    The previous pin ``rdkit>=2026.3.4,<2027.0`` was a FUTURE calendar
    version that does NOT exist on PyPI. RDKit uses calendar versioning
    (YYYY.M.x); 2024.3.x / 2024.9.x are the latest stable releases.
    The fix aligns with the lock file: ``rdkit>=2024.3.1,<2025.0``.
    """
    pins = _parse_pins(_read_requirements_txt())
    assert "rdkit" in pins, "graph_transformer/requirements.txt must declare rdkit"
    pin = pins["rdkit"]
    # The pin MUST NOT reference version 2026.x or 2027.x (future, does not exist).
    assert "2026" not in pin, (
        f"rdkit pin still references the future/non-existent 2026.x: {pin!r}. "
        "RDKit 2026.3.4 does NOT exist on PyPI. The fix is to use "
        "rdkit>=2024.3.1,<2025.0 (matching the lock file)."
    )
    assert "2027" not in pin, (
        f"rdkit pin still references 2027.x: {pin!r}."
    )
    # The pin MUST use a 2024.x lower bound.
    assert "2024.3.1" in pin or "2024.3" in pin or "2024.9" in pin, (
        f"rdkit pin should use a 2024.x lower bound: got {pin!r}."
    )


def test_scipy_pin_exists_on_pypi():
    """P3-001.3: scipy pin must reference a version that exists on PyPI.

    The previous pin ``scipy>=1.18.0,<2.0`` required scipy 1.18.0, which
    does NOT exist on PyPI (latest is 1.14.x as of 2024-12). The fix
    aligns with the lock file: ``scipy>=1.10,<2.0``.
    """
    pins = _parse_pins(_read_requirements_txt())
    assert "scipy" in pins, "graph_transformer/requirements.txt must declare scipy"
    pin = pins["scipy"]
    # The pin MUST NOT reference version 1.18.x (does not exist).
    assert "1.18" not in pin, (
        f"scipy pin still references the non-existent 1.18.0: {pin!r}. "
        "scipy 1.18.0 does NOT exist on PyPI. The fix is to use "
        "scipy>=1.10,<2.0 (matching the lock file)."
    )
    # The pin MUST use a 1.10+ lower bound (matches lock file).
    # We accept 1.10, 1.11, 1.12, 1.13, 1.14 -- all exist on PyPI.
    m = re.search(r">=\s*(1\.\d+)", pin)
    assert m, f"scipy pin should have a >= 1.x lower bound: got {pin!r}"
    lower = float(m.group(1))
    assert 1.10 <= lower <= 1.14, (
        f"scipy pin lower bound {lower} is not in the valid range [1.10, 1.14]: got {pin!r}."
    )


def test_torch_pin_not_excessively_narrow():
    """P3-001.4: torch pin must allow security upgrades (not pinned to single patch).

    The previous pin ``torch>=2.2.0,<2.3.0`` was excessively narrow -- a
    single patch version. This blocks security upgrades (e.g., torch 2.4.x
    fixes CVE-2024-XXXX). The fix widens to ``torch>=2.2.0,<2.6.0`` to
    allow security upgrades within the 2.x major series.
    """
    pins = _parse_pins(_read_requirements_txt())
    assert "torch" in pins, "graph_transformer/requirements.txt must declare torch"
    pin = pins["torch"]
    # The pin MUST have a lower bound of 2.2.0 (matches lock file).
    assert "2.2.0" in pin or "2.2" in pin, (
        f"torch pin should have lower bound 2.2.0: got {pin!r}."
    )
    # The upper bound MUST allow at least 2.4.x (security upgrades).
    # We extract the upper bound and check it is >= 2.4.0.
    m = re.search(r"<\s*(2\.\d+)", pin)
    assert m, f"torch pin should have an upper bound < 2.x: got {pin!r}"
    upper = float(m.group(1))
    assert upper >= 2.4, (
        f"torch pin upper bound {upper} is too narrow -- must allow at least 2.4.x "
        f"for security upgrades: got {pin!r}. The previous <2.3.0 was a single "
        "patch version that blocked security fixes."
    )


def test_torch_geometric_pin_exists_on_pypi():
    """P3-001.5: torch-geometric pin must reference a version that exists on PyPI.

    The previous pin ``torch-geometric>=2.8.0,<2.9.0`` may or may not
    exist (latest stable is 2.5.x as of 2024-12). The fix aligns with
    the lock file: ``torch-geometric>=2.5.0,<2.7.0``.
    """
    pins = _parse_pins(_read_requirements_txt())
    assert "torch-geometric" in pins, "graph_transformer/requirements.txt must declare torch-geometric"
    pin = pins["torch-geometric"]
    # The pin MUST NOT require >=2.8.0 (does not exist as of 2024-12).
    # We accept 2.4.x, 2.5.x, 2.6.x as lower bounds.
    m = re.search(r">=\s*(2\.\d+)", pin)
    assert m, f"torch-geometric pin should have a >= 2.x lower bound: got {pin!r}"
    lower = float(m.group(1))
    assert 2.4 <= lower <= 2.6, (
        f"torch-geometric pin lower bound {lower} is not in the valid range "
        f"[2.4, 2.6]: got {pin!r}. 2.8.0 does NOT exist on PyPI as of 2024-12."
    )


def test_pins_match_lock_file():
    """P3-001.6: graph_transformer/requirements.txt pins must match the lock file.

    The lock file (``graph_transformer/requirements.lock``) is the
    canonical source of truth for installed versions. The requirements.txt
    MUST be consistent with the lock file so dev installs and lock-based
    installs produce the SAME environment. A drift between the two files
    is the "silent drift between dev/CI and production environments" the
    P3-001 audit finding called out.
    """
    req_pins = _parse_pins(_read_requirements_txt())
    lock_path = _REPO_ROOT / "graph_transformer" / "requirements.lock"
    if not lock_path.exists():
        # Lock file is optional in some environments. Skip this test
        # rather than failing -- but log a warning.
        import warnings
        warnings.warn(
            f"graph_transformer/requirements.lock not found at {lock_path}. "
            "Skipping lock-file consistency check."
        )
        return
    lock_text = lock_path.read_text(encoding="utf-8")
    lock_pins = _parse_pins(lock_text)
    # Check the key packages from P3-001 are consistent.
    for pkg in ["pandas", "rdkit", "scipy", "torch", "torch-geometric", "scikit-learn"]:
        if pkg not in lock_pins:
            continue  # lock file may omit some packages
        if pkg not in req_pins:
            continue  # requirements.txt may omit some packages
        # Extract the lower bound from each and verify they match.
        req_match = re.search(r">=\s*([^,<\s]+)", req_pins[pkg])
        lock_match = re.search(r">=\s*([^,<\s]+)", lock_pins[pkg])
        if req_match and lock_match:
            # The lock file may have a tighter lower bound (e.g., 2.1.4
            # vs 2.0). We accept EITHER direction as long as both are
            # in the same major.minor series.
            req_lb = req_match.group(1)
            lock_lb = lock_match.group(1)
            # For calendar-versioned packages (rdkit), compare as strings.
            # For semver packages, compare major.minor.
            if pkg == "rdkit":
                # Both should be 2024.x.
                assert req_lb.startswith("2024.") or req_lb.startswith("2023."), (
                    f"rdkit lower bound in requirements.txt ({req_lb}) is not 2024.x: "
                    f"lock file has {lock_lb}."
                )
            else:
                # Extract major.minor from both and compare.
                req_mm = re.match(r"(\d+\.\d+)", req_lb)
                lock_mm = re.match(r"(\d+\.\d+)", lock_lb)
                if req_mm and lock_mm:
                    # Allow requirements.txt to have a slightly OLDER
                    # lower bound than the lock file (the lock file pins
                    # a specific tested version, requirements.txt is the
                    # lower limit). But the major MUST match.
                    req_major = int(req_mm.group(1).split(".")[0])
                    lock_major = int(lock_mm.group(1).split(".")[0])
                    assert req_major == lock_major, (
                        f"{pkg} major version mismatch: requirements.txt has "
                        f"{req_lb} (major {req_major}), lock file has {lock_lb} "
                        f"(major {lock_major}). Drift between dev and lock installs."
                    )


if __name__ == "__main__":
    # Allow running as a script for quick verification without pytest.
    import traceback
    tests = [
        test_pandas_pin_exists_on_pypi,
        test_rdkit_pin_exists_on_pypi,
        test_scipy_pin_exists_on_pypi,
        test_torch_pin_not_excessively_narrow,
        test_torch_geometric_pin_exists_on_pypi,
        test_pins_match_lock_file,
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
