"""Foreign runtime OTLP transport stays bound to the Adapter's own target."""
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from threading import Thread
from types import SimpleNamespace
import pytest
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
