# LanMsg — A Python based LAN Messenger

A peer-to-peer LAN messenger written in **pure Python stdlib** — no third-party
dependencies, 100% commercially safe (PSF License).

---

## Features

| Feature | Status |
|---|---|
| Peer-to-peer (no server needed) | ✅ |
| Automatic peer discovery via UDP broadcast | ✅ |
| Encrypted messaging (HMAC-SHA256 stream cipher) | ✅ |
| File transfer with SHA-256 integrity check | ✅ |
| Accept / Decline incoming file prompt | ✅ |
| File transfer history log | ✅ |
| Message history (JSON-lines, per peer) | ✅ |
| Terminal UI (curses) | ✅ |
| Works across VPNs (TCP) | ✅ |
| Standalone Windows EXE (PyInstaller) | ✅ |

---

## Requirements

- Python 3.10+
- `curses` — built into Python on Linux/macOS
- On Windows: `pip install windows-curses` (MIT licensed, commercially safe)

No other `pip install` needed — pure stdlib.

---

## Quick Start

```bash
python lanmsg.py
python lanmsg.py --username Alice --secret "my-office-passphrase"
python lanmsg.py --downloads ~/Desktop/received
```

All peers on the same LAN **must use the same `--secret`** for messages to decrypt.

---

## Key Bindings

| Key | Action |
|---|---|
| `Tab` | Switch between peers |
| `Enter` | Send message |
| `↑ / ↓` | Scroll chat history |
| `PgUp / PgDn` | Fast scroll |
| `F2` | Send a file (type path, Enter to confirm, ESC to cancel) |
| `F5` | Load saved chat history for current peer |
| `Y` | Accept incoming file (shown in status bar) |
| `N` | Decline incoming file |
| `Ctrl+C` | Quit |

---

## Incoming File Transfers

When a peer sends you a file, a prompt appears in the **bottom status bar**:

```
📎 INCOMING FILE from Alice: report.pdf (142.3 KB) — [Y] Accept  [N] Decline
```

Press **Y** to accept (file saves to your `--downloads` folder) or **N** to decline
(the sender is notified). Multiple incoming files are queued — answered one at a time.

---

## File Transfer History

All file events (sent, received, declined) are logged to:

```
~/.lanmsg_history/_file_transfers.jsonl
```

Each line is a JSON record with timestamp, peer, filename, size, status, and saved path.
This log persists across sessions and survives restarts.

---

## Building a Windows EXE


### Manual build

```bat
pip install pyinstaller windows-curses
pyinstaller lanmsg.spec
```

### Running the EXE

```bat
dist\LanMsg.exe --username Alice --secret "your-passphrase"
```

> **Important:** Run in a real **Command Prompt** or **Windows Terminal**.
> The curses UI will not work in PowerShell ISE or VS Code's integrated terminal.

> **Firewall:** Windows will prompt to allow the app through the firewall on first run.
> Click **Allow** for LAN communication on ports 55778 and 55779.

---

## Architecture

```
lanmsg/
├── lanmsg.py        Main app + entry point
├── discovery.py     UDP broadcast peer discovery
├── network.py       TCP chat server + encrypted connections
├── crypto.py        HMAC-SHA256 stream cipher (stdlib only)
├── filetransfer.py  File send/receive with integrity check
├── history.py       JSON-lines message + file transfer persistence
├── tui.py           Curses terminal UI
├── lanmsg.spec      PyInstaller build spec
```

---

## Ports Used

| Port | Protocol | Purpose |
|---|---|---|
| 55779 | UDP | Peer discovery (broadcast) |
| 55778 | TCP | Encrypted chat |
| 55800–55900 | TCP | File transfers (one per transfer) |

---

## Data Storage  (~/.lanmsg_history/)

| File | Contents |
|---|---|
| `<ip>.jsonl` | Chat message history per peer |
| `_file_transfers.jsonl` | All file transfer events (sent/received/declined) |

---

## License

MIT — use freely in commercial projects.
All dependencies are Python stdlib (PSF License) + optional `windows-curses` (MIT).
