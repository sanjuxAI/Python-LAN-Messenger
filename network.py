import socket
import threading
import json
import struct
import logging
import time
from crypto import encrypt, decrypt
import os

CHAT_PORT = 55778
HEADER_SIZE = 4  # 4-byte big-endian length prefix


def _send_frame(sock: socket.socket, payload: bytes):
    """Send length-prefixed frame."""
    header = struct.pack(">I", len(payload))
    sock.sendall(header + payload)


def _recv_frame(sock: socket.socket) -> bytes | None:
    """Receive length-prefixed frame. Returns None on disconnect."""
    try:
        raw_len = _recvall(sock, HEADER_SIZE)
        if not raw_len:
            return None
        length = struct.unpack(">I", raw_len)[0]
        if length > 10 * 1024 * 1024:  # 10 MB cap
            return None
        return _recvall(sock, length)
    except Exception:
        return None


def _recvall(sock: socket.socket, n: int) -> bytes | None:
    data = bytearray()
    while len(data) < n:
        try:
            packet = sock.recv(n - len(data))
        except Exception:
            return None
        if not packet:
            return None
        data.extend(packet)
    return bytes(data)


class Connection:
    """Wraps a single TCP connection to a peer."""

    def __init__(self, sock: socket.socket, peer_ip: str,
                 key: bytes | None, on_message, on_disconnect):
        self.sock = sock
        self.peer_ip = peer_ip
        self.key = key
        self.on_message = on_message
        self.on_disconnect = on_disconnect
        self._alive = True

        threading.Thread(target=self._recv_loop, daemon=True).start()

    def send(self, msg: dict):
        try:
            payload = json.dumps(msg).encode("utf-8")
            if self.key:
                payload = encrypt(payload, self.key)
            _send_frame(self.sock, payload)
        except Exception as e:
            logging.debug(f"Send error to {self.peer_ip}: {e}")
            self._close()

    def _recv_loop(self):
        while self._alive:
            frame = _recv_frame(self.sock)
            if frame is None:
                break
            try:
                if self.key:
                    frame = decrypt(frame, self.key)
                    if frame is None:
                        logging.warning(f"Decryption failed from {self.peer_ip}")
                        continue
                msg = json.loads(frame.decode("utf-8"))
                self.on_message(self.peer_ip, msg)
            except Exception as e:
                logging.debug(f"Recv parse error: {e}")
        self._close()

    def _close(self):
        if self._alive:
            self._alive = False
            try:
                self.sock.close()
            except Exception:
                pass
            self.on_disconnect(self.peer_ip)

    def close(self):
        self._close()

    @property
    def alive(self):
        return self._alive


class ChatServer:
    """
    Manages all incoming and outgoing TCP connections.
    """

    def __init__(self, username: str, shared_secret: str,
                 on_message, on_peer_connected, on_peer_disconnected):
        self.username = username
        self.shared_secret = shared_secret
        self.on_message = on_message
        self.on_peer_connected = on_peer_connected
        self.on_peer_disconnected = on_peer_disconnected

        self._connections: dict[str, Connection] = {}
        self._lock = threading.Lock()
        self._running = False

        self._server_sock = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
        if os.name != 'nt':
            self._server_sock.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
        self._server_sock.bind(("", CHAT_PORT))
        self._server_sock.listen(20)
        self._server_sock.settimeout(1.0)

    def _derive_key(self, peer_ip: str) -> bytes:
        from crypto import key_exchange_hash
        # local IP for key derivation
        try:
            s = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
            s.connect(("8.8.8.8", 80))
            local_ip = s.getsockname()[0]
            s.close()
        except Exception:
            local_ip = "127.0.0.1"
        return key_exchange_hash(self.shared_secret, peer_ip, local_ip)

    def _accept_loop(self):
        while self._running:
            try:
                client_sock, addr = self._server_sock.accept()
                peer_ip = addr[0]
                key = self._derive_key(peer_ip)
                conn = Connection(
                    client_sock, peer_ip, key,
                    self._on_msg, self._on_disc
                )
                with self._lock:
                    self._connections[peer_ip] = conn
                # Send handshake
                conn.send({"type": "hello", "username": self.username})
                self.on_peer_connected(peer_ip, "")
            except socket.timeout:
                continue
            except Exception as e:
                if self._running:
                    logging.debug(f"Accept error: {e}")

    def _on_msg(self, peer_ip: str, msg: dict):
        if msg.get("type") == "hello":
            username = msg.get("username", peer_ip)
            self.on_peer_connected(peer_ip, username)
        else:
            self.on_message(peer_ip, msg)

    def _on_disc(self, peer_ip: str):
        with self._lock:
            self._connections.pop(peer_ip, None)
        self.on_peer_disconnected(peer_ip)

    def connect_to(self, peer_ip: str, peer_port: int) -> bool:
        """Establish outgoing connection to a peer."""
        with self._lock:
            if peer_ip in self._connections and self._connections[peer_ip].alive:
                return True
        try:
            s = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
            s.settimeout(5)
            s.connect((peer_ip, peer_port))
            s.settimeout(None)
            key = self._derive_key(peer_ip)
            conn = Connection(s, peer_ip, key, self._on_msg, self._on_disc)
            with self._lock:
                self._connections[peer_ip] = conn
            conn.send({"type": "hello", "username": self.username})
            return True
        except Exception as e:
            logging.debug(f"Connect to {peer_ip} failed: {e}")
            return False

    def send_to(self, peer_ip: str, msg: dict) -> bool:
        with self._lock:
            conn = self._connections.get(peer_ip)
        if conn and conn.alive:
            conn.send(msg)
            return True
        return False

    def broadcast(self, msg: dict):
        with self._lock:
            conns = list(self._connections.values())
        for conn in conns:
            if conn.alive:
                conn.send(msg)

    def get_connected_ips(self) -> list[str]:
        with self._lock:
            return [ip for ip, c in self._connections.items() if c.alive]

    def start(self):
        self._running = True
        threading.Thread(target=self._accept_loop, daemon=True).start()

    def stop(self):
        self._running = False
        with self._lock:
            for conn in self._connections.values():
                conn.close()
            self._connections.clear()
        try:
            self._server_sock.close()
        except Exception:
            pass
