from __future__ import annotations

import asyncio
import time

from textual.app import App, ComposeResult
from textual.widgets import DataTable, Footer, Header, Static

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


STATUS_PRIORITY = {"running": 0, "pending": 1, "failed": 2, "done": 3}


class ReindexApp(App):
    """Textual app for reindex progress display."""

    TITLE = "Solr Reindex"

    BINDINGS = [
        ("q", "quit", "Quit"),
        ("escape", "quit", "Quit"),
    ]

    DEFAULT_CSS = """
    Screen {
        background: $surface;
    }

    DataTable {
        height: 1fr;
    }

    DataTable > .datatable--cursor {
        background: $accent;
    }

    .status-running {
        color: $warning;
    }

    .status-done {
        color: $success;
    }

    .status-failed {
        color: $error;
    }

    .status-pending {
        color: $text-muted;
    }
    """

    def __init__(self, engine, **kwargs):
        super().__init__(**kwargs)
        self._engine = engine
        self._table: DataTable | None = None
        self._summary_bar: SummaryBar | None = None
        self._row_keys: dict[str, str] = {}  # target_name -> row_key
        self._start_time = time.time()

    def compose(self) -> ComposeResult:
        yield Header(show_clock=True)
        yield SummaryBar(id="summary")
        yield DataTable(id="shards")
        yield Footer()

    def on_mount(self) -> None:
        """Initialize table and start background tasks."""
        self._summary_bar = self.query_one("#summary", SummaryBar)
        self._table = self.query_one("#shards", DataTable)

        # Set up columns
        self._table.add_column("Target Shard", key="target")
        self._table.add_column("Status", key="status")
        self._table.add_column("Current Source", key="source")
        self._table.add_column("Progress", key="progress")
        self._table.add_column("Elapsed", key="elapsed")
        self._table.add_column("Error", key="error")

        # Add initial rows
        for target in self._engine.get_state():
            row_key = self._table.add_row(
                target.name,
                target.status,
                target.current_source or "-",
                self._format_progress(target),
                "-",
                target.error or "",
                key=target.name,
            )
            self._row_keys[target.name] = row_key

        # Start background tasks
        asyncio.create_task(self._run_engine())
        self.set_interval(0.2, self._refresh_display)

    @staticmethod
    def _sort_key(target: TargetShardState) -> tuple[int, str]:
        """Sort key: status priority, then name."""
        return (STATUS_PRIORITY.get(target.status, 99), target.name)

    def _format_progress(self, target: TargetShardState) -> str:
        """Format progress string for a target shard."""
        if target.status == "pending":
            return "-"

        total = target.total_docs
        processed = target.docs_processed

        if total == 0:
            return "-"

        pct = int(100 * processed / total) if total > 0 else 0
        return f"{pct}% ({self._format_docs(processed)}/{self._format_docs(total)})"

    def _format_docs(self, count: int) -> str:
        """Format document count with K/M suffix."""
        if count >= 1_000_000:
            return f"{count / 1_000_000:.1f}M"
        elif count >= 1_000:
            return f"{count / 1_000:.1f}K"
        return str(count)

    def _format_elapsed(self, target: TargetShardState) -> str:
        """Format elapsed time for a target shard."""
        if target.started_at is None:
            return "-"

        end_time = target.completed_at or time.time()
        elapsed = end_time - target.started_at

        seconds = int(elapsed)
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

    def _refresh_display(self) -> None:
        """Update table and summary bar with current state."""
        if not self._table or not self._summary_bar:
            return

        summary = self._engine.get_summary()
        self._summary_bar.update_stats(
            total=summary["total"],
            running=summary["running"],
            done=summary["done"],
            failed=summary["failed"],
            docs_processed=summary["docs_processed"],
            total_docs=summary["total_docs"],
        )

        states = sorted(self._engine.get_state(), key=self._sort_key)

        self._table.clear()
        for target in states:
            self._table.add_row(
                target.name,
                target.status,
                target.current_source or "-",
                self._format_progress(target),
                self._format_elapsed(target),
                target.error or "",
                key=target.name,
            )

    async def _run_engine(self) -> None:
        """Run the reindex engine in background."""
        try:
            await self._engine.run()
        except Exception as e:
            self.notify(f"Engine error: {e}", severity="error")
        finally:
            self._refresh_display()

    def action_quit(self) -> None:
        """Handle quit action."""
        self._engine.request_cancel()
        self.exit()
