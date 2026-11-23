import pytest

from cosmos_cloud_mcp.redact import REDACTED, redact
from cosmos_cloud_mcp.safety import Level, classify, normalize_path


@pytest.mark.parametrize(
    "method, path, level",
    [
        ("GET", "/api/servapps", Level.READ),
        ("GET", "/api/cron", Level.READ),
        ("POST", "/api/jobs/get", Level.READ),
        ("GET", "/api/servapps/web/manage/restart", Level.WRITE),
        ("GET", "/api/servapps/web/auto-update/true", Level.WRITE),
        ("GET", "/api/images/pull", Level.WRITE),
        ("POST", "/api/routes", Level.WRITE),
        ("PUT", "/api/routes/web", Level.WRITE),
        ("GET", "/api/servapps/web/manage/remove", Level.DESTRUCTIVE),
        ("GET", "/api/servapps/web/manage/kill", Level.DESTRUCTIVE),
        ("GET", "/api/restart", Level.DESTRUCTIVE),
        ("GET", "/api/restart-server", Level.DESTRUCTIVE),
        ("GET", "/api/constellation/reset", Level.DESTRUCTIVE),
        ("DELETE", "/api/routes/web", Level.DESTRUCTIVE),
        ("POST", "/api/backups/b1/restore", Level.DESTRUCTIVE),
        ("POST", "/api/api-tokens", Level.DESTRUCTIVE),
        ("POST", "/api/users", Level.DESTRUCTIVE),
        ("GET", "/api/api-tokens", Level.READ),
        ("PUT", "/api/config", Level.DESTRUCTIVE),
        ("GET", "/api/config", Level.READ),
        ("GET", "/api/notifications/read", Level.WRITE),
        ("GET", "/api/mfa", Level.WRITE),
        ("POST", "/api/migrate-host", Level.DESTRUCTIVE),
        ("POST", "/api/register", Level.DESTRUCTIVE),
        ("POST", "/api/constellation/registries/r1/tokens", Level.DESTRUCTIVE),
    ],
)
def test_classify(method, path, level):
    assert classify(method, path) is level


def test_classify_rejects_unknown_method():
    with pytest.raises(ValueError):
        classify("TRACE", "/api/status")


@pytest.mark.parametrize(
    "raw, expected",
    [("/api/status", "/api/status"), ("api/status", "/api/status"), ("/cosmos/api/status/", "/api/status")],
)
def test_normalize_path(raw, expected):
    assert normalize_path(raw) == expected


@pytest.mark.parametrize(
    "raw",
    ["https://evil.test/api/status", "//evil.test/api", "/api/../admin", "/api/%2e%2e/x", "/other", "/api/status?x=1",
     "/api/servapps/web%2Fmanage%2Fremove", "/api/servapps/web%5cmanage"],
)
def test_normalize_path_rejects(raw):
    with pytest.raises(ValueError):
        normalize_path(raw)


def test_redact_nested_secrets_and_env():
    data = {
        "Config": {"Env": ["DB_PASSWORD=hunter2", "TZ=Europe/Zurich"], "Image": "app"},
        "HTTPConfig": {"AuthPrivateKey": "-----BEGIN", "AuthPublicKey": "pub", "Hostname": "h"},
        "EmailConfig": {"Password": "x", "Enabled": True},
        "Backups": {"b1": {"Password": "restic", "Source": "/data"}},
        "tokens": {"mytoken": {"tokenSuffix": "abcd", "permissions": [1]}},
        "envVars": {"KEY": "value"},
    }
    out = redact(data)
    assert out["Config"]["Env"] == [f"DB_PASSWORD={REDACTED}", f"TZ={REDACTED}"]
    assert out["Config"]["Image"] == "app"
    assert out["HTTPConfig"]["AuthPrivateKey"] == REDACTED
    assert out["HTTPConfig"]["AuthPublicKey"] == "pub"
    assert out["EmailConfig"] == {"Password": REDACTED, "Enabled": True}
    assert out["Backups"]["b1"] == {"Password": REDACTED, "Source": "/data"}
    assert out["tokens"]["mytoken"]["tokenSuffix"] == "abcd"
    assert out["envVars"] == {"KEY": REDACTED}
    assert data["EmailConfig"]["Password"] == "x"  # input untouched


def test_redact_tls_keys_and_secret_records():
    data = {
        "HTTPConfig": {"tlskey": "-----BEGIN", "selfTLSKey": "k", "OIDCSigningKey": "s", "tlscert": "cert",
                       "ZoneCerts": {"z": {"tlskey": "k2", "tlscert": "c2"}}},
        "secrets": [{"name": "API", "value": "v", "availableToPRs": False}],
        "env": [{"name": "DB_PASS", "value": "x"}],
    }
    out = redact(data)
    assert out["HTTPConfig"]["tlskey"] == REDACTED
    assert out["HTTPConfig"]["selfTLSKey"] == REDACTED
    assert out["HTTPConfig"]["OIDCSigningKey"] == REDACTED
    assert out["HTTPConfig"]["tlscert"] == "cert"
    assert out["HTTPConfig"]["ZoneCerts"]["z"] == {"tlskey": REDACTED, "tlscert": "c2"}
    assert out["secrets"] == [{"name": "API", "value": REDACTED, "availableToPRs": False}]
    assert out["env"] == [{"name": "DB_PASS", "value": REDACTED}]
