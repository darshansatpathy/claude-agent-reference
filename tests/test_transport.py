"""The real urllib transport, exercised against a loopback HTTP server (no external network)."""

from __future__ import annotations

import json
import threading
from collections.abc import Iterator
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

import pytest

from claude_agent.client import ClientConfig, MessagesClient
from claude_agent.errors import ApiError, TransportError
from claude_agent.transport import UrllibTransport

SEEN: list[dict[str, object]] = []
PLAN: list[tuple[int, dict[str, object], dict[str, str]]] = []


class Handler(BaseHTTPRequestHandler):
    def do_POST(self) -> None:
        length = int(self.headers.get("content-length", 0))
        SEEN.append(
            {
                "path": self.path,
                "key": self.headers.get("x-api-key"),
                "version": self.headers.get("anthropic-version"),
                "body": json.loads(self.rfile.read(length) or b"{}"),
            }
        )
        status, body, headers = PLAN.pop(0)
        payload = json.dumps(body).encode()
        self.send_response(status)
        self.send_header("content-type", "application/json")
        self.send_header("content-length", str(len(payload)))
        for k, v in headers.items():
            self.send_header(k, v)
        self.end_headers()
        self.wfile.write(payload)

    def log_message(self, *_args: object) -> None:  # keep test output quiet
        pass


@pytest.fixture
def server() -> Iterator[str]:
    SEEN.clear()
    PLAN.clear()
    httpd = ThreadingHTTPServer(("127.0.0.1", 0), Handler)
    thread = threading.Thread(target=httpd.serve_forever, daemon=True)
    thread.start()
    yield f"http://127.0.0.1:{httpd.server_address[1]}"
    httpd.shutdown()
    httpd.server_close()
    thread.join(timeout=5)


def client(base: str, **kw: object) -> MessagesClient:
    cfg = ClientConfig(api_key="test-key-not-real-123", base_url=base, backoff_base_s=0.0, **kw)  # type: ignore[arg-type]
    return MessagesClient(cfg, UrllibTransport(), sleep=lambda s: None)


OK_BODY = {
    "id": "msg_1",
    "type": "message",
    "role": "assistant",
    "model": "m",
    "content": [{"type": "text", "text": "over the wire"}],
    "stop_reason": "end_turn",
    "usage": {"input_tokens": 1, "output_tokens": 2},
}


def test_request_goes_over_real_http_with_the_right_headers(server):
    PLAN.append((200, OK_BODY, {"request-id": "req_wire"}))
    msg = client(server).create({"model": "m", "max_tokens": 1, "messages": []})
    assert msg.text == "over the wire" and msg.request_id == "req_wire"
    assert SEEN[0]["path"] == "/v1/messages"
    assert SEEN[0]["key"] == "test-key-not-real-123" and SEEN[0]["version"] == "2023-06-01"
    assert SEEN[0]["body"] == {"model": "m", "max_tokens": 1, "messages": []}


def test_http_errors_become_api_errors_and_transient_ones_retry(server):
    err = {"type": "error", "error": {"type": "overloaded_error", "message": "Overloaded"}}
    PLAN.extend(
        [
            (529, err, {}),
            (
                429,
                {"type": "error", "error": {"type": "rate_limit_error", "message": "x"}},
                {"retry-after": "0"},
            ),
            (200, OK_BODY, {}),
        ]
    )
    c = client(server)
    assert c.create({"model": "m", "max_tokens": 1, "messages": []}).text == "over the wire"
    assert c.retries == 2 and len(SEEN) == 3


def test_non_retryable_error_surfaces_request_id_from_body(server):
    body = {
        "type": "error",
        "error": {"type": "invalid_request_error", "message": "bad"},
        "request_id": "req_body",
    }
    PLAN.append((400, body, {}))
    with pytest.raises(ApiError) as exc:
        client(server).create({})
    assert exc.value.request_id == "req_body" and len(SEEN) == 1


def test_connection_refused_is_a_transport_error():
    c = client("http://127.0.0.1:9", max_retries=1)
    with pytest.raises(TransportError):
        c.create({})
    assert c.http_calls == 2
