import asyncio

import pytest

from app.scripts.runtime_rate_limit_probe import (
    PROBE_REDIS_LEASE_KEY,
    ProbeFailure,
    ProbeSummary,
    _release_probe_redis,
    build_probe_redis_url,
    validate_tag,
    validate_rate_limited_response,
)


def test_probe_redis_url_uses_isolated_database_without_changing_authority():
    original = "rediss://probe-user:secret@example.test:6380/0?ssl_cert_reqs=required"
    result = build_probe_redis_url(original, 15)
    assert result == (
        "rediss://probe-user:secret@example.test:6380/15?ssl_cert_reqs=required"
    )


@pytest.mark.parametrize(
    ("url", "database", "code"),
    (
        ("http://example.test/0", 15, "RATE_LIMIT_PROBE_REDIS_URL_INVALID"),
        ("redis:///0", 15, "RATE_LIMIT_PROBE_REDIS_URL_INVALID"),
        ("redis://example.test/0", 0, "RATE_LIMIT_PROBE_REDIS_DATABASE_INVALID"),
        ("redis://example.test/0", 16, "RATE_LIMIT_PROBE_REDIS_DATABASE_INVALID"),
    ),
)
def test_probe_redis_url_rejects_unsafe_targets(url, database, code):
    with pytest.raises(ProbeFailure) as caught:
        build_probe_redis_url(url, database)
    assert caught.value.code == code


def test_rate_limited_response_requires_stable_envelope_and_retry_after():
    assert (
        validate_rate_limited_response(
            status_code=429,
            body={"code": "RATE_LIMITED", "request_id": "probe-request"},
            headers={"X-Request-Id": "probe-request", "Retry-After": "37"},
            request_id="probe-request",
        )
        == 37
    )
    with pytest.raises(ProbeFailure) as caught:
        validate_rate_limited_response(
            status_code=429,
            body={"code": "RATE_LIMITED", "request_id": "probe-request"},
            headers={"X-Request-Id": "probe-request", "Retry-After": "0"},
            request_id="probe-request",
        )
    assert caught.value.code == "RATE_LIMIT_PROBE_RETRY_AFTER_INVALID"


@pytest.mark.parametrize(
    "tag",
    ("contains_%", "contains_under_score", "../escape", "short", "A-uppercase"),
)
def test_probe_tag_rejects_sql_wildcards_and_non_canonical_values(tag):
    with pytest.raises(ProbeFailure) as caught:
        validate_tag(tag)
    assert caught.value.code == "RATE_LIMIT_PROBE_TAG_INVALID"


def test_probe_tag_accepts_generated_shape():
    assert validate_tag("20260717123000-0123456789") == "20260717123000-0123456789"


def test_probe_redis_release_compares_owner_and_deletes_exact_keys_atomically():
    class RedisStub:
        def __init__(self):
            self.eval_call = None

        async def eval(self, script, number_of_keys, *values):
            self.eval_call = (script, number_of_keys, values)
            return 1

        async def mget(self, keys):
            assert keys == ("rate-key-a", "rate-key-b")
            return (None, None)

    redis = RedisStub()
    assert asyncio.run(
        _release_probe_redis(
            redis,
            owner_value="probe-owner",
            keys=("rate-key-a", "rate-key-b"),
        )
    )
    script, number_of_keys, values = redis.eval_call
    assert "redis.call('GET', KEYS[1]) == ARGV[1]" in script
    assert number_of_keys == 3
    assert values == (
        PROBE_REDIS_LEASE_KEY,
        "rate-key-a",
        "rate-key-b",
        "probe-owner",
    )


def test_public_summary_contains_only_boolean_and_count_evidence():
    summary = ProbeSummary(
        agent_success_count=22,
        internal_tool_success_count=62,
        user_limit_verified=True,
        ip_limit_verified=True,
        window_recovery_verified=True,
        atomic_isolation_verified=True,
        proxy_header_verified=True,
        database_cleanup_verified=True,
        redis_cleanup_verified=True,
    ).public_dict()
    serialized = repr(summary).lower()
    assert summary["ok"] is True
    assert "token" not in serialized
    assert "secret" not in serialized
    assert "redis://" not in serialized
    assert "postgres" not in serialized
