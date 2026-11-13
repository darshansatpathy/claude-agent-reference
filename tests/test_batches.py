from __future__ import annotations

import json

import pytest

from claude_agent.batches import BatchClient, BatchInfo, BatchRequest, batch_cost
from claude_agent.cost import ModelPrice, PriceTable
from claude_agent.errors import BatchError, ConfigError
from claude_agent.transport import HttpResponse
from tests.conftest import message, ok

RES_URL = "https://api.anthropic.com/v1/messages/batches/msgbatch_1/results"


def info(status="in_progress", url=None):
    return {
        "id": "msgbatch_1",
        "type": "message_batch",
        "processing_status": status,
        "request_counts": {
            "processing": 1,
            "succeeded": 0,
            "errored": 0,
            "canceled": 0,
            "expired": 0,
        },
        "results_url": url,
    }


def reqs(*ids):
    return [BatchRequest(i, {"model": "m", "max_tokens": 5, "messages": []}) for i in ids]


def test_create_posts_requests(make_client):
    c, t = make_client(ok(info()))
    out = BatchClient(c).create(reqs("a", "b"))
    assert out.id == "msgbatch_1" and not out.ended
    assert t.calls[0]["url"].endswith("/v1/messages/batches")
    assert [r["custom_id"] for r in json.loads(t.calls[0]["body"])["requests"]] == ["a", "b"]


@pytest.mark.parametrize("ids", [[], ["a", "a"], ["bad id"], ["x" * 65], ["a/b"]])
def test_create_validates(make_client, ids):
    c, t = make_client()
    with pytest.raises(ConfigError):
        BatchClient(c).create(reqs(*ids))
    assert t.calls == []


def test_batch_id_cannot_inject_path_segments(make_client):
    c, _ = make_client()
    with pytest.raises(ConfigError):
        BatchClient(c).get("../../v1/organizations")


def test_wait_polls_until_ended(make_client, sleeps):
    c, t = make_client(ok(info()), ok(info()), ok(info("ended", RES_URL)))
    done = BatchClient(c, sleep=sleeps.append).wait("msgbatch_1", poll_s=5)
    assert done.ended and sleeps == [5, 5] and len(t.calls) == 3


def test_wait_times_out(make_client):
    now = [0.0]
    c, _ = make_client(*[ok(info()) for _ in range(5)])
    b = BatchClient(c, sleep=lambda s: now.__setitem__(0, now[0] + s), clock=lambda: now[0])
    with pytest.raises(BatchError):
        b.wait("msgbatch_1", poll_s=10, timeout_s=25)


def test_results_parse_every_result_type(make_client):
    lines = [
        {
            "custom_id": "a",
            "result": {"type": "succeeded", "message": message("one", usage=(100, 50))},
        },
        {
            "custom_id": "b",
            "result": {
                "type": "errored",
                "error": {"type": "error", "error": {"type": "invalid_request_error"}},
            },
        },
        {"custom_id": "c", "result": {"type": "canceled"}},
        {"custom_id": "d", "result": {"type": "expired"}},
    ]
    body = ("\n".join(json.dumps(x) for x in lines) + "\n\n").encode()
    c, t = make_client(HttpResponse(200, body, {}))
    got = BatchClient(c).results(BatchInfo("msgbatch_1", "ended", {}, RES_URL))
    by_id = {r.custom_id: r for r in got}
    assert by_id["a"].message is not None and by_id["a"].message.text == "one"
    assert (
        by_id["b"].error is not None
        and by_id["c"].type == "canceled"
        and by_id["d"].type == "expired"
    )
    assert t.calls[0]["url"] == RES_URL
    prices = PriceTable({"m": ModelPrice(1.0, 5.0)}, batch_discount=0.5)
    assert batch_cost(got, prices, "m") == pytest.approx((100e-6 + 50 * 5e-6) * 0.5)


def test_results_before_the_batch_ends_are_refused(make_client):
    c, _ = make_client()
    with pytest.raises(BatchError):
        BatchClient(c).results(BatchInfo("msgbatch_1", "in_progress"))
    with pytest.raises(BatchError):
        BatchClient(c).results(BatchInfo("msgbatch_1", "ended", {}, None))


def test_cancel(make_client):
    c, t = make_client(ok(info("canceling")))
    assert BatchClient(c).cancel("msgbatch_1").processing_status == "canceling"
    assert t.calls[0]["url"].endswith("/msgbatch_1/cancel") and t.calls[0]["method"] == "POST"
