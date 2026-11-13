from __future__ import annotations

import json

import pytest

from claude_agent.audit import AuditLog, Redactor, fingerprint, verify_chain


@pytest.mark.parametrize(
    ("raw", "kind"),
    [
        ("key sk-ant-api03-ABCDEFGH12345678", "anthropic_key"),
        ("key sk-abcdefghijklmnopqrstuvwx", "api_key"),
        ("AKIAABCDEFGHIJKLMNOP", "aws_access_key"),
        ("ghp_" + "a" * 36, "github_token"),
        ("Authorization: Bearer abcdefghijklmnop1234", "bearer"),
        ("eyJhbGciOiJIUzI1.eyJzdWIiOiIxMjM0.SflKxwRJSMeKKF2QT4", "jwt"),
        ("write to jane.doe+x@example.co.uk", "email"),
    ],
)
def test_redacts_known_secret_shapes(raw, kind):
    out = Redactor().text(raw)
    assert f"[REDACTED:{kind}]" in out


def test_card_numbers_need_a_valid_luhn_checksum():
    r = Redactor()
    assert "[REDACTED:card]" in r.text("card 4111 1111 1111 1111 thanks")
    assert "[REDACTED:card]" not in r.text("order 1234 5678 9012 3456")
    assert r.text("short 12345") == "short 12345"


def test_redacts_inside_structures_and_sensitive_keys():
    out = Redactor().any(
        {"a": ["x@y.com"], "password": "hunter2", "n": 3, "nested": {"api_key": "zzz"}}
    )
    assert out["a"] == ["[REDACTED:email]"]
    assert (
        out["password"] == "[REDACTED:secret_field]"
        and out["nested"]["api_key"] == "[REDACTED:secret_field]"
    )
    assert out["n"] == 3


def test_custom_patterns():
    assert Redactor({"ticket": r"TKT-\d+"}).text("see TKT-42") == "see [REDACTED:ticket]"


def test_fingerprint_is_stable_and_short():
    assert fingerprint({"b": 1, "a": 2}) == fingerprint({"a": 2, "b": 1})
    assert len(fingerprint("x")) == 16


def test_content_is_fingerprinted_by_default_and_included_on_request():
    assert "content" not in AuditLog().content("secret text")
    assert AuditLog(log_content=True).content("hi")["content"] == "hi"


def test_chain_verifies_and_detects_tampering(tmp_path):
    path = tmp_path / "audit.jsonl"
    log = AuditLog(path)
    for i in range(4):
        log.emit("step", i=i)
    assert verify_chain(path) == (True, None)

    lines = path.read_text().splitlines()
    forged = json.loads(lines[1])
    forged["i"] = 99
    tampered = [*lines[:1], json.dumps(forged, sort_keys=True), *lines[2:]]
    path.write_text("\n".join(tampered) + "\n")
    assert verify_chain(path) == (False, 1)


def test_removed_or_reordered_lines_are_detected(tmp_path):
    path = tmp_path / "a.jsonl"
    log = AuditLog(path)
    for i in range(3):
        log.emit("e", i=i)
    lines = path.read_text().splitlines()
    path.write_text("\n".join([lines[0], lines[2]]) + "\n")
    assert verify_chain(path)[0] is False
    path.write_text("\n".join([lines[1], lines[0], lines[2]]) + "\n")
    assert verify_chain(path)[0] is False
    path.write_text("not json\n")
    assert verify_chain(path) == (False, 0)


def test_log_resumes_the_chain_across_processes(tmp_path):
    path = tmp_path / "a.jsonl"
    AuditLog(path).emit("one")
    AuditLog(path).emit("two")
    assert verify_chain(path) == (True, None)
    assert [json.loads(line)["seq"] for line in path.read_text().splitlines()] == [0, 1]


def test_emit_redacts_before_hashing_and_writing(tmp_path):
    path = tmp_path / "a.jsonl"
    AuditLog(path).emit("e", note="mail me at a@b.com")
    text = path.read_text()
    assert "a@b.com" not in text and "[REDACTED:email]" in text
    assert verify_chain(path)[0]


def test_in_memory_log_keeps_events():
    log = AuditLog()
    log.emit("x", a=1)
    assert log.events[0]["event"] == "x" and log.events[0]["run_id"] == log.run_id
