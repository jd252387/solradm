import pytest
from textual.widgets import Static

from solradm.commands.collections.reindex_ui import SummaryBar


def test_summary_bar_format_docs():
    bar = SummaryBar()
    assert bar._format_docs(0) == "0"
    assert bar._format_docs(999) == "999"
    assert bar._format_docs(1000) == "1.0K"
    assert bar._format_docs(1500) == "1.5K"
    assert bar._format_docs(1000000) == "1.0M"
    assert bar._format_docs(1234567) == "1.2M"


def test_summary_bar_format_time():
    bar = SummaryBar()
    assert bar._format_time(0) == "0s"
    assert bar._format_time(45) == "45s"
    assert bar._format_time(60) == "1m 0s"
    assert bar._format_time(90) == "1m 30s"
    assert bar._format_time(3661) == "1h 1m"
