from __future__ import annotations

import asyncio
import time
from dataclasses import dataclass

from rich.text import Text
from textual.app import App, ComposeResult
from textual.binding import Binding
from textual.widgets import DataTable, Footer, Header, Static
from textual import work

from solradm.api.models import Replica
from solradm.api.utils import send_request
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


@dataclass
class BusyShardState:
    name: str
    leader: Replica | None
    status: str = "checking"
    progress: str = ""
    message: str = ""


def _parse_busy_status(json_resp: dict) -> tuple[str, str]:
    status = str(json_resp.get("status", "")).lower()
    if status != "busy":
        return "not_running", ""

    msgs = json_resp.get("statusMessages", {})
    processed = None
    total = None
    for k, v in msgs.items():
        if "processed" in str(k).lower():
            digits = "".join(ch for ch in str(v) if ch.isdigit())
            if digits:
                processed = int(digits)
        if "total" in str(k).lower():
            digits = "".join(ch for ch in str(v) if ch.isdigit())
            if digits:
                total = int(digits)

    if processed is None:
        return "running", "starting..."
    if total:
        pct = min(100, int(processed / total * 100))
        return "running", f"{processed:,}/{total:,} ({pct}%)"
    return "running", f"{processed:,}"


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


class BusyDataimportApp(App):
    BINDINGS = [Binding("q", "quit", "Quit")]

    def __init__(self, leaders: dict[str, Replica | None], dataimport_path: str) -> None:
        super().__init__()
        self._leaders = leaders
        self._dataimport_path = dataimport_path
        self._states = [BusyShardState(name=name, leader=leader) for name, leader in sorted(leaders.items())]
        self._stop = asyncio.Event()
        self._col_keys: dict[str, object] = {}

    def compose(self) -> ComposeResult:
        yield Header()
        yield DataTable(cursor_type="row")
        yield Footer()

    def on_mount(self) -> None:
        table = self.query_one(DataTable)
        self._col_keys["Target Shard"] = table.add_column("Target Shard", width=20)
        self._col_keys["Status"] = table.add_column("Status", width=16)
        self._col_keys["Progress"] = table.add_column("Progress", width=28)
        self._col_keys["Message"] = table.add_column("Message", width=50)

        for shard in self._states:
            table.add_row(shard.name, "Checking", "", "", key=shard.name)

        self._start_polling()
        self.set_interval(0.25, self._refresh_table)

    @work(thread=False)
    async def _start_polling(self) -> None:
        while not self._stop.is_set():
            await asyncio.gather(*(self._poll_shard(shard) for shard in self._states))
            await asyncio.sleep(1.0)

    async def _poll_shard(self, shard: BusyShardState) -> None:
        if not shard.leader or not shard.leader.base_url:
            shard.status = "unavailable"
            shard.progress = ""
            shard.message = "No leader/base URL"
            return

        try:
            response = await send_request(
                shard.leader.base_url,
                self._dataimport_path,
                params={"command": "status", "wt": "json"},
            )
            shard.status, shard.progress = _parse_busy_status(response)
            shard.message = str(response.get("statusMessages", ""))[:200]
        except Exception as exc:
            shard.status = "error"
            shard.progress = ""
            shard.message = str(exc)

    def _refresh_table(self) -> None:
        table = self.query_one(DataTable)
        status_labels = {
            "checking": Text("Checking", style="dim"),
            "running": Text("Running", style="green"),
            "not_running": Text("Not Running", style="yellow"),
            "unavailable": Text("Unavailable", style="red"),
            "error": Text("Error", style="red"),
        }

        for shard in self._states:
            table.update_cell(shard.name, self._col_keys["Status"], status_labels.get(shard.status, shard.status))
            table.update_cell(shard.name, self._col_keys["Progress"], shard.progress)
            table.update_cell(shard.name, self._col_keys["Message"], shard.message)

    def action_quit(self) -> None:
        self._stop.set()
        self.exit()
