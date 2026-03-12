"""
File transfer over a separate TCP connection.
Sender: opens a listener, tells the peer via chat message.
Receiver: connects and downloads.
Stdlib only.
"""

import socket
import threading
import os
import struct
import hashlib
import logging
import time

FILE_TRANSFER_PORT_RANGE = (55800, 55900)


def _find_free_port() -> int:
    for port in range(*FILE_TRANSFER_PORT_RANGE):
        try:
            s = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
            s.bind(("", port))
            s.close()
            return port
        except OSError:
            continue
    raise RuntimeError("No free port available for file transfer")


def _file_hash(path: str) -> str:
    h = hashlib.sha256()
    with open(path, "rb") as f:
        for chunk in iter(lambda: f.read(65536), b""):
            h.update(chunk)
    return h.hexdigest()


class FileSender:
    """Sends a single file to one peer."""

    def __init__(self, filepath: str, on_progress=None, on_done=None):
        self.filepath = filepath
        self.filename = os.path.basename(filepath)
        self.filesize = os.path.getsize(filepath)
        self.on_progress = on_progress  # (bytes_sent, total)
        self.on_done = on_done          # (success: bool, msg: str)
        self.port = _find_free_port()
        self._sock = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
        self._sock.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
        self._sock.bind(("", self.port))
        self._sock.listen(1)
        self._sock.settimeout(30)

    def start(self):
        threading.Thread(target=self._serve, daemon=True).start()

    def _serve(self):
        try:
            conn, _ = self._sock.accept()
            conn.settimeout(60)
            # Send header: filename_len (4B) + filename + filesize (8B) + hash (64B ascii)
            fname_bytes = self.filename.encode("utf-8")
            file_hash = _file_hash(self.filepath)
            header = (
                struct.pack(">I", len(fname_bytes)) +
                fname_bytes +
                struct.pack(">Q", self.filesize) +
                file_hash.encode("ascii")
            )
            conn.sendall(header)

            sent = 0
            with open(self.filepath, "rb") as f:
                while True:
                    chunk = f.read(65536)
                    if not chunk:
                        break
                    conn.sendall(chunk)
                    sent += len(chunk)
                    if self.on_progress:
                        self.on_progress(sent, self.filesize)

            conn.close()
            if self.on_done:
                self.on_done(True, f"Sent {self.filename} ({self.filesize} bytes)")
        except Exception as e:
            if self.on_done:
                self.on_done(False, str(e))
        finally:
            try:
                self._sock.close()
            except Exception:
                pass


class FileReceiver:
    """Receives a single file from a peer."""

    def __init__(self, peer_ip: str, port: int, save_dir: str,
                 on_progress=None, on_done=None):
        self.peer_ip = peer_ip
        self.port = port
        self.save_dir = save_dir
        self.on_progress = on_progress
        self.on_done = on_done

    def start(self):
        threading.Thread(target=self._receive, daemon=True).start()

    def _receive(self):
        try:
            s = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
            s.settimeout(10)
            s.connect((self.peer_ip, self.port))
            s.settimeout(60)

            # Read filename length
            raw = self._recvall(s, 4)
            fname_len = struct.unpack(">I", raw)[0]
            filename = self._recvall(s, fname_len).decode("utf-8")
            filesize = struct.unpack(">Q", self._recvall(s, 8))[0]
            expected_hash = self._recvall(s, 64).decode("ascii")

            save_path = os.path.join(self.save_dir, filename)
            # Avoid clobbering
            if os.path.exists(save_path):
                base, ext = os.path.splitext(filename)
                save_path = os.path.join(self.save_dir, f"{base}_{int(time.time())}{ext}")

            received = 0
            h = hashlib.sha256()
            with open(save_path, "wb") as f:
                while received < filesize:
                    chunk = s.recv(min(65536, filesize - received))
                    if not chunk:
                        break
                    f.write(chunk)
                    h.update(chunk)
                    received += len(chunk)
                    if self.on_progress:
                        self.on_progress(received, filesize)

            s.close()
            actual_hash = h.hexdigest()
            if actual_hash != expected_hash:
                os.remove(save_path)
                if self.on_done:
                    self.on_done(False, "Hash mismatch — file corrupted")
            else:
                if self.on_done:
                    self.on_done(True, save_path)
        except Exception as e:
            if self.on_done:
                self.on_done(False, str(e))

    def _recvall(self, s: socket.socket, n: int) -> bytes:
        data = bytearray()
        while len(data) < n:
            chunk = s.recv(n - len(data))
            if not chunk:
                raise ConnectionError("Connection closed during receive")
            data.extend(chunk)
        return bytes(data)
