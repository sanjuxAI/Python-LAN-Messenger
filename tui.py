"""
Curses-based Terminal UI for LanMsg.
Stdlib only — commercially safe.

Layout:
┌─────────────────────────────────────────────────────┐
│ UIIC LAN Messenger · user@ip · 3 peers online       │
├──────────────┬──────────────────────────────────────┤
│ PEERS        │ Chat with: Alice (● online)          │
│ ● Alice      │ Hello                                │
│ ● Bob        │ Hi                                   │
│ ○ Carol      │                                      │
│              │                                      │
├──────────────┴──────────────────────────────────────┤
│ > typing message here                               │
│ Ready. Tab=switch peer | F2=send file               │
│     Developed by Sanju Sarkar | LAN Messenger v1.0  │
└─────────────────────────────────────────────────────┘
"""

import curses
import threading
import time
import os
import sys
from datetime import datetime
from typing import Optional


# ── Colour pair indices ──────────────────────────────
C_HEADER   = 1
C_SIDEBAR  = 2
C_ACTIVE   = 3
C_MSG_ME   = 4
C_MSG_THEM = 5
C_STATUS   = 6
C_INPUT    = 7
C_SYSTEM   = 8
C_ONLINE   = 9
C_OFFLINE  = 10
C_FILE     = 11
C_FOOTER = 12

def _init_colours():
    curses.start_color()
    curses.use_default_colors()
    curses.init_pair(C_HEADER,   curses.COLOR_BLACK,  curses.COLOR_CYAN)
    curses.init_pair(C_SIDEBAR,  curses.COLOR_CYAN,   -1)
    curses.init_pair(C_ACTIVE,   curses.COLOR_BLACK,  curses.COLOR_BLUE)
    curses.init_pair(C_MSG_ME,   curses.COLOR_GREEN,  -1)
    curses.init_pair(C_MSG_THEM, curses.COLOR_CYAN,   -1)
    curses.init_pair(C_STATUS,   curses.COLOR_YELLOW, -1)
    curses.init_pair(C_INPUT,    curses.COLOR_WHITE,  -1)
    curses.init_pair(C_SYSTEM,   curses.COLOR_MAGENTA,-1)
    curses.init_pair(C_ONLINE,   curses.COLOR_GREEN,  -1)
    curses.init_pair(C_OFFLINE,  curses.COLOR_RED,    -1)
    curses.init_pair(C_FILE,     curses.COLOR_YELLOW, -1)
    curses.init_pair(C_FOOTER, curses.COLOR_BLACK, curses.COLOR_WHITE)

class ChatMessage:
    def __init__(self, sender: str, text: str, is_me: bool,
                 ts: float | None = None, is_system: bool = False,
                 is_file: bool = False):
        self.sender = sender
        self.text = text
        self.is_me = is_me
        self.is_system = is_system
        self.is_file = is_file
        self.ts = ts or time.time()

    def timestamp_str(self) -> str:
        return datetime.fromtimestamp(self.ts).strftime("%H:%M")


class TUI:
    def __init__(self, app):
        self.app = app          # reference to App instance
        self.stdscr = None
        self._lock = threading.Lock()
        self._running = False

        # Peer list: list of dicts {ip, username, online, port}
        self._peers: list[dict] = []
        self._selected_peer_idx: int = 0

        # Messages per peer: {ip: [ChatMessage]}
        self._messages: dict[str, list[ChatMessage]] = {}

        # Input buffer
        self._input = ""
        self._input_cursor = 0
        self._input_scroll = 0  # horizontal scroll offset for long inputs

        # Scroll position in chat (lines from bottom)
        self._chat_scroll = 0

        # Status bar text
        self._status = "Ready. Tab=switch peer | F2=send file | Ctrl+C=quit"

        # File prompt mode state (None = not in prompt)
        self._file_prompt_active = False
        self._file_prompt_buf = ""
        self._file_prompt_scroll = 0

        # File accept/decline prompt queue: list of pending dicts
        # {peer_ip, peer_username, filename, filesize, port}
        self._file_accept_queue: list[dict] = []
        self._file_accept_active = False   # True when showing Y/N for top of queue

    # ── Public API (called from other threads) ───────────────

    def add_peer(self, ip: str, username: str, port: int):
        with self._lock:
            was_empty = not any(p["online"] for p in self._peers)
            for p in self._peers:
                if p["ip"] == ip:
                    p["username"] = username
                    p["online"] = True
                    break
            else:
                self._peers.append({"ip": ip, "username": username,
                                    "online": True, "port": port})
            # Auto-select this peer if nobody was online/selected before
            if was_empty:
                online = [i for i, p in enumerate(self._peers) if p["online"]]
                if online:
                    self._selected_peer_idx = online[0]
        self._refresh()

    def remove_peer(self, ip: str):
        with self._lock:
            for p in self._peers:
                if p["ip"] == ip:
                    p["online"] = False
                    break
        self._refresh()

    def add_message(self, peer_ip: str, msg: ChatMessage):
        with self._lock:
            self._messages.setdefault(peer_ip, []).append(msg)
        self._refresh()

    def set_status(self, text: str):
        with self._lock:
            self._status = text
        self._refresh()

    def ask_accept_file(self, peer_ip: str, peer_username: str,
                        filename: str, filesize: int, port: int):
        """Queue a file accept/decline prompt. Thread-safe."""
        with self._lock:
            self._file_accept_queue.append({
                "peer_ip": peer_ip,
                "peer_username": peer_username,
                "filename": filename,
                "filesize": filesize,
                "port": port,
            })
        self._refresh()

    # ── Selected peer helpers ────────────────────────────────

    def _selected_peer(self) -> Optional[dict]:
        with self._lock:
            if not self._peers:
                return None
            idx = min(self._selected_peer_idx, len(self._peers) - 1)
            return self._peers[idx]

    def _selected_ip(self) -> Optional[str]:
        p = self._selected_peer()
        return p["ip"] if p else None

    # ── Main entry point ─────────────────────────────────────

    def run(self):
        curses.wrapper(self._main)

    def _main(self, stdscr):
        self.stdscr = stdscr
        _init_colours()
        curses.curs_set(1)
        stdscr.nodelay(True)   # non-blocking getch
        stdscr.keypad(True)

        self._running = True
        self._needs_redraw = True
        self._draw()

        while self._running:
            try:
                key = stdscr.getch()
                if key == curses.ERR:
                    # No key pressed — check if a redraw was requested
                    if self._needs_redraw:
                        self._needs_redraw = False
                        self._draw()
                    else:
                        time.sleep(0.02)   # 20 ms idle sleep to avoid busy-loop
                else:
                    self._handle_key(key)
            except KeyboardInterrupt:
                self._running = False
            except curses.error:
                pass

        self.app.shutdown()

    def _refresh(self):
        """Thread-safe redraw trigger — called from any thread."""
        self._needs_redraw = True

    # ── Drawing ──────────────────────────────────────────────

    def _draw(self):
        if not self.stdscr:
            return
        try:
            self.stdscr.erase()
            h, w = self.stdscr.getmaxyx()
            if h < 10 or w < 40:
                self.stdscr.addstr(0, 0, "Terminal too small!")
                self.stdscr.refresh()
                return

            sidebar_w = min(22, w // 4)
            chat_x = sidebar_w + 1
            chat_w = w - chat_x
            # Rows: header(1) + body(h-4) + input(1) + help(1) + border(1)
            header_y = 0
            body_y = 1
            body_h = h - 4
            input_y = h - 3
            help_y = h - 2
            div_y = h - 1  # bottom border line (unused, just reference)

            self._draw_header(header_y, w)
            self._draw_sidebar(body_y, body_h, sidebar_w)
            self._draw_divider_v(body_y, body_h, sidebar_w)
            self._draw_chat(body_y, body_h, chat_x, chat_w)
            self._draw_input(input_y, w)
            self._draw_help(help_y, w)
            self._draw_footer(div_y, w)
            # Position cursor in input
            inp_vis = self._input_visible(w - 4)
            cursor_x = min(2 + (self._input_cursor - self._input_scroll), w - 2)
            try:
                self.stdscr.move(input_y, cursor_x)
            except curses.error:
                pass

            self.stdscr.refresh()
        except curses.error:
            pass

    def _draw_header(self, y: int, w: int):
        s = self.stdscr
        username = self.app.username
        ip = self.app.local_ip
        with self._lock:
            n_online = sum(1 for p in self._peers if p["online"])
        title = f" LanMsg  ·  {username}@{ip}  ·  {n_online} peer(s) online"
        title = title.ljust(w)[:w]
        try:
            s.addstr(y, 0, title, curses.color_pair(C_HEADER) | curses.A_BOLD)
        except curses.error:
            pass

    def _draw_sidebar(self, y: int, h: int, w: int):
        s = self.stdscr
        # Header
        hdr = " PEERS ".center(w)
        try:
            s.addstr(y, 0, hdr[:w], curses.color_pair(C_SIDEBAR) | curses.A_BOLD)
        except curses.error:
            pass

        with self._lock:
            peers = list(self._peers)
            sel = self._selected_peer_idx

        for i, peer in enumerate(peers):
            row = y + 1 + i
            if row >= y + h:
                break
            online = peer["online"]
            indicator = "●" if online else "○"
            name = peer["username"][:w - 5]
            line = f" {indicator} {name}"
            line = line.ljust(w)[:w]
            colour = curses.color_pair(C_ONLINE if online else C_OFFLINE)
            if i == sel:
                attr = curses.color_pair(C_ACTIVE) | curses.A_BOLD
            else:
                attr = colour
            try:
                s.addstr(row, 0, line, attr)
            except curses.error:
                pass

        if not peers:
            try:
                s.addstr(y + 2, 0, " (no peers yet)", curses.color_pair(C_STATUS))
                s.addstr(y + 3, 0, " Waiting for", curses.color_pair(C_STATUS))
                s.addstr(y + 4, 0, " discovery...", curses.color_pair(C_STATUS))
            except curses.error:
                pass

    def _draw_divider_v(self, y: int, h: int, x: int):
        s = self.stdscr
        for row in range(y, y + h):
            try:
                s.addch(row, x, curses.ACS_VLINE, curses.color_pair(C_SIDEBAR))
            except curses.error:
                pass

    def _draw_chat(self, y: int, h: int, x: int, w: int):
        s = self.stdscr
        peer = self._selected_peer()

        # Header bar
        if peer:
            status = "● online" if peer["online"] else "○ offline"
            hdr = f" Chat with: {peer['username']} ({status}) "
        else:
            hdr = " No peer selected — waiting for discovery "
        hdr = hdr.ljust(w)[:w]
        try:
            s.addstr(y, x, hdr, curses.color_pair(C_SIDEBAR) | curses.A_BOLD)
        except curses.error:
            pass

        # Messages area
        area_h = h - 1
        area_y = y + 1

        msgs = []
        if peer:
            with self._lock:
                msgs = list(self._messages.get(peer["ip"], []))

        # Wrap messages to fit width
        lines = []  # list of (attr, text_str)
        for m in msgs:
            ts = m.timestamp_str()
            prefix_me   = f"[{ts}] You: "
            prefix_them = f"[{ts}] {m.sender}: "
            if m.is_system:
                prefix = f"[{ts}] *** "
                attr = curses.color_pair(C_SYSTEM) | curses.A_ITALIC
            elif m.is_file:
                prefix = f"[{ts}]  "
                attr = curses.color_pair(C_FILE) | curses.A_BOLD
            elif m.is_me:
                prefix = prefix_me
                attr = curses.color_pair(C_MSG_ME)
            else:
                prefix = prefix_them
                attr = curses.color_pair(C_MSG_THEM)

            full = prefix + m.text
            # Word-wrap
            while full:
                lines.append((attr, full[:w - 1]))
                full = full[w - 1:]
                if full:
                    full = "  " + full  # indent continuation

        total = len(lines)
        scroll = min(self._chat_scroll, max(0, total - area_h))
        visible = lines[max(0, total - area_h - scroll): total - scroll]

        for i, (attr, text) in enumerate(visible):
            row = area_y + i
            if row >= area_y + area_h:
                break
            try:
                s.addstr(row, x, text.ljust(w - 1)[:w - 1], attr)
            except curses.error:
                pass

    def _input_visible(self, w: int) -> str:
        return self._input[self._input_scroll: self._input_scroll + w]

    def _draw_input(self, y: int, w: int):
        s = self.stdscr
        if self._file_prompt_active:
            prompt = " File path (ESC=cancel): "
            available = w - len(prompt) - 1
            buf = self._file_prompt_buf
            scroll = self._file_prompt_scroll
            visible = buf[scroll: scroll + available]
            line = (prompt + visible).ljust(w)[:w]
            try:
                s.addstr(y, 0, line, curses.color_pair(C_FILE) | curses.A_BOLD)
                cursor_x = min(len(prompt) + (len(buf) - scroll), w - 1)
                self.stdscr.move(y, cursor_x)
            except curses.error:
                pass
        else:
            prompt = "> "
            available = w - len(prompt) - 1
            visible = self._input_visible(available)
            line = (prompt + visible).ljust(w)[:w]
            try:
                s.addstr(y, 0, line, curses.color_pair(C_INPUT) | curses.A_BOLD)
            except curses.error:
                pass

    def _draw_help(self, y: int, w: int):
        s = self.stdscr
        with self._lock:
            status = self._status
            pending = list(self._file_accept_queue)

        if pending:
            f = pending[0]
            size_kb = f["filesize"] / 1024
            banner = (f"  INCOMING FILE from {f['peer_username']}: "
                      f"{f['filename']} ({size_kb:.1f} KB)  — "
                      f"[Y] Accept  [N] Decline"
                      + (f"  (+{len(pending)-1} more)" if len(pending) > 1 else ""))
            line = banner[:w].ljust(w)
            try:
                s.addstr(y, 0, line, curses.color_pair(C_FILE) | curses.A_BOLD)
            except curses.error:
                pass
        else:
            line = f" {status}"[:w].ljust(w)
            try:
                s.addstr(y, 0, line, curses.color_pair(C_STATUS))
            except curses.error:
                pass
    def _draw_footer(self, y: int, w: int):
        s = self.stdscr

        version = "v1.1"
        company = "Developed by Sanju Sarkar"

        footer = f" {company} | LAN Messenger {version} "
        footer = footer.center(w)

        try:
            s.addstr(y, 0, footer[:w],
                    curses.color_pair(C_FOOTER) | curses.A_BOLD)
        except curses.error:
            pass
    # ── Key handling ─────────────────────────────────────────

    def _handle_key(self, key: int):
        h, w = self.stdscr.getmaxyx()
        sidebar_w = min(22, w // 4)

        if key == curses.KEY_RESIZE:
            self._draw()
            return

        # ── File prompt mode — intercepts all keys ──
        if self._file_prompt_active:
            self._handle_file_prompt_key(key, w)
            return

        # ── File accept/decline — Y or N when a request is pending ──
        with self._lock:
            has_pending = bool(self._file_accept_queue)
        if has_pending and key in (ord('y'), ord('Y'), ord('n'), ord('N')):
            self._handle_file_accept_key(key)
            return

        # ── Navigation ──
        if key == ord('\t'):            # Tab: cycle peers
            with self._lock:
                n = len(self._peers)
            if n:
                self._selected_peer_idx = (self._selected_peer_idx + 1) % n
                self._chat_scroll = 0
            self._draw()
            return

        if key == curses.KEY_UP:
            self._chat_scroll += 1
            self._draw()
            return

        if key == curses.KEY_DOWN:
            self._chat_scroll = max(0, self._chat_scroll - 1)
            self._draw()
            return

        if key == curses.KEY_PPAGE:    # Page Up
            self._chat_scroll += (h - 6)
            self._draw()
            return

        if key == curses.KEY_NPAGE:    # Page Down
            self._chat_scroll = max(0, self._chat_scroll - (h - 6))
            self._draw()
            return

        # ── Function keys ──
        if key == curses.KEY_F2:
            self._file_prompt_active = True
            self._file_prompt_buf = ""
            self._file_prompt_scroll = 0
            self.set_status("Type file path and press Enter. ESC to cancel.")
            self._draw()
            return

        if key == curses.KEY_F5:
            self._load_history()
            return

        # ── Input editing ──
        if key in (curses.KEY_BACKSPACE, 127, 8):
            if self._input_cursor > 0:
                pos = self._input_cursor
                self._input = self._input[:pos-1] + self._input[pos:]
                self._input_cursor -= 1
                self._sync_scroll(w)
            self._draw()
            return

        if key == curses.KEY_DC:       # Delete
            pos = self._input_cursor
            self._input = self._input[:pos] + self._input[pos+1:]
            self._draw()
            return

        if key == curses.KEY_LEFT:
            self._input_cursor = max(0, self._input_cursor - 1)
            self._sync_scroll(w)
            self._draw()
            return

        if key == curses.KEY_RIGHT:
            self._input_cursor = min(len(self._input), self._input_cursor + 1)
            self._sync_scroll(w)
            self._draw()
            return

        if key == curses.KEY_HOME:
            self._input_cursor = 0
            self._input_scroll = 0
            self._draw()
            return

        if key == curses.KEY_END:
            self._input_cursor = len(self._input)
            self._sync_scroll(w)
            self._draw()
            return

        if key in (10, 13):            # Enter
            self._send_current_input()
            return

        if key == 3:                   # Ctrl+C
            self._running = False
            return

        # ── Printable character ──
        if 32 <= key <= 126:
            ch = chr(key)
            pos = self._input_cursor
            self._input = self._input[:pos] + ch + self._input[pos:]
            self._input_cursor += 1
            self._sync_scroll(w)
            self._draw()

    def _sync_scroll(self, win_w: int):
        """Ensure cursor is visible in input box."""
        available = win_w - 4
        if self._input_cursor < self._input_scroll:
            self._input_scroll = self._input_cursor
        elif self._input_cursor >= self._input_scroll + available:
            self._input_scroll = self._input_cursor - available + 1

    def _send_current_input(self):
        text = self._input.strip()
        self._input = ""
        self._input_cursor = 0
        self._input_scroll = 0

        if not text:
            self._draw()
            return

        peer = self._selected_peer()
        if not peer:
            self.set_status("No peer selected.")
            self._draw()
            return

        self.app.send_message(peer["ip"], peer["port"], text)
        self._draw()

    def _handle_file_prompt_key(self, key: int, w: int):
        """Handle a single keypress while the file path prompt is active."""
        buf = self._file_prompt_buf
        prompt = " File path (ESC=cancel): "
        available = w - len(prompt) - 1

        if key == 27:                               # ESC — cancel
            self._file_prompt_active = False
            self._file_prompt_buf = ""
            self.set_status("File send cancelled.  Tab=switch peer | F2=send file | Ctrl+C=quit")
            self._draw()
            return

        if key in (10, 13):                         # Enter — submit
            self._file_prompt_active = False
            path = buf.strip().strip('"\'')
            self._file_prompt_buf = ""
            self.set_status("Tab=switch peer | F2=send file | Ctrl+C=quit")
            if path:
                peer = self._selected_peer()
                if peer:
                    self.app.send_file(peer["ip"], peer["port"], path)
                else:
                    self.set_status("No peer selected.")
            self._draw()
            return

        if key in (curses.KEY_BACKSPACE, 127, 8):   # Backspace
            self._file_prompt_buf = buf[:-1]
        elif key == curses.KEY_DC:                   # Delete — ignore
            pass
        elif 32 <= key <= 126:                       # Printable char
            self._file_prompt_buf = buf + chr(key)

        # Update horizontal scroll so cursor stays visible
        buf = self._file_prompt_buf
        cursor = len(buf)
        scroll = self._file_prompt_scroll
        if cursor < scroll:
            scroll = cursor
        elif cursor > scroll + available:
            scroll = cursor - available
        self._file_prompt_scroll = scroll
        self._draw()

    def _handle_file_accept_key(self, key: int):
        """Handle Y/N response to an incoming file request."""
        with self._lock:
            if not self._file_accept_queue:
                return
            f = self._file_accept_queue.pop(0)

        accepted = key in (ord('y'), ord('Y'))
        self.app.respond_to_file_offer(f, accepted)
        self._draw()

    def _load_history(self):
        peer = self._selected_peer()
        if not peer:
            return
        self.app.load_history_for(peer["ip"])
