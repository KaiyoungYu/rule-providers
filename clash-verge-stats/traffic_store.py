"""Collect per-connection mihomo counters into a local rolling 24-hour view."""

from __future__ import annotations

import http.client
import json
import os
import re
import socket
import sqlite3
import threading
import time
from pathlib import Path
from urllib.parse import urlsplit


POLL_SECONDS = 1
WINDOW_SECONDS = 24 * 60 * 60
DB_PATH = Path(__file__).with_name("traffic.sqlite3")
VERGE_CONFIG = (
    Path.home()
    / "Library/Application Support/io.github.clash-verge-rev.clash-verge-rev/clash-verge.yaml"
)


class UnixHTTPConnection(http.client.HTTPConnection):
    def __init__(self, socket_path: str, timeout: float = 2.0):
        super().__init__("localhost", timeout=timeout)
        self.socket_path = socket_path

    def connect(self) -> None:
        self.sock = socket.socket(socket.AF_UNIX, socket.SOCK_STREAM)
        self.sock.settimeout(self.timeout)
        self.sock.connect(self.socket_path)


def configured_socket() -> str | None:
    explicit = os.getenv("CLASH_SOCKET")
    if explicit:
        return explicit
    try:
        config = VERGE_CONFIG.read_text(encoding="utf-8")
    except OSError:
        return None
    match = re.search(r"^\s*external-controller-unix:\s*(.*?)\s*$", config, re.MULTILINE)
    if not match:
        return None
    return match.group(1).strip("\"'") or None


def source_label() -> str:
    if url := os.getenv("CLASH_API_URL"):
        try:
            parsed = urlsplit(url)
        except ValueError:
            return "HTTP 控制接口（地址无效）"
        if parsed.hostname:
            host = parsed.hostname
            if ":" in host:
                host = f"[{host}]"
            try:
                port = f":{parsed.port}" if parsed.port else ""
            except ValueError:
                return "HTTP 控制接口（端口无效）"
            return f"{parsed.scheme}://{host}{port}"
        return "HTTP 控制接口"
    path = configured_socket()
    return f"Unix Socket: {path}" if path else "未找到 Clash 控制接口"


def fetch_connections() -> list[dict]:
    url = os.getenv("CLASH_API_URL")
    if url:
        parsed = urlsplit(url)
        if parsed.scheme not in {"http", "https"} or not parsed.hostname:
            raise ValueError("CLASH_API_URL 需要是 http(s)://主机:端口")
        if parsed.username or parsed.password:
            raise ValueError("请通过 CLASH_SECRET 设置密钥，不要放入 CLASH_API_URL")
        cls = http.client.HTTPSConnection if parsed.scheme == "https" else http.client.HTTPConnection
        conn = cls(parsed.hostname, parsed.port, timeout=2)
        path = parsed.path.rstrip("/")
        endpoint = path if path.endswith("/connections") else path + "/connections"
        if parsed.query:
            endpoint += "?" + parsed.query
    else:
        unix_path = configured_socket()
        if not unix_path:
            raise RuntimeError("未找到 Unix Socket；请设置 CLASH_API_URL")
        conn = UnixHTTPConnection(unix_path)
        endpoint = "/connections"

    secret = os.getenv("CLASH_SECRET", "")
    headers = {"Authorization": f"Bearer {secret}"} if secret else {}
    try:
        conn.request("GET", endpoint, headers=headers)
        response = conn.getresponse()
        data = response.read()
        if response.status != 200:
            raise RuntimeError(f"Clash API 返回 HTTP {response.status}")
        payload = json.loads(data)
        connections = payload.get("connections", [])
        if not isinstance(connections, list):
            raise ValueError("API 响应缺少 connections 列表")
        return connections
    finally:
        conn.close()


def open_db(path: Path = DB_PATH) -> sqlite3.Connection:
    db = sqlite3.connect(path, timeout=5)
    db.execute("PRAGMA journal_mode=WAL")
    db.execute("PRAGMA busy_timeout=5000")
    if os.name == "posix":
        for suffix in ("", "-wal", "-shm"):
            candidate = Path(str(path) + suffix)
            if candidate.exists():
                candidate.chmod(0o600)
    db.executescript(
        """
        CREATE TABLE IF NOT EXISTS connection_state (
            id TEXT PRIMARY KEY,
            target TEXT NOT NULL,
            download INTEGER NOT NULL,
            upload INTEGER NOT NULL,
            last_seen INTEGER NOT NULL
        );
        CREATE TABLE IF NOT EXISTS traffic_minute (
            bucket INTEGER NOT NULL,
            target TEXT NOT NULL,
            download INTEGER NOT NULL DEFAULT 0,
            upload INTEGER NOT NULL DEFAULT 0,
            PRIMARY KEY (bucket, target)
        );
        CREATE TABLE IF NOT EXISTS meta (key TEXT PRIMARY KEY, value TEXT NOT NULL);
        """
    )
    return db


def _counter(value: object) -> int:
    try:
        return max(0, int(value))
    except (TypeError, ValueError):
        return 0


def csv_safe_target(target: str) -> str:
    """Prevent spreadsheet apps from treating an untrusted target as a formula."""
    if target.lstrip().startswith(("=", "+", "-", "@")) or target.startswith(("\t", "\r", "\n")):
        return "'" + target
    return target


def record_sample(db: sqlite3.Connection, connections: list[dict], now: int) -> None:
    """Store counter differences, baselining after startup or a sampling gap."""
    bucket = now // 60 * 60
    last_sample = db.execute("SELECT value FROM meta WHERE key='last_sample'").fetchone()
    baseline = last_sample is None or now - int(last_sample[0]) > 10 or now < int(last_sample[0])
    with db:
        for item in connections:
            if not isinstance(item, dict):
                continue
            metadata = item.get("metadata") or {}
            if not isinstance(metadata, dict):
                continue
            target = str(metadata.get("host") or metadata.get("destinationIP") or "").strip()
            connection_id = str(item.get("id") or "").strip()
            if not target or not connection_id:
                continue
            key = connection_id + "|" + str(item.get("start") or "")
            down, up = _counter(item.get("download")), _counter(item.get("upload"))
            previous = db.execute(
                "SELECT download, upload FROM connection_state WHERE id=?", (key,)
            ).fetchone()
            if baseline:
                delta_down = delta_up = 0
            elif previous:
                delta_down = max(0, down - previous[0])
                delta_up = max(0, up - previous[1])
            else:
                delta_down, delta_up = down, up
            db.execute(
                """INSERT INTO connection_state VALUES (?, ?, ?, ?, ?)
                   ON CONFLICT(id) DO UPDATE SET target=excluded.target,
                   download=excluded.download, upload=excluded.upload,
                   last_seen=excluded.last_seen""",
                (key, target, down, up, now),
            )
            if delta_down or delta_up:
                db.execute(
                    """INSERT INTO traffic_minute VALUES (?, ?, ?, ?)
                       ON CONFLICT(bucket, target) DO UPDATE SET
                       download=download+excluded.download, upload=upload+excluded.upload""",
                    (bucket, target, delta_down, delta_up),
                )
        db.execute("INSERT OR REPLACE INTO meta VALUES ('last_sample', ?)", (str(now),))
        db.execute("DELETE FROM traffic_minute WHERE bucket < ?", (bucket - WINDOW_SECONDS + 60,))
        db.execute("DELETE FROM connection_state WHERE last_seen < ?", (now - 2 * WINDOW_SECONDS,))


def read_stats(db: sqlite3.Connection, now: int) -> list[tuple[str, int, int]]:
    cutoff = now // 60 * 60 - WINDOW_SECONDS + 60
    return db.execute(
        """SELECT target, SUM(download), SUM(upload) FROM traffic_minute
           WHERE bucket >= ? GROUP BY target
           ORDER BY SUM(download + upload) DESC""",
        (cutoff,),
    ).fetchall()


class Monitor:
    def __init__(self, path: Path = DB_PATH):
        self.path = path
        self.last_success: float | None = None
        self.last_error: str | None = None
        self._lock = threading.Lock()
        self._stop = threading.Event()
        self._thread = threading.Thread(target=self._run, daemon=True, name="clash-traffic-monitor")
        self._thread.start()

    def _run(self) -> None:
        db: sqlite3.Connection | None = None
        try:
            while not self._stop.is_set():
                try:
                    if db is None:
                        db = open_db(self.path)
                    connections = fetch_connections()
                    now = int(time.time())
                    record_sample(db, connections, now)
                    with self._lock:
                        self.last_success = time.time()
                        self.last_error = None
                except Exception as exc:
                    if isinstance(exc, sqlite3.Error) and db is not None:
                        db.close()
                        db = None
                    with self._lock:
                        self.last_error = str(exc)
                self._stop.wait(POLL_SECONDS)
        finally:
            if db is not None:
                db.close()

    def status(self) -> tuple[float | None, str | None]:
        with self._lock:
            return self.last_success, self.last_error

    def close(self) -> None:
        self._stop.set()
        self._thread.join(timeout=3)
