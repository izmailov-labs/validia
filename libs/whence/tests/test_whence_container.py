"""Scenarios that need a real container mount, not a temporary directory.

Skipped everywhere else. `make test-docker` runs them inside `docker compose`,
where `/run/secrets` is a genuine read-only tmpfs and a ConfigMap volume has the
symlink layout kubelet actually produces -- neither of which a CI runner or a
`tmp_path` fixture can reproduce.
"""

import os
import sys
from pathlib import Path

import pytest

from whence import Config, Discovery, SecretsDirSource

RUN_SECRETS = Path("/run/secrets")

# Read at import, not in a fixture. `conftest.clean_env` is an autouse fixture
# that wipes the whole environment for hermeticity, and it runs first -- so a
# fixture asking for WHENCE_CONTAINER would always see it unset and skip
# everything, silently, including inside the container.
IN_CONTAINER = os.environ.get("WHENCE_CONTAINER") == "1"

pytestmark = [
    pytest.mark.container,
    pytest.mark.skipif(
        not IN_CONTAINER,
        reason="needs a real container mount; run `make test-docker`",
    ),
]


def test_real_docker_secrets_are_read() -> None:
    """`/run/secrets` here is a read-only tmpfs the daemon mounted, not a fixture."""
    assert RUN_SECRETS.is_dir(), "compose did not mount the secrets"
    layer = SecretsDirSource(RUN_SECRETS).load()
    assert layer.found
    assert layer.entries[("db", "password")] == "hunter2"
    assert layer.entries[("api", "token")] == "tok-abc123"
    # The trailing newline every editor adds must not become part of the secret.
    assert "\n" not in str(layer.entries[("db", "password")].value)


def test_a_mounted_secret_outranks_a_config_file(tmp_path: Path) -> None:
    """The precedence inversion whence makes deliberately.

    pydantic-settings puts its secrets directory at the bottom of the stack, so a
    checked-in default silently beats a mounted production secret. A secret an
    operator went to the trouble of mounting is deployment truth.
    """
    (tmp_path / "myapp.toml").write_text('[db]\npassword = "from-the-repo"\n', encoding="utf-8")
    config = Config.load(
        discovery=Discovery("myapp", user_config=False, secrets_dir=RUN_SECRETS, dotenv=()),
        cwd=tmp_path,
        environ={},
        argv=(),
    )
    assert config.get("db.password") == "hunter2"
    # Reported by origin, redacted by value: both the winner and the loser match
    # `*password*`, so `explain` names the files and prints neither secret.
    explanation = config.explain("db.password")
    assert "/run/secrets/db.password" in explanation
    assert "myapp.toml" in explanation
    assert "hunter2" not in explanation
    assert "from-the-repo" not in explanation


def test_the_secrets_mount_is_the_one_compose_provided() -> None:
    """Confirms the scenario is real rather than reading a stray directory.

    Deliberately *not* an assertion that the mount is read-only. Swarm puts
    secrets on a read-only tmpfs, but `docker compose run` bind-mounts them and
    the directory stays writable -- asserting the stricter behaviour would make
    this a test of which orchestrator is running rather than of whence.
    """
    names = {p.name for p in RUN_SECRETS.iterdir() if p.is_file()}
    assert {"db.password", "api.token"} <= names


def test_a_live_configmap_swap_is_seen_through_the_symlinks(tmp_path: Path) -> None:
    """Kubelet's exact layout, built with real symlinks on a real Linux filesystem.

    The user-visible `myapp.toml` symlink never changes; only what `..data`
    resolves to does. An inotify watch on the file would be bound to an inode
    that has been unlinked and would go permanently deaf.
    """

    def publish(body: str, stamp: str) -> None:
        data = tmp_path / f"..{stamp}"
        data.mkdir()
        (data / "myapp.toml").write_text(body, encoding="utf-8")
        tmp = tmp_path / "..data_tmp"
        tmp.symlink_to(data.name)
        tmp.replace(tmp_path / "..data")
        visible = tmp_path / "myapp.toml"
        if not visible.is_symlink():
            visible.symlink_to(Path("..data") / "myapp.toml")
        live = (tmp_path / "..data").resolve()
        for child in tmp_path.iterdir():
            if child.is_dir() and not child.is_symlink() and child != live:
                for stale in child.iterdir():
                    stale.unlink()
                child.rmdir()

    publish("replicas = 1\n", "2026_09_08_10_00_00.111")

    def load() -> Config:
        return Config.load(
            discovery=Discovery("myapp", user_config=False, secrets_dir=None, dotenv=()),
            cwd=tmp_path,
            environ={},
            argv=(),
        )

    assert load().get("replicas") == 1

    publish("replicas = 5\n", "2026_09_08_10_05_00.222")
    assert load().get("replicas") == 5
    assert (tmp_path / "myapp.toml").is_symlink()  # never rewritten


def test_the_environment_is_what_the_scenario_claims() -> None:
    """Print the container's shape, so a failure elsewhere is diagnosable."""
    import locale
    import platform

    sys.stdout.write(
        f"\n    libc={platform.libc_ver()} python={platform.python_version()}"
        f"\n    fsencoding={sys.getfilesystemencoding()}"
        f" preferred={locale.getpreferredencoding(False)}"
        f"\n    uid={os.getuid()} run_secrets={RUN_SECRETS.is_dir()}\n"
    )
    assert sys.platform == "linux"
