import os
from typing import Any, Dict, List


def _parse_bool_env(value: str, default: bool = False) -> bool:
    if value is None:
        return default
    return value.lower() in ("1", "true", "yes", "on")


def get_initial_ice_config() -> Dict[str, Any]:
    """Build initial ICE/TURN config from environment variables.

    Supported env vars:
    - USE_TURN: "1"/"true" to enable usage of TURN credentials
    - TURN_URLS: comma-separated list, e.g. "turn:turn.example.com:3478,stun:stun.l.google.com:19302"
    - TURN_USERNAME: TURN username (optional)
    - TURN_CREDENTIAL: TURN credential/password (optional)
    """
    use_turn = _parse_bool_env(os.getenv("USE_TURN", "0"), default=False)
    urls_raw = os.getenv("TURN_URLS", "stun:stun.l.google.com:19302")
    urls: List[str] = [u.strip() for u in urls_raw.split(",") if u.strip()]
    username = os.getenv("TURN_USERNAME")
    credential = os.getenv("TURN_CREDENTIAL")

    return {
        "use_turn": use_turn,
        "urls": urls,
        "username": username,
        "credential": credential,
        "relay_only": _parse_bool_env(os.getenv("ICE_RELAY_ONLY", "0"), default=False),
    }


