"""Cooperative cancellation tied to one HTTP connection, without shared jobs."""
from __future__ import annotations

import contextlib
import select
import socket
import sqlite3
import time


class RequestCancelled(ConnectionError):
    pass


class RequestCancellation:
    def __init__(self, connection: socket.socket | None) -> None:
        self.connection = connection
        self.cancelled = False
        self.next_check = 0.0

    def check(self) -> None:
        if self.cancelled:
            raise RequestCancelled("HTTP client disconnected")
        now = time.monotonic()
        if self.connection is None or now < self.next_check:
            return
        self.next_check = now + 0.05
        try:
            readable, _, _ = select.select([self.connection], [], [], 0)
            # Peek rather than consume: only EOF cancels this request.
            disconnected = bool(readable) and not self.connection.recv(1, socket.MSG_PEEK)
        except OSError:
            disconnected = True
        if disconnected:
            self.cancelled = True
            raise RequestCancelled("HTTP client disconnected")

    @contextlib.contextmanager
    def during_queries(self, conn: sqlite3.Connection):
        def progress() -> int:
            try:
                self.check()
            except RequestCancelled:
                return 1
            return 0

        self.check()
        conn.set_progress_handler(progress, 1000)
        try:
            yield
        except sqlite3.OperationalError:
            if self.cancelled:
                raise RequestCancelled("HTTP client disconnected") from None
            raise
        finally:
            conn.set_progress_handler(None, 0)
