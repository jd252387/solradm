import asyncio
import hashlib
import os
import re
import threading
import time

import rich
from kazoo.client import KazooClient
from watchdog.events import FileSystemEventHandler

from solradm.api import get_initialized_sesssion
from solradm.api.state import get_collections
from solradm.api.utils import get_collections_using_config
from solradm.commands.collections import reload
from solradm.commands.zk.utils import create_or_update, get_relative_znode_path


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
        self.pending_changes = dict()
        self.modification_hashes = dict()
        self.scheduled_sync = None

    def on_created(self, event):
        if not event.is_directory:
            self._schedule_sync(event.src_path, "created")

    def on_modified(self, event):
        if not event.is_directory:
            contents = open(event.src_path, "rb").read()
            if not contents:
                return

            edit_hash = hashlib.md5(contents).hexdigest()

            if self.modification_hashes.get(event.src_path) != edit_hash:
                self._schedule_sync(event.src_path, "modified")
                self.modification_hashes[event.src_path] = edit_hash

    def on_deleted(self, event):
        if not event.is_directory:
            self._schedule_sync(event.src_path, "deleted")

    def _schedule_sync(self, file_path: str, change_type: str):
        """Schedule a sync operation."""
        rich.print(f"🔄 [yellow]{change_type}: [green] {file_path}")
        current_time = time.time()
        self.pending_changes[file_path] = change_type
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

        for file_path, change_type in self.pending_changes.items():
            try:
                zk_path = get_relative_znode_path(self.znode_path, self.temp_dir, file_path)
                self._sync_file_change(file_path, zk_path, change_type)

                if self.reload:
                    split_path = zk_path.split("/")
                    if split_path[0] == "configs":
                        to_reload.extend(get_collections_using_config(get_collections(), split_path[1]))
            except Exception as e:
                rich.print(f"[error]❌ Error syncing {file_path}: {e}")

        if to_reload:
            asyncio.run(reload(
                collection_name_filter=r"^(" + "|".join(re.escape(collection.name) for collection in to_reload) + r")$",
                dry_run=False))
            asyncio.run(get_initialized_sesssion().close())

        self.pending_changes.clear()
        self.modification_hashes.clear()
        self.last_sync = time.time()
        rich.print("[success]✅ Sync completed")

    def _sync_file_change(self, file_path: str, zk_path: str, change_type: str):
        """Sync a single file change to ZooKeeper."""
        # Calculate relative path from temp directory

        if change_type == "created" or change_type == "modified":
            # Create or update zNode
            if os.path.exists(file_path):
                with open(file_path, "r", encoding="utf-8") as f:
                    content = f.read()

                create_or_update(self.zk, zk_path, content.encode("utf-8"))
        elif change_type == "deleted":
            # Delete zNode if it exists
            if self.zk.exists(zk_path):
                self.zk.delete(zk_path, recursive=True)
                rich.print(f"[red]🗑️ Deleted: {zk_path}")
