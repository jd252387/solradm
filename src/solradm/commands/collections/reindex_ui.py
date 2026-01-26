from __future__ import annotations

from textual.app import App, ComposeResult
from textual.widgets import DataTable, Footer, Header, Static
from textual.containers import Container

from solradm.commands.collections.reindex_types import TargetShardState


class SummaryBar(Static):
    """Displays aggregate progress statistics."""

    DEFAULT_CSS = """
    SummaryBar {
        height: 1;
        background: $surface;
        color: $text;
        padding: 0 1;
    }
    """

    def __init__(self, **kwargs):
        super().__init__(**kwargs)
        self._total = 0
        self._running = 0
        self._done = 0
        self._failed = 0
        self._docs_processed = 0
        self._total_docs = 0

    def _format_docs(self, count: int) -> str:
        """Format document count with K/M suffix."""
        if count >= 1_000_000:
            return f"{count / 1_000_000:.1f}M"
        elif count >= 1_000:
            return f"{count / 1_000:.1f}K"
        return str(count)

    def _format_time(self, seconds: float) -> str:
        """Format seconds as human-readable time."""
        seconds = int(seconds)
        if seconds < 60:
            return f"{seconds}s"
        elif seconds < 3600:
            mins = seconds // 60
            secs = seconds % 60
            return f"{mins}m {secs}s"
        else:
            hours = seconds // 3600
            mins = (seconds % 3600) // 60
            return f"{hours}h {mins}m"

    def update_stats(
        self,
        total: int,
        running: int,
        done: int,
        failed: int,
        docs_processed: int,
        total_docs: int,
    ) -> None:
        """Update summary statistics."""
        self._total = total
        self._running = running
        self._done = done
        self._failed = failed
        self._docs_processed = docs_processed
        self._total_docs = total_docs
        self._update_display()

    def _update_display(self) -> None:
        """Render the summary bar."""
        pct = (
            int(100 * self._docs_processed / self._total_docs)
            if self._total_docs > 0
            else 0
        )
        parts = [
            f"Running: {self._running}/{self._total}",
            f"Completed: {self._done}",
            f"Failed: {self._failed}",
            f"Progress: {pct}% ({self._format_docs(self._docs_processed)})",
        ]
        self.update(" | ".join(parts))
