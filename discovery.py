"""
Peer discovery via UDP broadcast/multicast.
Uses only Python stdlib — commercially safe (PSF License).
"""

import socket
import threading
import json
import time
import logging

DISCOVERY_PORT = 55779
BROADCAST_ADDR = "255.255.255.255"
DISCOVERY_INTERVAL = 5  # seconds
PEER_TIMEOUT = 15  # seconds


class Discovery:
    """Announces our presence and discovers other peers on the LAN."""

    def __init__(self, username: str, chat_port: int, on_peer_found, on_peer_lost):
        self.username = username
        self.chat_port = chat_port
        self.on_peer_found = on_peer_found
        self.on_peer_lost = on_peer_lost

        self._peers: dict[str, dict] = {}  # ip -> {username, port, last_seen}
        self._lock = threading.Lock()
        self._running = False

        self._sock = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
        self._sock.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
        self._sock.setsockopt(socket.SOL_SOCKET, socket.SO_BROADCAST, 1)
        self._sock.settimeout(1.0)

        self.local_ip = self._get_local_ip()

    def _get_local_ip(self) -> str:
        try:
            s = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
            s.connect(("8.8.8.8", 80))
            ip = s.getsockname()[0]
            s.close()
            return ip
        except Exception:
            return "127.0.0.1"

    def _make_announce(self) -> bytes:
        msg = json.dumps({
            "type": "announce",
            "username": self.username,
            "port": self.chat_port,
            "ip": self.local_ip,
        })
        return msg.encode("utf-8")

    def _send_goodbye(self):
        try:
            msg = json.dumps({
                "type": "goodbye",
                "username": self.username,
                "ip": self.local_ip,
            }).encode("utf-8")
            self._sock.sendto(msg, (BROADCAST_ADDR, DISCOVERY_PORT))
        except Exception:
            pass

    def _broadcast_loop(self):
        while self._running:
            try:
                self._sock.sendto(self._make_announce(), (BROADCAST_ADDR, DISCOVERY_PORT))
            except Exception as e:
                logging.debug(f"Broadcast error: {e}")
            time.sleep(DISCOVERY_INTERVAL)

    def _listen_loop(self):
        try:
            listen_sock = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
            listen_sock.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
            listen_sock.setsockopt(socket.SOL_SOCKET, socket.SO_BROADCAST, 1)
            listen_sock.bind(("", DISCOVERY_PORT))
            listen_sock.settimeout(1.0)
        except Exception as e:
            logging.error(f"Discovery listen bind failed: {e}")
            return

        while self._running:
            try:
                data, addr = listen_sock.recvfrom(1024)
                src_ip = addr[0]
                if src_ip == self.local_ip:
                    continue
                msg = json.loads(data.decode("utf-8"))
                self._handle_message(msg, src_ip)
            except socket.timeout:
                self._expire_peers()
            except Exception as e:
                logging.debug(f"Discovery recv error: {e}")

        listen_sock.close()

    def _handle_message(self, msg: dict, src_ip: str):
        mtype = msg.get("type")
        uname = msg.get("username", "unknown")
        port = msg.get("port", 55778)
        ip = msg.get("ip", src_ip)

        if mtype == "announce":
            with self._lock:
                is_new = ip not in self._peers
                self._peers[ip] = {
                    "username": uname,
                    "port": port,
                    "last_seen": time.time(),
                    "ip": ip,
                }
            if is_new:
                self.on_peer_found(ip, uname, port)

        elif mtype == "goodbye":
            with self._lock:
                peer = self._peers.pop(ip, None)
            if peer:
                self.on_peer_lost(ip, peer["username"])

    def _expire_peers(self):
        now = time.time()
        with self._lock:
            expired = [ip for ip, p in self._peers.items()
                       if now - p["last_seen"] > PEER_TIMEOUT]
            for ip in expired:
                peer = self._peers.pop(ip)
                self.on_peer_lost(ip, peer["username"])

    def get_peers(self) -> list[dict]:
        with self._lock:
            return list(self._peers.values())

    def start(self):
        self._running = True
        threading.Thread(target=self._broadcast_loop, daemon=True).start()
        threading.Thread(target=self._listen_loop, daemon=True).start()

    def stop(self):
        self._running = False
        self._send_goodbye()
        try:
            self._sock.close()
        except Exception:
            pass
