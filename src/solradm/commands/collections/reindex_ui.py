from __future__ import annotations

import time

from rich.text import Text
from textual.app import App, ComposeResult
from textual.binding import Binding
from textual.widgets import DataTable, Footer, Header, Static
from textual import work

from solradm.commands.collections.reindex_engine import ReindexEngine


class SummaryBar(Static):
    def update_summary(self, engine: ReindexEngine) -> None:
        summary = engine.get_summary()
        text = (
            f"Running: {summary.running_targets}/{summary.total_targets} | "
            f"Completed: {summary.completed_targets} | "
            f"Failed: {summary.failed_targets} | "
            f"Docs: {summary.total_docs_processed:,}"
        )
        self.update(text)


_STATUS_STYLES = {
    "pending": ("Pending", "dim"),
    "running": ("Running", "green"),
    "done": ("Done", "bold green"),
    "failed": ("Failed", "bold red"),
}


def _format_status(status: str) -> Text:
    label, style = _STATUS_STYLES.get(status, (status, ""))
    return Text(label, style=style)


def _format_elapsed(started_at: float | None, completed_at: float | None) -> str:
    if started_at is None:
        return ""
    end = completed_at or time.monotonic()
    elapsed = end - started_at
    minutes, seconds = divmod(int(elapsed), 60)
    if minutes:
        return f"{minutes}m{seconds:02d}s"
    return f"{seconds}s"


def _format_progress(source_shards) -> str:
    for s in source_shards:
        if s.status == "running":
            if s.docs_total and s.docs_total > 0:
                pct = min(100, int(s.docs_processed / s.docs_total * 100))
                return f"{s.docs_processed:,}/{s.docs_total:,} ({pct}%)"
            elif s.docs_processed > 0:
                return f"{s.docs_processed:,}"
            return "starting..."
    return ""


class ReindexApp(App):
    BINDINGS = [Binding("q", "quit_app", "Quit")]

    CSS = """
    SummaryBar {
        dock: top;
        height: 1;
        background: $surface;
        padding: 0 1;
    }
    """

    def __init__(self, engine: ReindexEngine) -> None:
        super().__init__()
        self._engine = engine
        self._col_keys: dict[str, object] = {}
        self._auto_exit_at: float | None = None

    def compose(self) -> ComposeResult:
        yield Header()
        yield SummaryBar()
        yield DataTable(cursor_type="row")
        yield Footer()

    def on_mount(self) -> None:
        table = self.query_one(DataTable)
        columns = [
            ("Target Shard", 20),
            ("Status", 10),
            ("Source", 20),
            ("Progress", 28),
            ("Sources Done", 14),
            ("Elapsed", 10),
            ("Error", 40),
        ]
        for label, width in columns:
            key = table.add_column(label, width=width)
            self._col_keys[label] = key

        for target in self._engine.get_state():
            table.add_row(
                target.name,
                _format_status("pending"),
                "",
                "",
                f"0/{target.sources_total}",
                "",
                "",
                key=target.name,
            )

        self._start_engine()
        self.set_interval(0.2, self._refresh_table)

    @work(thread=False)
    async def _start_engine(self) -> None:
        await self._engine.run()

    def _refresh_table(self) -> None:
        table = self.query_one(DataTable)
        summary_bar = self.query_one(SummaryBar)
        summary_bar.update_summary(self._engine)

        for target in self._engine.get_state():
            table.update_cell(
                target.name,
                self._col_keys["Status"],
                _format_status(target.status),
            )
            table.update_cell(
                target.name,
                self._col_keys["Source"],
                target.current_source or "",
            )
            table.update_cell(
                target.name,
                self._col_keys["Progress"],
                _format_progress(target.source_shards),
            )
            done_failed = f"{target.sources_done}/{target.sources_total}"
            if target.sources_failed:
                done_failed += f" ({target.sources_failed} failed)"
            table.update_cell(
                target.name,
                self._col_keys["Sources Done"],
                done_failed,
            )
            table.update_cell(
                target.name,
                self._col_keys["Elapsed"],
                _format_elapsed(target.started_at, target.completed_at),
            )
            if target.error:
                table.update_cell(
                    target.name,
                    self._col_keys["Error"],
                    Text(target.error, style="red"),
                )

        if self._engine.is_done:
            if self._auto_exit_at is None:
                self._auto_exit_at = time.monotonic() + 1.0
            elif time.monotonic() >= self._auto_exit_at:
                self.exit()

    def action_quit_app(self) -> None:
        self._engine.request_cancel()
        self.exit()
