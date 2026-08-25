from __future__ import annotations

SERVICE = "EPLValueBetting"
USERNAME = "PulseScoreAPIKey"

try:
    import keyring  # type: ignore
except Exception:
    keyring = None


def get_api_key() -> str:
    if keyring is None:
        return ""
    try:
        return keyring.get_password(SERVICE, USERNAME) or ""
    except Exception:
        return ""


def save_api_key(value: str) -> bool:
    if keyring is None:
        return False
    try:
        if value:
            keyring.set_password(SERVICE, USERNAME, value)
        else:
            try:
                keyring.delete_password(SERVICE, USERNAME)
            except Exception:
                pass
        return True
    except Exception:
        return False


def clear_api_key() -> bool:
    return save_api_key("")
