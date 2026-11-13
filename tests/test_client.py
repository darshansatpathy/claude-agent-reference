from __future__ import annotations

import json

import pytest

from claude_agent.client import ClientConfig, MessagesClient
from claude_agent.errors import ApiError, ConfigError, TransportError
from claude_agent.transport import HttpResponse, UrllibTransport, validate_base_url
from tests.conftest import KEY, FakeTransport, err, message, ok


def test_create_sends_required_headers_and_parses(make_client):
    c, t = make_client(ok(message("hello"), request_id="req_9"))
    msg = c.create({"model": "m", "max_tokens": 5, "messages": []})
    call = t.calls[0]
    assert call["url"] == "https://api.anthropic.com/v1/messages"
    assert call["headers"]["x-api-key"] == KEY
    assert call["headers"]["anthropic-version"] == "2023-06-01"
    assert call["headers"]["content-type"] == "application/json"
    assert json.loads(call["body"])["max_tokens"] == 5
    assert msg.text == "hello" and msg.request_id == "req_9"
    assert msg.usage.total_tokens == 15


def test_api_key_is_not_in_repr_or_error_messages(make_client):
    cfg = ClientConfig(api_key=KEY)
    assert KEY not in repr(cfg)
    c, _ = make_client(err(401, "authentication_error", "invalid x-api-key"))
    with pytest.raises(ApiError) as exc:
        c.create({})
    assert KEY not in str(exc.value)


def test_retries_transient_errors_with_exponential_backoff(make_client, sleeps):
    c, t = make_client(err(529, "overloaded_error"), err(500), ok(message()))
    c.create({})
    assert len(t.calls) == 3 and c.retries == 2
    assert sleeps == [0.5, 1.0]  # rand() == 1.0 gives the full ceiling


def test_backoff_is_capped(make_client, sleeps):
    c, _ = make_client(*([err(529)] * 5), ok(message()), max_retries=5, backoff_cap_s=2.0)
    c.create({})
    assert max(sleeps) == 2.0


def test_full_jitter_uses_the_random_source(sleeps):
    t = FakeTransport(err(529), ok(message()))
    c = MessagesClient(ClientConfig(api_key=KEY), t, sleep=sleeps.append, rand=lambda: 0.5)
    c.create({})
    assert sleeps == [0.25]


def test_retry_after_is_honoured(make_client, sleeps):
    c, _ = make_client(err(429, "rate_limit_error", retry_after="7"), ok(message()))
    c.create({})
    assert sleeps == [7.0]


def test_retry_after_longer_than_cap_is_surfaced(make_client, sleeps):
    c, t = make_client(err(429, "rate_limit_error", retry_after="600"), max_retry_after_s=60)
    with pytest.raises(ApiError) as exc:
        c.create({})
    assert exc.value.status == 429 and exc.value.retry_after == 600 and sleeps == []
    assert len(t.calls) == 1


@pytest.mark.parametrize("status", [400, 401, 403, 404, 413])
def test_client_errors_are_not_retried(make_client, status):
    c, t = make_client(err(status, "invalid_request_error"))
    with pytest.raises(ApiError) as exc:
        c.create({})
    assert exc.value.status == status and len(t.calls) == 1 and not exc.value.retryable


def test_retries_are_bounded(make_client):
    c, t = make_client(*([err(529)] * 4), max_retries=3)
    with pytest.raises(ApiError):
        c.create({})
    assert len(t.calls) == 4


def test_zero_retries_means_one_attempt(make_client):
    c, t = make_client(err(500), max_retries=0)
    with pytest.raises(ApiError):
        c.create({})
    assert len(t.calls) == 1


def test_transport_errors_are_retried_then_raised(make_client):
    c, t = make_client(TransportError("reset"), TransportError("reset"), max_retries=1)
    with pytest.raises(TransportError):
        c.create({})
    assert len(t.calls) == 2


def test_error_body_fields_are_extracted(make_client):
    body = {
        "type": "error",
        "error": {"type": "rate_limit_error", "message": "slow down"},
        "request_id": "req_abc",
    }
    c, _ = make_client(HttpResponse(400, json.dumps(body).encode(), {}))
    with pytest.raises(ApiError) as exc:
        c.create({})
    assert (exc.value.error_type, exc.value.message, exc.value.request_id) == (
        "rate_limit_error",
        "slow down",
        "req_abc",
    )


def test_non_json_error_body_is_handled(make_client):
    c, _ = make_client(
        HttpResponse(502, b"<html>bad gateway</html>", {"request-id": "req_x"}), max_retries=0
    )
    with pytest.raises(ApiError) as exc:
        c.create({})
    assert exc.value.status == 502 and exc.value.request_id == "req_x"


@pytest.mark.parametrize("body", [b"not json", b"[1, 2]"])
def test_success_with_bad_body_raises(make_client, body):
    c, _ = make_client(HttpResponse(200, body, {}))
    with pytest.raises(ApiError) as exc:
        c.create({})
    assert exc.value.error_type == "invalid_response"


def test_credentials_are_never_sent_to_another_origin(make_client):
    c, t = make_client()
    with pytest.raises(ConfigError):
        c.request("GET", "https://evil.example.com/steal")
    with pytest.raises(ConfigError):
        c.request("GET", "https://api.anthropic.com.evil.example.com/x")
    assert t.calls == []


@pytest.mark.parametrize("key", ["", "   ", "abc\ndef"])
def test_bad_keys_are_rejected(key):
    with pytest.raises(ConfigError):
        ClientConfig(api_key=key)


@pytest.mark.parametrize(
    "url", ["http://api.example.com", "ftp://x", "file:///etc/passwd", "https://"]
)
def test_non_https_base_urls_are_rejected(url):
    with pytest.raises(ConfigError):
        ClientConfig(api_key=KEY, base_url=url)


def test_loopback_http_is_allowed_for_local_servers():
    assert validate_base_url("http://127.0.0.1:8080/") == "http://127.0.0.1:8080"
    assert ClientConfig(api_key=KEY, base_url="http://localhost:9000").base_url.startswith(
        "http://localhost"
    )


def test_invalid_numeric_config_is_rejected():
    with pytest.raises(ConfigError):
        ClientConfig(api_key=KEY, max_retries=-1)
    with pytest.raises(ConfigError):
        ClientConfig(api_key=KEY, timeout_s=0)


def test_from_env():
    cfg = ClientConfig.from_env(
        {"ANTHROPIC_API_KEY": KEY, "ANTHROPIC_BASE_URL": "https://gateway.example.com"}
    )
    assert cfg.base_url == "https://gateway.example.com"
    with pytest.raises(ConfigError):
        ClientConfig.from_env({})


def test_unparseable_retry_after_is_ignored(make_client, sleeps):
    c, _ = make_client(err(529, retry_after="Wed, 21 Oct 2026 07:28:00 GMT"), ok(message()))
    c.create({})
    assert sleeps == [0.5]


def test_urllib_transport_refuses_other_schemes():
    with pytest.raises(ConfigError):
        UrllibTransport().send("GET", "file:///etc/passwd", {}, None, 1.0)
