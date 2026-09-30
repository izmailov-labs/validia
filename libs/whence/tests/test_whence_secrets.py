"""Secrets: masked by default, revealed only on purpose, never in a dump."""

import pytest

from whence import MASK, Config, Secret, SecretError, is_sensitive, sanitize, unlock_secrets

LEAK = "hunter2-do-not-print"


def test_a_secret_will_not_render_itself() -> None:
    secret = Secret(LEAK)
    assert LEAK not in str(secret)
    assert LEAK not in repr(secret)
    assert LEAK not in f"{secret}"
    assert LEAK not in "{}".format(secret)  # noqa: UP032
    assert str(secret) == MASK


def test_reading_a_secret_needs_an_explicit_scope() -> None:
    """SmallRye's doUnlocked: turns accidental exposure into a loud error."""
    secret = Secret(LEAK)
    with pytest.raises(SecretError, match="explicit scope"):
        secret.reveal()
    with unlock_secrets():
        assert secret.reveal() == LEAK
    with pytest.raises(SecretError):
        secret.reveal()


def test_secrets_compare_and_hash_without_revealing() -> None:
    assert Secret("a") == Secret("a")
    assert Secret("a") != Secret("b")
    assert Secret("a").__eq__("a") is NotImplemented
    assert hash(Secret("a")) == hash("a")
    assert bool(Secret("")) is False


@pytest.mark.parametrize(
    "key",
    ["db.password", "API_KEY", "auth.token", "x.client_secret", "svc.credentials", "a.private_key"],
)
def test_sensitive_key_patterns(key: str) -> None:
    assert is_sensitive(key)


def test_ordinary_keys_are_not_sensitive() -> None:
    assert not is_sensitive("db.host")


def test_sanitize_masks_by_key_and_by_type() -> None:
    """Both directions matter: convict masks by type, Spring by key name."""
    assert sanitize("db.password", LEAK) == MASK
    assert sanitize("harmless", Secret(LEAK)) == MASK
    assert sanitize("db.host", "localhost") == "localhost"


def test_dump_redacts_by_default() -> None:
    config = Config.from_mapping({"db": {"password": LEAK, "host": "h"}})
    dumped = config.dump()
    assert dumped["db.password"] == MASK
    assert dumped["db.host"] == "h"
    assert LEAK not in str(dumped)


def test_dump_can_reveal_but_only_deliberately() -> None:
    config = Config.from_mapping({"db": {"password": LEAK}})
    assert config.dump(reveal=True)["db.password"] == LEAK


def test_explain_redacts_the_winner_and_the_shadowed() -> None:
    base = Config.from_mapping({"db": {"password": "old-" + LEAK}}, name="file")
    top = Config.from_mapping({"db": {"password": LEAK}}, name="env")
    explanation = top.with_fallback(base).explain("db.password")
    assert LEAK not in explanation
    assert explanation.count(MASK) >= 2


def test_no_configured_secret_reaches_any_rendering_surface() -> None:
    """One assertion covering every surface that can print."""
    config = Config.from_mapping({"a": {"token": LEAK}, "b": {"host": "h"}})
    surfaces = [
        repr(config),
        str(config.dump()),
        config.explain("a.token"),
        str(config.origins()),
    ]
    for surface in surfaces:
        assert LEAK not in surface


def test_a_bind_error_never_echoes_the_rejected_value() -> None:
    from dataclasses import dataclass

    from whence import BindError

    @dataclass(frozen=True)
    class S:
        token: int = 0

    config = Config.from_mapping({"token": LEAK})
    with pytest.raises(BindError) as caught:
        config.bind(S)
    assert LEAK not in str(caught.value)
