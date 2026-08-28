"""Unit coverage for content-free SSE load-test statistics."""

from scripts.load_test_sse import percentile


def test_percentile_interpolates_sorted_samples() -> None:
    """Reported percentiles should be deterministic for small samples."""
    assert percentile([], 0.95) == 0.0
    assert percentile([4.0], 0.95) == 4.0
    assert percentile([1.0, 2.0, 3.0, 4.0], 0.5) == 2.5
