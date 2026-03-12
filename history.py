"""
Simple JSON-lines message history, stored per peer.
Stdlib only.
"""

import json
import os
import threading
from datetime import datetime


class History:
    def __init__(self, data_dir: str):
        self.data_dir = data_dir
        os.makedirs(data_dir, exist_ok=True)
        self._lock = threading.Lock()

    def _path(self, peer_ip: str) -> str:
        safe = peer_ip.replace(".", "_")
        return os.path.join(self.data_dir, f"{safe}.jsonl")

    def append(self, peer_ip: str, direction: str, username: str,
                text: str, timestamp: float | None = None):
        """direction: 'sent' or 'recv'"""
        if timestamp is None:
            from time import time
            timestamp = time()
        entry = {
            "ts": timestamp,
            "dir": direction,
            "user": username,
            "text": text,
        }
        with self._lock:
            with open(self._path(peer_ip), "a", encoding="utf-8") as f:
                f.write(json.dumps(entry) + "\n")

    def load(self, peer_ip: str, limit: int = 200) -> list[dict]:
        path = self._path(peer_ip)
        if not os.path.exists(path):
            return []
        with self._lock:
            with open(path, "r", encoding="utf-8") as f:
                lines = f.readlines()
        entries = []
        for line in lines[-limit:]:
            try:
                entries.append(json.loads(line.strip()))
            except Exception:
                pass
        return entries

    def format_entry(self, entry: dict) -> str:
        ts = datetime.fromtimestamp(entry["ts"]).strftime("%H:%M:%S")
        direction = "→" if entry["dir"] == "sent" else "←"
        return f"[{ts}] {direction} {entry['user']}: {entry['text']}"

    # ── File transfer history ─────────────────────────────────

    def _file_log_path(self) -> str:
        return os.path.join(self.data_dir, "_file_transfers.jsonl")

    def append_file(self, peer_ip: str, peer_username: str,
                    direction: str, filename: str, filesize: int,
                    status: str, saved_path: str = ""):
        """
        Record a file transfer event.
        direction : 'sent' | 'received' | 'declined'
        status    : 'ok' | 'failed' | 'declined'
        """
        from time import time
        entry = {
            "ts": time(),
            "peer_ip": peer_ip,
            "peer_user": peer_username,
            "dir": direction,
            "filename": filename,
            "filesize": filesize,
            "status": status,
            "saved_path": saved_path,
        }
        with self._lock:
            with open(self._file_log_path(), "a", encoding="utf-8") as f:
                f.write(json.dumps(entry) + "\n")

    def load_file_transfers(self, limit: int = 100) -> list[dict]:
        path = self._file_log_path()
        if not os.path.exists(path):
            return []
        with self._lock:
            with open(path, "r", encoding="utf-8") as f:
                lines = f.readlines()
        entries = []
        for line in lines[-limit:]:
            try:
                entries.append(json.loads(line.strip()))
            except Exception:
                pass
        return entries

    def format_file_entry(self, entry: dict) -> str:
        ts = datetime.fromtimestamp(entry["ts"]).strftime("%Y-%m-%d %H:%M:%S")
        direction = "↑ Sent" if entry["dir"] == "sent" else (
                    "↓ Recv" if entry["dir"] == "received" else "✗ Declined")
        size_kb = entry["filesize"] / 1024
        status = entry["status"].upper()
        return (f"[{ts}] {direction}  {entry['filename']}  "
                f"({size_kb:.1f} KB)  [{status}]  "
                f"peer={entry['peer_user']}@{entry['peer_ip']}"
                + (f"  → {entry['saved_path']}" if entry.get("saved_path") else ""))
