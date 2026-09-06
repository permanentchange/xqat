from __future__ import annotations

import importlib

import pytest


def _security():
    try:
        return importlib.import_module("xqatexp.security")
    except ModuleNotFoundError:
        pytest.fail("xqatexp.security is not implemented", pytrace=False)


def test_secret_value_never_reveals_itself_in_string_or_repr() -> None:
    """Catches accidental credential disclosure by diagnostics or dataclass repr."""
    secret = _security().SecretValue("abcdefgh12345678")
    assert str(secret) == "<redacted>"
    assert repr(secret) == "SecretValue(<redacted>)"
    assert secret.reveal() == "abcdefgh12345678"


def test_missing_tushare_token_has_stable_issue_code() -> None:
    """Catches a missing token reported as an internal or provider failure."""
    with pytest.raises(Exception, match="SECURITY_SECRET_MISSING"):
        _security().load_tushare_token({})


def test_redaction_handles_sensitive_keys_and_secret_fragments() -> None:
    """Catches nested values or partial tokens leaking through generic context."""
    secret = _security().SecretValue("abcdefgh12345678")
    value = {
        "normal": "safe",
        "Authorization": "Bearer anything",
        "nested": ["prefix-abcdefgh-suffix", {"token": "different"}],
    }
    assert _security().redact(value, (secret,)) == {
        "normal": "safe",
        "Authorization": "<redacted>",
        "nested": ["<redacted>", {"token": "<redacted>"}],
    }


def test_secret_scan_blocks_material_before_publication() -> None:
    """Catches an artifact containing a full or eight-character token fragment."""
    secret = _security().SecretValue("abcdefgh12345678")
    with pytest.raises(Exception, match="SECURITY_SECRET_EXPOSURE_BLOCKED"):
        _security().assert_no_secret("error contains 12345678", (secret,))
