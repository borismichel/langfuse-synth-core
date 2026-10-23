"""Foreign runtime OTLP transport stays bound to the Adapter's own target."""
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from threading import Event, Thread
from time import monotonic
from types import SimpleNamespace
import pytest
import requests
from langfuse_synth_core.companion import CompanionAdapter


def test_native_runtime_export_preserves_payload_auth_and_partial_result(monkeypatch):
    seen = []
    status = [200]
    class Target(BaseHTTPRequestHandler):
        def log_message(self, *args):
            pass
        def do_POST(self):
            body = self.rfile.read(int(self.headers['content-length']))
            seen.append((self.path, dict(self.headers), body))
            self.send_response(status[0])
            self.send_header('Location', '/must-not-follow')
            self.send_header('content-type', 'application/x-protobuf')
            self.end_headers()
            self.wfile.write(b'\x0a\x02\x08\x01')  # Original partial-success response is retained.
    server = ThreadingHTTPServer(('127.0.0.1', 0), Target)
    thread = Thread(target=server.serve_forever, daemon=True)
    thread.start()
    monkeypatch.setenv('LANGFUSE_BASE_URL', f'http://127.0.0.1:{server.server_port}')
    monkeypatch.setenv('LANGFUSE_PUBLIC_KEY', 'pk-fixture')
    monkeypatch.setenv('LANGFUSE_SECRET_KEY', 'sk-fixture')
    adapter = CompanionAdapter(SimpleNamespace(target=SimpleNamespace(base_url='https://unrelated.invalid')))
    try:
        assert adapter.forward_otel(b'\x0a\x00') == b'\x0a\x02\x08\x01'
        assert len(seen) == 1
        path, headers, body = seen[0]
        assert path == '/api/public/otel/v1/traces' and body == b'\x0a\x00'
        assert headers['Content-Type'] == 'application/x-protobuf'
        assert headers['x-langfuse-ingestion-version'] == '4'
        assert headers['Authorization'] == 'Basic cGstZml4dHVyZTpzay1maXh0dXJl'
        for invalid in ('text', b'x' * (1024 * 1024 + 1)):
            with pytest.raises(ValueError):
                adapter.forward_otel(invalid)
        assert len(seen) == 1
        for code in (302, 503):
            status[0] = code
            with pytest.raises(RuntimeError, match='did not accept'):
                adapter.forward_otel(b'\x0a\x00')
        assert len(seen) == 3  # One attempt per batch, with no redirect or retry.
        assert all(item[0] == '/api/public/otel/v1/traces' for item in seen)
        monkeypatch.delenv('LANGFUSE_SECRET_KEY')
        with pytest.raises(ValueError, match='unavailable'):
            adapter.forward_otel(b'\x0a\x00')
        assert len(seen) == 3
    finally:
        server.shutdown()
        thread.join()
        server.server_close()


@pytest.mark.parametrize('phase', ['body', 'headers'])
def test_native_export_deadline_closes_a_continuously_dripping_response(monkeypatch, phase):
    stopped = Event()
    disconnected = Event()

    class SlowTarget(BaseHTTPRequestHandler):
        def log_message(self, *args):
            pass

        def do_POST(self):
            self.rfile.read(int(self.headers['Content-Length']))
            if phase == 'body':
                self.send_response(200)
                self.send_header('Content-Length', '60')
                self.end_headers()
            else:
                self.wfile.write(b'HTTP/1.0 200 OK\r\nX-Slow: ')
            try:
                for _ in range(60):
                    self.wfile.write(b'x')
                    self.wfile.flush()
                    if stopped.wait(0.2):
                        break
                if phase == 'headers':
                    self.wfile.write(b'\r\nContent-Length: 0\r\n\r\n')
            except (BrokenPipeError, ConnectionResetError):
                disconnected.set()

    server = ThreadingHTTPServer(('127.0.0.1', 0), SlowTarget)
    thread = Thread(target=server.serve_forever, daemon=True)
    thread.start()
    monkeypatch.setenv('LANGFUSE_BASE_URL', f'http://127.0.0.1:{server.server_port}')
    monkeypatch.setenv('LANGFUSE_PUBLIC_KEY', 'pk-fixture')
    monkeypatch.setenv('LANGFUSE_SECRET_KEY', 'sk-fixture')
    adapter = CompanionAdapter(SimpleNamespace(target=SimpleNamespace(base_url='')))
    try:
        started = monotonic()
        with pytest.raises(requests.Timeout):
            adapter.forward_otel(b'\x0a\x00')
        assert monotonic() - started < 11.5
        assert disconnected.wait(1), 'the timed-out request must close its connection'
    finally:
        stopped.set()
        server.shutdown()
        thread.join()
        server.server_close()


def test_native_export_preserves_host_egress_proxy_and_response_byte_limit(monkeypatch):
    paths = []

    class Proxy(BaseHTTPRequestHandler):
        def log_message(self, *args):
            pass

        def do_POST(self):
            self.rfile.read(int(self.headers['Content-Length']))
            paths.append(self.path)
            self.send_response(200)
            self.send_header('Content-Length', str(1024 * 1024 + 1))
            self.end_headers()
            self.wfile.write(b'x' * (1024 * 1024 + 1))

    server = ThreadingHTTPServer(('127.0.0.1', 0), Proxy)
    thread = Thread(target=server.serve_forever, daemon=True)
    thread.start()
    monkeypatch.setenv('LANGFUSE_BASE_URL', 'http://target.invalid')
    monkeypatch.setenv('LANGFUSE_PUBLIC_KEY', 'pk-fixture')
    monkeypatch.setenv('LANGFUSE_SECRET_KEY', 'sk-fixture')
    monkeypatch.setenv('http_proxy', f'http://127.0.0.1:{server.server_port}')
    monkeypatch.setenv('no_proxy', '')
    monkeypatch.delenv('NO_PROXY', raising=False)
    adapter = CompanionAdapter(SimpleNamespace(target=SimpleNamespace(base_url='')))
    try:
        with pytest.raises(ValueError, match='response exceeds 1 MiB'):
            adapter.forward_otel(b'\x0a\x00')
        assert paths == ['http://target.invalid/api/public/otel/v1/traces']
    finally:
        server.shutdown()
        thread.join()
        server.server_close()
