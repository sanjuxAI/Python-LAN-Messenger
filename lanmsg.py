#!/usr/bin/env python3
"""
LanMsg — BeeBeep-inspired LAN messenger
Pure Python stdlib, commercially safe (PSF License).

Usage:
    python lanmsg.py [--username NAME] [--secret PASSPHRASE] [--downloads DIR]
"""

import argparse
import os
import socket
import sys
import threading
import time
import logging

# ── Local imports ────────────────────────────────────────────
from discovery import Discovery, DISCOVERY_PORT
from network import ChatServer, CHAT_PORT
from filetransfer import FileSender, FileReceiver
from history import History
from tui import TUI, ChatMessage


logging.basicConfig(
    filename=os.path.expanduser("~/.lanmsg.log"),
    level=logging.DEBUG,
    format="%(asctime)s %(levelname)s %(message)s",
)


def _get_local_ip() -> str:
    try:
        s = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
        s.connect(("8.8.8.8", 80))
        ip = s.getsockname()[0]
        s.close()
        return ip
    except Exception:
        return "127.0.0.1"


class App:
    def __init__(self, username: str, shared_secret: str, downloads_dir: str):
        self.username = username
        self.local_ip = _get_local_ip()
        self.shared_secret = shared_secret
        self.downloads_dir = downloads_dir
        os.makedirs(downloads_dir, exist_ok=True)

        data_dir = os.path.expanduser("~/.lanmsg_history")
        self.history = History(data_dir)

        # Peer registry: ip -> {username, port, online}
        self._peer_info: dict[str, dict] = {}
        self._peer_lock = threading.Lock()

        # TUI
        self.tui = TUI(self)

        # Network
        self.server = ChatServer(
            username=username,
            shared_secret=shared_secret,
            on_message=self._on_message,
            on_peer_connected=self._on_peer_connected_tcp,
            on_peer_disconnected=self._on_peer_disconnected_tcp,
        )

        # Discovery
        self.discovery = Discovery(
            username=username,
            chat_port=CHAT_PORT,
            on_peer_found=self._on_peer_found,
            on_peer_lost=self._on_peer_lost,
        )

    # ── Lifecycle ─────────────────────────────────────────────

    def run(self):
        self.server.start()
        self.discovery.start()
        self.tui.set_status(
            f"Listening on {self.local_ip}:{CHAT_PORT}  |  "
            "Tab=switch peer  F2=send file  F5=history  Ctrl+C=quit"
        )
        self.tui.run()   # blocks until user quits

    def shutdown(self):
        self.discovery.stop()
        self.server.stop()

    # ── Discovery callbacks ───────────────────────────────────

    def _on_peer_found(self, ip: str, username: str, port: int):
        with self._peer_lock:
            self._peer_info[ip] = {"username": username, "port": port, "online": True}
        self.tui.add_peer(ip, username, port)
        self.tui.add_message(ip, ChatMessage(
            sender="System", text=f"{username} ({ip}) joined the network.",
            is_me=False, is_system=True,
        ))
        # Try to establish encrypted TCP channel proactively
        threading.Thread(
            target=self.server.connect_to, args=(ip, port), daemon=True
        ).start()

    def _on_peer_lost(self, ip: str, username: str):
        with self._peer_lock:
            if ip in self._peer_info:
                self._peer_info[ip]["online"] = False
        self.tui.remove_peer(ip)
        self.tui.add_message(ip, ChatMessage(
            sender="System", text=f"{username} ({ip}) left the network.",
            is_me=False, is_system=True,
        ))

    # ── TCP callbacks ─────────────────────────────────────────

    def _on_peer_connected_tcp(self, ip: str, username: str):
        if username:
            with self._peer_lock:
                if ip in self._peer_info:
                    self._peer_info[ip]["username"] = username
                else:
                    self._peer_info[ip] = {"username": username,
                                           "port": CHAT_PORT, "online": True}
            self.tui.add_peer(ip, username, CHAT_PORT)

    def _on_peer_disconnected_tcp(self, ip: str):
        with self._peer_lock:
            peer = self._peer_info.get(ip)
        username = peer["username"] if peer else ip
        self.tui.add_message(ip, ChatMessage(
            sender="System", text=f"Connection to {username} closed.",
            is_me=False, is_system=True,
        ))

    def _on_message(self, peer_ip: str, msg: dict):
        mtype = msg.get("type", "chat")

        if mtype == "chat":
            text = msg.get("text", "")
            sender = msg.get("username", peer_ip)
            ts = msg.get("ts", time.time())

            self.tui.add_message(peer_ip, ChatMessage(
                sender=sender, text=text, is_me=False, ts=ts,
            ))
            self.history.append(peer_ip, "recv", sender, text, ts)

        elif mtype == "file_offer":
            filename = msg.get("filename", "unknown")
            filesize = msg.get("filesize", 0)
            port = msg.get("port", 0)
            sender = msg.get("username", peer_ip)

            # Show in chat that a file is incoming
            self.tui.add_message(peer_ip, ChatMessage(
                sender="System",
                text=f"📎 {sender} wants to send: {filename} ({filesize:,} bytes)  — Press Y/N to accept",
                is_me=False, is_system=True, is_file=True,
            ))
            # Queue the accept/decline prompt in the TUI
            self.tui.ask_accept_file(
                peer_ip=peer_ip,
                peer_username=sender,
                filename=filename,
                filesize=filesize,
                port=port,
            )

        elif mtype == "file_declined":
            filename = msg.get("filename", "unknown")
            sender = msg.get("username", peer_ip)
            self.tui.add_message(peer_ip, ChatMessage(
                sender="System",
                text=f"✗ {sender} declined your file: {filename}",
                is_me=False, is_system=True,
            ))

        elif mtype == "typing":
            pass  # Could show typing indicator

    # ── Sending ───────────────────────────────────────────────

    def send_message(self, peer_ip: str, peer_port: int, text: str):
        # Ensure connection
        connected = self.server.send_to(peer_ip, {
            "type": "chat",
            "username": self.username,
            "text": text,
            "ts": time.time(),
        })
        if not connected:
            # Try connecting first
            def _connect_then_send():
                ok = self.server.connect_to(peer_ip, peer_port)
                if ok:
                    self.server.send_to(peer_ip, {
                        "type": "chat",
                        "username": self.username,
                        "text": text,
                        "ts": time.time(),
                    })
                    self.tui.add_message(peer_ip, ChatMessage(
                        sender=self.username, text=text, is_me=True,
                    ))
                    self.history.append(peer_ip, "sent", self.username, text)
                else:
                    self.tui.add_message(peer_ip, ChatMessage(
                        sender="System",
                        text=f"Could not connect to peer. Message not sent.",
                        is_me=False, is_system=True,
                    ))
            threading.Thread(target=_connect_then_send, daemon=True).start()
            return

        self.tui.add_message(peer_ip, ChatMessage(
            sender=self.username, text=text, is_me=True,
        ))
        self.history.append(peer_ip, "sent", self.username, text)

    def send_file(self, peer_ip: str, peer_port: int, filepath: str):
        if not os.path.isfile(filepath):
            self.tui.set_status(f"File not found: {filepath}")
            return

        filename = os.path.basename(filepath)
        filesize = os.path.getsize(filepath)
        with self._peer_lock:
            peer_username = self._peer_info.get(peer_ip, {}).get("username", peer_ip)

        def _progress(sent, total):
            pct = int(sent / total * 100) if total else 0
            self.tui.set_status(f"Sending {filename}: {pct}%")

        def _done(success, msg):
            if success:
                self.tui.set_status(f"✓ Sent {filename}")
                self.tui.add_message(peer_ip, ChatMessage(
                    sender="System",
                    text=f"Sent file: {filename} ({filesize:,} bytes)",
                    is_me=False, is_system=True, is_file=True,
                ))
                self.history.append_file(
                    peer_ip, peer_username, "sent", filename, filesize, "ok"
                )
            else:
                self.tui.set_status(f"✗ Send failed: {msg}")
                self.history.append_file(
                    peer_ip, peer_username, "sent", filename, filesize, "failed"
                )

        sender = FileSender(filepath, on_progress=_progress, on_done=_done)
        sender.start()

        # Notify peer
        notified = self.server.send_to(peer_ip, {
            "type": "file_offer",
            "username": self.username,
            "filename": filename,
            "filesize": filesize,
            "port": sender.port,
        })
        if not notified:
            def _connect_then_notify():
                ok = self.server.connect_to(peer_ip, peer_port)
                if ok:
                    self.server.send_to(peer_ip, {
                        "type": "file_offer",
                        "username": self.username,
                        "filename": filename,
                        "filesize": filesize,
                        "port": sender.port,
                    })
                else:
                    self.tui.set_status("Could not connect to peer for file transfer.")
            threading.Thread(target=_connect_then_notify, daemon=True).start()

        self.tui.add_message(peer_ip, ChatMessage(
            sender="System",
            text=f"Offering file: {filename} ({filesize:,} bytes)...",
            is_me=False, is_system=True, is_file=True,
        ))

    def respond_to_file_offer(self, offer: dict, accepted: bool):
        """Called by TUI when user presses Y or N on a file offer."""
        peer_ip       = offer["peer_ip"]
        peer_username = offer["peer_username"]
        filename      = offer["filename"]
        filesize      = offer["filesize"]
        port          = offer["port"]

        if not accepted:
            # Tell the sender we declined
            self.server.send_to(peer_ip, {
                "type": "file_declined",
                "username": self.username,
                "filename": filename,
            })
            self.tui.add_message(peer_ip, ChatMessage(
                sender="System",
                text=f"✗ You declined: {filename}",
                is_me=False, is_system=True,
            ))
            self.history.append_file(
                peer_ip, peer_username, "declined", filename, filesize, "declined"
            )
            self.tui.set_status(f"Declined {filename}")
            return

        # ── Accepted ──────────────────────────────────────────
        self.tui.add_message(peer_ip, ChatMessage(
            sender="System",
            text=f"⬇ Receiving: {filename} ({filesize:,} bytes)...",
            is_me=False, is_system=True, is_file=True,
        ))

        def _progress(recv, total):
            pct = int(recv / total * 100) if total else 0
            self.tui.set_status(f"Receiving {filename}: {pct}%")

        def _done(success, result):
            if success:
                self.tui.set_status(f"✓ Saved to {result}")
                self.tui.add_message(peer_ip, ChatMessage(
                    sender="System",
                    text=f"✓ File saved: {os.path.basename(result)}  →  {result}",
                    is_me=False, is_system=True, is_file=True,
                ))
                self.history.append_file(
                    peer_ip, peer_username, "received",
                    filename, filesize, "ok", saved_path=result,
                )
            else:
                self.tui.set_status(f"✗ Receive failed: {result}")
                self.tui.add_message(peer_ip, ChatMessage(
                    sender="System",
                    text=f"✗ File receive failed: {result}",
                    is_me=False, is_system=True,
                ))
                self.history.append_file(
                    peer_ip, peer_username, "received",
                    filename, filesize, "failed",
                )

        receiver = FileReceiver(
            peer_ip, port, self.downloads_dir,
            on_progress=_progress, on_done=_done,
        )
        receiver.start()

    def load_history_for(self, peer_ip: str):
        entries = self.history.load(peer_ip)
        with self._peer_lock:
            peer = self._peer_info.get(peer_ip, {})
        username = peer.get("username", peer_ip)

        for entry in entries:
            is_me = entry["dir"] == "sent"
            self.tui.add_message(peer_ip, ChatMessage(
                sender=entry["user"],
                text=entry["text"],
                is_me=is_me,
                ts=entry["ts"],
            ))
        self.tui.set_status(f"Loaded {len(entries)} messages from history.")


# ── Entry point ───────────────────────────────────────────────

def main():
    parser = argparse.ArgumentParser(description="LanMsg — LAN messenger")
    parser.add_argument("--username", "-u", default=None,
                        help="Your display name (default: system username)")
    parser.add_argument("--secret", "-s", default="lanmsg_default_secret",
                        help="Shared passphrase for encryption (all peers must use same)")
    parser.add_argument("--downloads", "-d",
                        default=os.path.expanduser("~/Downloads/LanMsg"),
                        help="Directory to save received files")
    args = parser.parse_args()

    username = args.username or os.environ.get("USER") or os.environ.get("USERNAME") or "User"

    print(f"Starting LanMsg as '{username}'...")
    print(f"Downloads will be saved to: {args.downloads}")
    print(f"Shared secret: {'(default)' if args.secret == 'lanmsg_default_secret' else '(custom)'}")
    print()

    app = App(
        username=username,
        shared_secret=args.secret,
        downloads_dir=args.downloads,
    )
    try:
        app.run()
    except KeyboardInterrupt:
        pass
    finally:
        app.shutdown()
        print("\nGoodbye!")


if __name__ == "__main__":
    main()
