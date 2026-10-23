"""Single-request OTLP transport with a deadline that closes the active socket."""
from contextlib import contextmanager
import socket
from threading import Lock, Timer

import requests
from requests.adapters import HTTPAdapter


class _Deadline:
    def __init__(self, seconds):
        self._lock = Lock()
        self._connections = []
        self._sockets = []
        self.expired = False
        self._timer = Timer(seconds, self._expire)

    def start(self):
        self._timer.start()

    def finish(self):
        self._timer.cancel()
        self._timer.join()

    def watch(self, connection):
        with self._lock:
            self.check()
            self._connections.append(connection)

    def check(self):
        if self.expired:
            raise requests.Timeout("OTLP target response deadline exceeded")

    def connected(self, sock):
        with self._lock:
            self.check()
            # HTTP/1.0 detaches the connection's socket while the response still
            # owns a file reference. Keep that socket reachable until body close.
            self._sockets.append(sock)

    def _expire(self):
        with self._lock:
            self.expired = True
            sockets = self._sockets + [connection.sock for connection in self._connections]
            for sock in sockets:
                if sock is not None:
                    # close() alone does not wake a read holding a makefile reference.
                    # shutdown() interrupts both header and streaming-body reads.
                    try:
                        sock.shutdown(socket.SHUT_RDWR)
                    except OSError:
                        pass
                    sock.close()


class _DeadlineAdapter(HTTPAdapter):
    def __init__(self, deadline):
        self._deadline = deadline
        super().__init__(max_retries=0)

    def _bind_pools(self, manager):
        deadline = self._deadline
        pools = {}
        for scheme, pool in manager.pool_classes_by_scheme.items():
            class Connection(pool.ConnectionCls):
                def connect(self):
                    deadline.watch(self)
                    super().connect()
                    try:
                        deadline.connected(self.sock)
                    except requests.Timeout:
                        self.close()
                        raise

            pools[scheme] = type("DeadlinePool", (pool,), {"ConnectionCls": Connection})
        manager.pool_classes_by_scheme = pools

    def init_poolmanager(self, *args, **kwargs):
        super().init_poolmanager(*args, **kwargs)
        self._bind_pools(self.poolmanager)

    def proxy_manager_for(self, proxy, **kwargs):
        existing = proxy in self.proxy_manager
        manager = super().proxy_manager_for(proxy, **kwargs)
        if not existing:
            self._bind_pools(manager)
        return manager


@contextmanager
def response_deadline(seconds):
    """Keep Requests' TLS/proxy behavior, but abort slow responses in this caller.

    The timer only closes sockets; it never performs network work. Cancellation joins
    the timer and closes the session on every exit, so no request continues after return.
    The ordinary connect timeout still covers establishing a connection.
    """
    deadline = _Deadline(seconds)
    with requests.Session() as session:
        adapter = _DeadlineAdapter(deadline)
        session.mount("http://", adapter)
        session.mount("https://", adapter)
        deadline.start()
        try:
            yield session
            deadline.check()
        except Exception:
            # Socket shutdown may appear as EOF, a protocol error or a read error.
            # All deadline expirations have one stable, credential-free outcome.
            deadline.check()
            raise
        finally:
            deadline.finish()
