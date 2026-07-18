"""v124 Teammate 1 — P2-020 ROOT FIX verification.

This test verifies the REAL bug fixed in this commit: the YAML header at
``phase2/drugos_graph/data/verified_uniprot_gene_crosswalk.yaml`` CLAIMED
that ``id_crosswalk.py`` had a runtime UniProt API fallback enabled via
``DRUGOS_CROSSWALK_API_FALLBACK=true``, but the code did NOT implement
this feature. This was the exact "comments are fakes, code is broken"
pattern the audit flagged.

ROOT FIX (this commit): implemented the ``_fetch_gene_id_from_uniprot_api``
method in ``IDCrosswalk`` and wired it into ``uniprot_ac_to_ncbi_gene_id``
and ``uniprot_ac_to_ncbi_gene_id_all``. The fallback:
  - is DISABLED by default (network call per accession is slow)
  - is ENABLED when ``DRUGOS_CROSSWALK_API_FALLBACK=true``
  - caches results (positive AND negative) to avoid repeat network calls
  - is thread-safe (guarded by ``_api_fallback_lock``)
  - fails soft (returns None on any network error, logs WARNING)
  - rate-limits to ~4.76 req/s (under UniProt's 5 req/s fair-use policy)

This test does NOT hit the real UniProt API. It verifies:
  1. The env var is read correctly (disabled by default, enabled when set)
  2. The ``_fetch_gene_id_from_uniprot_api`` method exists
  3. The cache works (positive and negative)
  4. The fallback is wired into ``uniprot_ac_to_ncbi_gene_id``
  5. Thread-safety infrastructure exists
"""
from __future__ import annotations

import os
import sys
import threading
from pathlib import Path
from unittest.mock import patch

# Make the module importable.
_REPO_ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(_REPO_ROOT))
sys.path.insert(0, str(_REPO_ROOT / "phase2" / "drugos_graph"))


def test_api_fallback_disabled_by_default():
    """P2-020: fallback is DISABLED when env var is not set."""
    os.environ.pop("DRUGOS_CROSSWALK_API_FALLBACK", None)
    import importlib
    import id_crosswalk
    importlib.reload(id_crosswalk)
    cw = id_crosswalk.IDCrosswalk()
    assert cw._api_fallback_enabled is False, \
        "P2-020 FAIL: fallback should be disabled by default"


def test_api_fallback_enabled_when_env_set():
    """P2-020: fallback is ENABLED when DRUGOS_CROSSWALK_API_FALLBACK=true."""
    os.environ["DRUGOS_CROSSWALK_API_FALLBACK"] = "true"
    try:
        import importlib
        import id_crosswalk
        importlib.reload(id_crosswalk)
        cw = id_crosswalk.IDCrosswalk()
        assert cw._api_fallback_enabled is True, \
            "P2-020 FAIL: fallback should be enabled when env=true"
    finally:
        os.environ.pop("DRUGOS_CROSSWALK_API_FALLBACK", None)


def test_fetch_method_exists():
    """P2-020: _fetch_gene_id_from_uniprot_api method exists."""
    import id_crosswalk
    cw = id_crosswalk.IDCrosswalk()
    assert hasattr(cw, "_fetch_gene_id_from_uniprot_api"), \
        "P2-020 FAIL: _fetch_gene_id_from_uniprot_api method missing"
    assert callable(cw._fetch_gene_id_from_uniprot_api), \
        "P2-020 FAIL: _fetch_gene_id_from_uniprot_api not callable"


def test_cache_positive_hit():
    """P2-020: cache returns positive results without hitting the API."""
    os.environ["DRUGOS_CROSSWALK_API_FALLBACK"] = "true"
    try:
        import importlib
        import id_crosswalk
        importlib.reload(id_crosswalk)
        cw = id_crosswalk.IDCrosswalk()
        # Inject a fake positive result into the cache
        cw._api_fallback_cache["P23219"] = "5742"
        # Call the method — should return the cached value WITHOUT hitting the network
        result = cw._fetch_gene_id_from_uniprot_api("P23219")
        assert result == "5742", f"P2-020 FAIL: expected '5742', got {result!r}"
    finally:
        os.environ.pop("DRUGOS_CROSSWALK_API_FALLBACK", None)


def test_cache_negative_hit():
    """P2-020: negative cache returns None without hitting the API."""
    os.environ["DRUGOS_CROSSWALK_API_FALLBACK"] = "true"
    try:
        import importlib
        import id_crosswalk
        importlib.reload(id_crosswalk)
        cw = id_crosswalk.IDCrosswalk()
        cw._api_fallback_cache["UNKNOWN_ACCESSION"] = None
        result = cw._fetch_gene_id_from_uniprot_api("UNKNOWN_ACCESSION")
        assert result is None, f"P2-020 FAIL: expected None, got {result!r}"
    finally:
        os.environ.pop("DRUGOS_CROSSWALK_API_FALLBACK", None)


def test_disabled_fallback_returns_none():
    """P2-020: when disabled, _fetch_gene_id_from_uniprot_api returns None."""
    os.environ.pop("DRUGOS_CROSSWALK_API_FALLBACK", None)
    import importlib
    import id_crosswalk
    importlib.reload(id_crosswalk)
    cw = id_crosswalk.IDCrosswalk()
    result = cw._fetch_gene_id_from_uniprot_api("P23219")
    assert result is None, f"P2-020 FAIL: disabled fallback should return None"


def test_thread_lock_exists():
    """P2-020: thread lock exists for concurrent cache access."""
    import id_crosswalk
    cw = id_crosswalk.IDCrosswalk()
    assert hasattr(cw, "_api_fallback_lock"), "P2-020 FAIL: no lock"
    assert isinstance(cw._api_fallback_lock, type(threading.Lock())), \
        "P2-020 FAIL: wrong lock type"


def test_fallback_wired_into_uniprot_ac_to_ncbi_gene_id():
    """P2-020: uniprot_ac_to_ncbi_gene_id calls the API fallback when enabled."""
    os.environ["DRUGOS_CROSSWALK_API_FALLBACK"] = "true"
    try:
        import importlib
        import id_crosswalk
        importlib.reload(id_crosswalk)
        cw = id_crosswalk.IDCrosswalk()
        # Mock the API fetch to return a known value
        with patch.object(cw, "_fetch_gene_id_from_uniprot_api", return_value="5742"):
            # Lookup an accession NOT in the builtin table
            # (P99999 is not in the 38-entry seed)
            result = cw.uniprot_ac_to_ncbi_gene_id("P99999")
            assert result == "5742", \
                f"P2-020 FAIL: expected '5742' from API fallback, got {result!r}"
    finally:
        os.environ.pop("DRUGOS_CROSSWALK_API_FALLBACK", None)


def test_fallback_not_called_when_disabled():
    """P2-020: when disabled, uniprot_ac_to_ncbi_gene_id does NOT call the API."""
    os.environ.pop("DRUGOS_CROSSWALK_API_FALLBACK", None)
    import importlib
    import id_crosswalk
    importlib.reload(id_crosswalk)
    cw = id_crosswalk.IDCrosswalk()
    # Mock the API fetch — it should NOT be called
    with patch.object(cw, "_fetch_gene_id_from_uniprot_api") as mock_fetch:
        result = cw.uniprot_ac_to_ncbi_gene_id("P99999")
        assert result is None
        mock_fetch.assert_not_called(), \
            "P2-020 FAIL: API fallback was called when disabled"


def test_fallback_wired_into_uniprot_ac_to_ncbi_gene_id_all():
    """P2-020: uniprot_ac_to_ncbi_gene_id_all also uses the API fallback."""
    os.environ["DRUGOS_CROSSWALK_API_FALLBACK"] = "true"
    try:
        import importlib
        import id_crosswalk
        importlib.reload(id_crosswalk)
        cw = id_crosswalk.IDCrosswalk()
        with patch.object(cw, "_fetch_gene_id_from_uniprot_api", return_value="5742"):
            result = cw.uniprot_ac_to_ncbi_gene_id_all("P99999")
            assert result == ["5742"], \
                f"P2-020 FAIL: expected ['5742'], got {result!r}"
    finally:
        os.environ.pop("DRUGOS_CROSSWALK_API_FALLBACK", None)


def test_yaml_header_documents_the_feature():
    """P2-020: the YAML header documents the DRUGOS_CROSSWALK_API_FALLBACK feature.

    This is what made the bug so insidious — the documentation CLAIMED the
    feature existed, but the code didn't implement it. Now both the YAML
    docs AND the code agree.
    """
    yaml_path = _REPO_ROOT / "phase2" / "drugos_graph" / "data" / "verified_uniprot_gene_crosswalk.yaml"
    with open(yaml_path) as f:
        yaml_text = f.read()
    assert "DRUGOS_CROSSWALK_API_FALLBACK" in yaml_text, \
        "P2-020 FAIL: YAML doesn't document DRUGOS_CROSSWALK_API_FALLBACK"
    assert "UniProt ID mapping API" in yaml_text, \
        "P2-020 FAIL: YAML doesn't document the API fallback"


if __name__ == "__main__":
    # Run all tests
    tests = [
        test_api_fallback_disabled_by_default,
        test_api_fallback_enabled_when_env_set,
        test_fetch_method_exists,
        test_cache_positive_hit,
        test_cache_negative_hit,
        test_disabled_fallback_returns_none,
        test_thread_lock_exists,
        test_fallback_wired_into_uniprot_ac_to_ncbi_gene_id,
        test_fallback_not_called_when_disabled,
        test_fallback_wired_into_uniprot_ac_to_ncbi_gene_id_all,
        test_yaml_header_documents_the_feature,
    ]
    passed = 0
    failed = 0
    for test in tests:
        try:
            test()
            print(f"  PASS: {test.__name__}")
            passed += 1
        except Exception as e:
            print(f"  FAIL: {test.__name__}: {e}")
            failed += 1
    print(f"\n{passed}/{len(tests)} passed, {failed} failed")
    sys.exit(0 if failed == 0 else 1)
