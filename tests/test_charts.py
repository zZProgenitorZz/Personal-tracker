"""The small pure helpers for the bar charts."""
import pytest

from app.charts import compact_number


@pytest.mark.parametrize("n, text", [(0, ""), (999, "999"), (1000, "1k"), (1049, "1k"), (1050, "1.1k"),
                                     (8123, "8.1k"), (12000, "12k"), (12345, "12.3k"), (200_000, "200k")])
def test_compact_number_is_short_enough_for_a_narrow_bar(n, text):
    assert compact_number(n) == text
