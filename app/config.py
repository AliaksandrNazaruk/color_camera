import json
import logging
import os
from pathlib import Path
from typing import Any, Dict, List


logger = logging.getLogger("config")


def _parse_bool_env(value: str, default: bool = False) -> bool:
    if value is None:
        return default
    return value.lower() in ("1", "true", "yes", "on")


def _load_file_ice_config() -> Dict[str, Any]:
    """Attempt to load ICE configuration from JSON file."""
    search_paths = []

    env_path = os.getenv("ICE_CONFIG_PATH")
    if env_path:
        candidate = Path(env_path)
        if candidate.is_file():
            search_paths.append(candidate)
        else:
            logger.warning("ICE_CONFIG_PATH %s is not a file, falling back to defaults", candidate)

    repo_default = Path(__file__).resolve().parent.parent / "ice_config.json"
    if repo_default.is_file():
        search_paths.append(repo_default)

    for path in search_paths:
        try:
            with path.open("r", encoding="utf-8") as fp:
                data = json.load(fp)
                if isinstance(data, dict):
                    logger.info("Loaded ICE config from %s", path)
                    return data
                logger.warning("ICE config file %s does not contain a JSON object", path)
        except json.JSONDecodeError as exc:
            logger.error("Failed to parse ICE config %s: %s", path, exc)
        except OSError as exc:
            logger.error("Failed to read ICE config %s: %s", path, exc)

    return {}


def get_initial_ice_config() -> Dict[str, Any]:
    """Build initial ICE/TURN config.

    Priority order:
      1. JSON file specified in ``ICE_CONFIG_PATH`` (if valid).
      2. Repository ``ice_config.json`` fallback.
      3. Environment variables ``USE_TURN``, ``TURN_URLS`` etc.
      4. Built-in defaults.
    """

    config: Dict[str, Any] = {
        "use_turn": False,
        "urls": ["stun:stun.l.google.com:19302"],
        "username": None,
        "credential": None,
        "relay_only": False,
    }

    file_config = _load_file_ice_config()
    if file_config:
        config.update({k: v for k, v in file_config.items() if v is not None})

    urls_raw = os.getenv("TURN_URLS")
    if urls_raw:
        urls: List[str] = [u.strip() for u in urls_raw.split(",") if u.strip()]
        if urls:
            config["urls"] = urls

    if "USE_TURN" in os.environ:
        config["use_turn"] = _parse_bool_env(os.getenv("USE_TURN"), default=config["use_turn"])

    if "TURN_USERNAME" in os.environ:
        config["username"] = os.getenv("TURN_USERNAME") or None

    if "TURN_CREDENTIAL" in os.environ:
        config["credential"] = os.getenv("TURN_CREDENTIAL") or None

    if "ICE_RELAY_ONLY" in os.environ:
        config["relay_only"] = _parse_bool_env(os.getenv("ICE_RELAY_ONLY"), default=config["relay_only"])

    return config


