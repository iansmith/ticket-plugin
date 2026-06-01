"""Unit tests for rag_service.query_preprocessor (Layer 1 — pure function).

No TestClient, no fakes, no model weights.  Just call preprocess_query directly.
"""

from rag_service.query_preprocessor import preprocess_query


def test_residual_expanded():
    q = preprocess_query("finalBlockSize fix pixel shift residual multicol-breaking-001")
    assert "residual" in q
    assert "regression side-effect from a prior fix" in q


def test_residual_case_insensitive():
    q = preprocess_query("Residual regression from LOU-94")
    assert "regression side-effect from a prior fix" in q


def test_no_match_unchanged():
    original = "min-height nested multicol break-inside:avoid overflow"
    assert preprocess_query(original) == original


def test_no_substring_hit():
    # "residuals" (plural) should NOT be expanded — word boundary prevents it.
    q = preprocess_query("looking at residuals from the test suite")
    # The plural form doesn't match \bresidual\b
    assert "regression side-effect from a prior fix" not in q


def test_idempotent():
    # Expanding twice should not double-expand.
    once = preprocess_query("residual pixel shift")
    twice = preprocess_query(once)
    assert once == twice
