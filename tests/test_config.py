import pytest
from src.common.config import Config, ConfigKeyError


class TestConfig:
    def test_load_config(self, tmp_path):
        config_file = tmp_path / "config.json"
        config_file.write_text('{"app": {"name": "test", "port": 8080}}')
        config = Config(str(config_file))
        assert config.get("app.name") == "test"
        assert config.get("app.port") == 8080

    def test_default_value(self):
        config = Config()
        assert config.get("nonexistent.key", "default") == "default"

    def test_set_value(self):
        config = Config()
        config.set("database.host", "localhost")
        assert config.get("database.host") == "localhost"

    def test_nested_set(self):
        config = Config()
        config.set("a.b.c.d", "value")
        assert config.get("a.b.c.d") == "value"

    def test_to_dict(self):
        config = Config()
        config.set("key1", "value1")
        config.set("key2", "value2")
        data = config.to_dict()
        assert data["key1"] == "value1"
        assert data["key2"] == "value2"


class TestConfigKeyValidation:
    """Regression tests for Issue #4994 — reject empty dotted config keys."""

    def test_get_rejects_leading_dot(self):
        config = Config()
        config.set("a.b.c", "value")
        with pytest.raises(ConfigKeyError):
            config.get(".a.b.c")

    def test_get_rejects_trailing_dot(self):
        config = Config()
        config.set("a.b.c", "value")
        with pytest.raises(ConfigKeyError):
            config.get("a.b.c.")

    def test_get_rejects_empty_segment(self):
        config = Config()
        config.set("a.b.c", "value")
        with pytest.raises(ConfigKeyError):
            config.get("a..c")

    def test_get_rejects_multiple_empty_segments(self):
        config = Config()
        config.set("a.b.c", "value")
        with pytest.raises(ConfigKeyError):
            config.get("a...c")

    def test_set_rejects_leading_dot(self):
        config = Config()
        with pytest.raises(ConfigKeyError):
            config.set(".a.b.c", "value")

    def test_set_rejects_trailing_dot(self):
        config = Config()
        with pytest.raises(ConfigKeyError):
            config.set("a.b.c.", "value")

    def test_set_rejects_empty_segment(self):
        config = Config()
        with pytest.raises(ConfigKeyError):
            config.set("a..c", "value")

    def test_set_rejects_multiple_empty_segments(self):
        config = Config()
        with pytest.raises(ConfigKeyError):
            config.set("a...c", "value")

    def test_set_rejects_empty_key(self):
        config = Config()
        with pytest.raises(ConfigKeyError):
            config.set("", "value")

    def test_normal_keys_still_work(self):
        config = Config()
        config.set("a.b.c", "value")
        assert config.get("a.b.c") == "value"

    def test_single_segment_key_still_works(self):
        config = Config()
        config.set("key", "value")
        assert config.get("key") == "value"

    def test_error_message_includes_key_name(self):
        config = Config()
        try:
            config.get("a..b")
        except ConfigKeyError as e:
            assert "a..b" in str(e)
