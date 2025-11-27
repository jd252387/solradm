import asyncio
import hashlib
import os
import re
import threading
import time
from dataclasses import dataclass
from typing import List, Optional

import rich
from kazoo.client import KazooClient
from watchdog.events import FileSystemEventHandler

from solradm.api import get_initialized_session
from solradm.api.state import get_collections
from solradm.api.utils import get_collections_using_config
from solradm.commands.collections.maintenance import reload
from solradm.commands.zk.utils import create_or_update, get_relative_znode_path


@dataclass
class PendingChange:
    src_path: str
    change_type: str
    dest_path: Optional[str] = None
    is_directory: bool = False


class ZooKeeperSyncHandler(FileSystemEventHandler):
    """Watchdog handler for syncing local changes back to ZooKeeper."""

    def __init__(
            self,
            zk: KazooClient,
            temp_dir: str,
            znode_path: str,
            sync_interval: int = 5,
            reload: bool = False,
    ):
        self.zk = zk
        self.temp_dir = temp_dir
        self.znode_path = znode_path
        self.sync_interval = sync_interval
        self.reload = reload
        self.last_sync = 0
        self.pending_changes: List[PendingChange] = []
        self.modification_hashes = dict()
        self.scheduled_sync = None

    def on_created(self, event):
        self._schedule_sync(
            event.src_path,
            "created",
            dest_path=None,
            is_directory=event.is_directory,
        )

    def on_modified(self, event):
        if not event.is_directory:
            try:
                contents = open(event.src_path, "rb").read()
                if not contents:
                    return
            except Exception:
                return

            edit_hash = hashlib.md5(contents).hexdigest()

            if self.modification_hashes.get(event.src_path) != edit_hash:
                self._schedule_sync(
                    event.src_path,
                    "modified",
                    dest_path=None,
                    is_directory=event.is_directory,
                )
                self.modification_hashes[event.src_path] = edit_hash

    def on_deleted(self, event):
        self._schedule_sync(
            event.src_path,
            "deleted",
            dest_path=None,
            is_directory=event.is_directory,
        )

    def on_moved(self, event):
        if getattr(event, "dest_path", None) and str(event.dest_path).startswith(self.temp_dir):
            change_type = "moved_dir" if event.is_directory else "moved"
            self._schedule_sync(
                event.src_path,
                change_type,
                dest_path=event.dest_path,
                is_directory=event.is_directory,
            )
        else:
            # Treat moves outside of the sync directory as deletions so recursive cleanup still happens.
            self._schedule_sync(
                event.src_path,
                "deleted",
                dest_path=None,
                is_directory=event.is_directory,
            )

    def _schedule_sync(self, file_path: str, change_type: str, *, dest_path: Optional[str], is_directory: bool):
        """Schedule a sync operation."""
        rich.print(f"🔄 [yellow]{change_type}: [green] {file_path}")
        current_time = time.time()
        self.pending_changes.append(
            PendingChange(
                src_path=file_path,
                change_type=change_type,
                dest_path=dest_path,
                is_directory=is_directory,
            )
        )
        last_sync_delta = current_time - self.last_sync

        if last_sync_delta >= self.sync_interval:
            self._sync_changes()
        else:
            if not self.scheduled_sync or not self.scheduled_sync.is_alive():
                self.scheduled_sync = threading.Timer(
                    self.sync_interval - last_sync_delta, self._sync_changes
                )
                rich.print(
                    f"[blue]🔄 Scheduling sync in {self.sync_interval - last_sync_delta} seconds"
                )
                self.scheduled_sync.start()

    def _sync_changes(self):
        """Sync pending changes to ZooKeeper."""
        if not self.pending_changes:
            return

        rich.print(
            f"[blue]🔄 Syncing {len(self.pending_changes)} changes to ZooKeeper..."
        )

        to_reload = []

        for change in list(self.pending_changes):
            try:
                affected_paths = self._sync_file_change(change)

                if self.reload:
                    for zk_path in affected_paths:
                        split_path = [part for part in zk_path.split("/") if part]
                        if len(split_path) >= 2 and split_path[0] == "configs":
                            to_reload.extend(
                                get_collections_using_config(get_collections(), split_path[1])
                            )
            except Exception as e:
                rich.print(f"[error]❌ Error syncing {change.src_path}: {e}")

        if len(to_reload) > 0:
            asyncio.run(reload(
                collection_name_filter=r"^(" + "|".join(re.escape(collection.name) for collection in to_reload) + r")$", coordinators=None,
                dry_run=False,
                skip_checks=True))
            asyncio.run(get_initialized_session().close())

        self.pending_changes.clear()
        self.modification_hashes.clear()
        self.last_sync = time.time()
        rich.print("[success]✅ Sync completed")

    def _sync_file_change(self, change: PendingChange) -> List[str]:
        """Sync a single file change to ZooKeeper."""

        zk_path = get_relative_znode_path(self.znode_path, self.temp_dir, change.src_path)

        if change.change_type in {"created", "modified"}:
            return self._sync_create_or_update(change, zk_path)

        if change.change_type == "deleted":
            self._remove_znode(zk_path)
            return [zk_path]

        if change.change_type in {"moved", "moved_dir"}:
            return self._sync_move_change(change, zk_path)

        return []

    def _sync_create_or_update(self, change: PendingChange, zk_path: str) -> List[str]:
        if change.is_directory:
            self._sync_directory_contents(change.src_path)
            return [zk_path]

        if os.path.exists(change.src_path):
            with open(change.src_path, "r", encoding="utf-8") as f:
                content = f.read()

            create_or_update(self.zk, zk_path, content.encode("utf-8"))
            return [zk_path]

        return []

    def _sync_move_change(self, change: PendingChange, zk_src_path: str) -> List[str]:
        # Moves out of the sync directory are treated as deletions so recursive cleanup always occurs.
        if not change.dest_path or not str(change.dest_path).startswith(self.temp_dir):
            self._remove_znode(zk_src_path)
            return [zk_src_path]

        zk_dest_path = get_relative_znode_path(self.znode_path, self.temp_dir, change.dest_path)
        self._remove_znode(zk_src_path)

        if os.path.isdir(change.dest_path):
            self._sync_directory_contents(change.dest_path)
        elif os.path.exists(change.dest_path):
            with open(change.dest_path, "r", encoding="utf-8") as f:
                content = f.read()

            create_or_update(self.zk, zk_dest_path, content.encode("utf-8"))

        return [zk_dest_path]

    def _remove_znode(self, zk_path: str) -> None:
        if self.zk.exists(zk_path):
            self.zk.delete(zk_path, recursive=True)
            rich.print(f"[red]🗑️ Deleted: {zk_path}")

    def _sync_directory_contents(self, directory_path: str) -> None:
        for root, _, files in os.walk(directory_path):
            for file_name in files:
                local_path = os.path.join(root, file_name)
                zk_path = get_relative_znode_path(
                    self.znode_path, self.temp_dir, local_path
                )
                with open(local_path, "r", encoding="utf-8") as f:
                    content = f.read()
                create_or_update(self.zk, zk_path, content.encode("utf-8"))
