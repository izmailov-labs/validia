"""Per-platform config directories, asserted for all three on one machine.

The seam is why this test exists: `user_config_dirs` takes the platform and the
environment as arguments, so CI does not need three runners to prove the
conventions are right.
"""

from pathlib import Path, PurePosixPath, PureWindowsPath

from whence._platform import current_platform, same_file_key, user_config_dirs

HOME = {"HOME": "/home/u", "USERPROFILE": r"C:\Users\u"}


def test_linux_follows_xdg() -> None:
    dirs = user_config_dirs("myapp", platform="linux", environ=HOME)
    assert dirs[0] == PurePosixPath("/home/u/.config/myapp")
    assert PurePosixPath("/etc/xdg/myapp") in dirs


def test_linux_honours_xdg_config_home() -> None:
    env = {**HOME, "XDG_CONFIG_HOME": "/custom", "XDG_CONFIG_DIRS": "/a:/b"}
    dirs = user_config_dirs("myapp", platform="linux", environ=env)
    assert dirs[0] == PurePosixPath("/custom/myapp")
    assert dirs[1:] == (PurePosixPath("/a/myapp"), PurePosixPath("/b/myapp"))


def test_xdg_ignores_a_relative_value() -> None:
    """The spec says a non-absolute value must be treated as unset."""
    env = {**HOME, "XDG_CONFIG_HOME": "relative/path"}
    assert user_config_dirs("myapp", platform="linux", environ=env)[0] == PurePosixPath(
        "/home/u/.config/myapp"
    )


def test_macos_prefers_application_support_but_also_offers_xdg() -> None:
    dirs = user_config_dirs("myapp", platform="darwin", environ=HOME)
    assert dirs[0] == PurePosixPath("/home/u/Library/Application Support/myapp")
    assert PurePosixPath("/home/u/.config/myapp") in dirs


def test_macos_honours_an_explicit_xdg_config_home() -> None:
    env = {**HOME, "XDG_CONFIG_HOME": "/custom"}
    assert PurePosixPath("/custom/myapp") in user_config_dirs(
        "myapp", platform="darwin", environ=env
    )


def test_windows_prefers_local_over_roaming() -> None:
    """Roaming is merged across machines and Microsoft has been retiring it."""
    env = {
        **HOME,
        "APPDATA": r"C:\Users\u\AppData\Roaming",
        "LOCALAPPDATA": r"C:\Users\u\AppData\Local",
    }
    dirs = user_config_dirs("myapp", platform="win32", environ=env)
    assert len(dirs) == 2
    assert dirs[0] == PureWindowsPath(r"C:\Users\u\AppData\Local\myapp")
    assert dirs[1] == PureWindowsPath(r"C:\Users\u\AppData\Roaming\myapp")


def test_windows_falls_back_to_userprofile() -> None:
    dirs = user_config_dirs("myapp", platform="win32", environ={"USERPROFILE": r"C:\Users\u"})
    assert dirs[0] == PureWindowsPath(r"C:\Users\u\AppData\Local\myapp")


def test_windows_paths_are_judged_with_windows_rules() -> None:
    r"""Windows values must be judged by Windows rules.

    A POSIX ``PurePath`` calls ``C:\Users\u`` relative, so without the flavour
    seam this test would exercise POSIX semantics and pass against the wrong
    expectation while proving nothing.
    """
    dirs = user_config_dirs(
        "myapp", platform="win32", environ={"LOCALAPPDATA": r"C:\Users\u\AppData\Local"}
    )
    assert dirs == (PureWindowsPath(r"C:\Users\u\AppData\Local\myapp"),)


def test_a_relative_or_empty_home_is_refused_not_guessed() -> None:
    """expanduser("~") turns HOME="" into "/" and HOME="rel" into "rel"."""
    assert user_config_dirs("myapp", platform="linux", environ={"HOME": "rel/dir"}) == (
        PurePosixPath("/etc/xdg/myapp"),
    )


def test_xdg_config_dirs_of_only_separators_means_the_default() -> None:
    env = {**HOME, "XDG_CONFIG_DIRS": "::"}
    dirs = user_config_dirs("myapp", platform="linux", environ=env)
    assert PurePosixPath("/etc/xdg/myapp") in dirs


def test_directories_are_deduplicated() -> None:
    env = {**HOME, "XDG_CONFIG_HOME": "/home/u/.config"}
    dirs = user_config_dirs("myapp", platform="darwin", environ=env)
    assert len(dirs) == len(set(dirs))


def test_current_platform_is_a_string() -> None:
    assert isinstance(current_platform(), str)


def test_same_file_key_is_stable() -> None:
    assert same_file_key(Path("a.toml")) == same_file_key(Path("a.toml"))
