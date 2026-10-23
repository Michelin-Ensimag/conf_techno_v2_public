"""Startup checks of the identity (keys read from the environment)."""

import pytest
from cryptography.hazmat.primitives import serialization
from cryptography.hazmat.primitives.asymmetric.ed25519 import Ed25519PrivateKey

from student_client.config import Settings
from student_client.security import StartupError, load_identity


def keypair() -> tuple[str, str]:
    key = Ed25519PrivateKey.generate()
    private = key.private_bytes(
        serialization.Encoding.PEM, serialization.PrivateFormat.PKCS8, serialization.NoEncryption()
    ).decode()
    public = key.public_key().public_bytes(
        serialization.Encoding.PEM, serialization.PublicFormat.SubjectPublicKeyInfo
    ).decode()
    return private, public


def make_settings(**overrides) -> Settings:
    team_private, team_public = keypair()
    _, admin_public = keypair()
    values = {
        "team_username": "team-a",
        "team_private_key": team_private,
        "team_public_key": team_public,
        "admin_public_key": admin_public,
        "_env_file": None,
    }
    values.update(overrides)
    return Settings(**values)


def test_valid_identity_loads():
    identity = load_identity(make_settings())

    assert identity.username == "team-a"
    envelope = identity.build_envelope({"type": "buy_truck", "content": {"model_code": "small"}})
    assert envelope["seq"] < identity.next_sequence()


def test_keys_written_on_one_line_with_escaped_newlines_are_accepted():
    settings = make_settings()
    escaped = {
        name: getattr(settings, name).replace("\n", "\\n")
        for name in ("team_private_key", "team_public_key", "admin_public_key")
    }

    assert load_identity(make_settings(**escaped)).username == "team-a"


def test_missing_variables_are_all_reported():
    with pytest.raises(StartupError) as exc:
        load_identity(Settings(_env_file=None))

    message = str(exc.value)
    for name in ("TEAM_USERNAME", "TEAM_PRIVATE_KEY", "TEAM_PUBLIC_KEY", "ADMIN_PUBLIC_KEY"):
        assert name in message


def test_private_key_must_match_public_key():
    _, other_public = keypair()

    with pytest.raises(StartupError, match="does not match"):
        load_identity(make_settings(team_public_key=other_public))


def test_garbage_keys_are_rejected():
    with pytest.raises(StartupError, match="ADMIN_PUBLIC_KEY is not a valid"):
        load_identity(make_settings(admin_public_key="not a key"))


def test_admin_key_cannot_be_the_team_key():
    settings = make_settings()

    with pytest.raises(StartupError, match="team's own public key"):
        load_identity(make_settings(
            team_private_key=settings.team_private_key,
            team_public_key=settings.team_public_key,
            admin_public_key=settings.team_public_key,
        ))
