"""Configuration management module."""

import os
import json
from typing import Any, Dict, Optional


class ConfigKeyError(ValueError):
    """Raised when a config key is invalid (empty segments, etc.)."""
    pass


class Config:
    def __init__(self, config_path: Optional[str] = None):
        self._data: Dict[str, Any] = {}
        if config_path:
            self.load(config_path)
        self._load_env_overrides()

    def load(self, path: str) -> None:
        with open(path) as f:
            self._data = json.load(f)

    def _load_env_overrides(self) -> None:
        prefix = "AO_"
        for key, value in os.environ.items():
            if key.startswith(prefix):
                config_key = key[len(prefix):].lower().replace("_", ".")
                self._set_nested(config_key, value)

    def _validate_key(self, key: str) -> None:
        """Reject keys with empty dot-separated segments."""
        if not key:
            raise ConfigKeyError("Config key must not be empty")
        parts = key.split(".")
        for i, part in enumerate(parts):
            if part == "":
                raise ConfigKeyError(
                    f"Config key '{key}' contains an empty segment at position {i}"
                )

    def _set_nested(self, key: str, value: Any) -> None:
        self._validate_key(key)
        parts = key.split(".")
        current = self._data
        for part in parts[:-1]:
            if part not in current:
                current[part] = {}
            current = current[part]
        current[parts[-1]] = value

    def get(self, key: str, default: Any = None) -> Any:
        self._validate_key(key)
        parts = key.split(".")
        current = self._data
        for part in parts:
            if isinstance(current, dict):
                current = current.get(part)
                if current is None:
                    return default
            else:
                return default
        return current

    def set(self, key: str, value: Any) -> None:
        self._set_nested(key, value)

    def to_dict(self) -> Dict:
        return self._data
