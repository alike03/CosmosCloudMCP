import pytest

from cosmos_cloud_mcp.config import ConfigError, Settings, load_env_file


def test_from_env_defaults():
    s = Settings.from_env({"COSMOS_URL": "https://c.test/", "COSMOS_TOKEN": "cosmos_abc"})
    assert s.api_base == "https://c.test/cosmos"
    assert s.read_only is False
    assert s.verify_tls is True
    assert s.redact_secrets is True


def test_url_with_cosmos_suffix_is_not_doubled():
    s = Settings.from_env({"COSMOS_URL": "https://c.test/cosmos", "COSMOS_TOKEN": "cosmos_abc"})
    assert s.api_base == "https://c.test/cosmos"


@pytest.mark.parametrize(
    "env, message",
    [
        ({"COSMOS_TOKEN": "cosmos_abc"}, "COSMOS_URL"),
        ({"COSMOS_URL": "c.test", "COSMOS_TOKEN": "cosmos_abc"}, "http"),
        ({"COSMOS_URL": "https://c.test"}, "COSMOS_TOKEN is not set"),
        ({"COSMOS_URL": "https://c.test", "COSMOS_TOKEN": "abc"}, "prefix"),
        ({"COSMOS_URL": "https://c.test", "COSMOS_TOKEN": "cosmos_a", "COSMOS_READ_ONLY": "maybe"}, "true or false"),
    ],
)
def test_from_env_errors(env, message):
    with pytest.raises(ConfigError, match=message):
        Settings.from_env(env)


def test_bool_parsing():
    s = Settings.from_env(
        {"COSMOS_URL": "https://c.test", "COSMOS_TOKEN": "cosmos_a", "COSMOS_READ_ONLY": "YES", "COSMOS_VERIFY_TLS": "0"}
    )
    assert s.read_only is True
    assert s.verify_tls is False


def test_repr_hides_token():
    s = Settings(url="https://c.test", token="cosmos_supersecret")
    assert "supersecret" not in repr(s)


def test_env_file(tmp_path):
    f = tmp_path / ".env"
    f.write_text(
        "# comment\nexport COSMOS_URL='https://file.test'\nCOSMOS_TOKEN=\"cosmos_fromfile\"\nCOSMOS_READ_ONLY=true\n"
    )
    s = Settings.from_env({"COSMOS_ENV_FILE": str(f), "COSMOS_READ_ONLY": "false"})
    assert s.url == "https://file.test"
    assert s.token == "cosmos_fromfile"
    assert s.read_only is False  # explicit environment wins over the file


def test_env_file_missing(tmp_path):
    with pytest.raises(ConfigError, match="does not exist"):
        load_env_file(tmp_path / "nope", {})
