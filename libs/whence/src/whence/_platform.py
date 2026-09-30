"""Platform conventions, behind a seam so tests do not need three machines.

Every function takes the platform and the environment as arguments rather than
reading ``sys.platform`` and ``os.environ`` directly. That is what lets one test
run assert the Windows, macOS and Linux answers on whichever machine CI happens
to schedule, and it is the same seam figment's ``Jail`` and viper's ``afero``
exist to provide.
"""

import os
import sys
from collections.abc import Mapping
from pathlib import Path, PurePath, PurePosixPath, PureWindowsPath

__all__ = ["Platform", "current_platform", "flavour", "same_file_key", "user_config_dirs"]

type Platform = str
"""A ``sys.platform`` value: ``"linux"``, ``"darwin"``, ``"win32"``, ..."""


def current_platform() -> Platform:
    """Return the running platform.

    Returns:
        The value of ``sys.platform``.
    """
    return sys.platform


def flavour(platform: Platform) -> type[PurePath]:
    r"""Return the path flavour a platform uses.

    ``PureWindowsPath`` and ``PurePosixPath`` are fully functional on every
    platform, which is what makes the whole platform matrix testable in one
    process. Overriding ``sys.platform`` alone is not enough and is actively
    misleading: ``PurePath(r"C:\\Users\\u").is_absolute()`` is ``False`` on POSIX,
    so a Windows test would silently exercise POSIX semantics and pass against
    the wrong expectation.

    Args:
        platform: A ``sys.platform`` value.

    Returns:
        ``PureWindowsPath`` on Windows, ``PurePosixPath`` elsewhere.
    """
    return PureWindowsPath if platform == "win32" else PurePosixPath


def _is_abs(value: str | None, platform: Platform) -> bool:
    """Report whether a value is a non-empty absolute path for this platform."""
    if not value:
        return False
    return flavour(platform)(value).is_absolute()


def _home(environ: Mapping[str, str], platform: Platform) -> PurePath | None:
    """Resolve the user's home directory, or ``None`` if it is unusable.

    Deliberately not ``os.path.expanduser("~")`` or ``Path.home()``: with
    ``HOME=""`` the former returns ``"/"`` and with ``HOME="rel/dir"`` it returns
    that string verbatim. Neither raises, so both turn a broken environment into
    a plausible-looking wrong answer -- a config directory of ``/.config/myapp``,
    or a directory literally named ``~`` in the working directory.
    """
    if platform == "win32":
        value = environ.get("USERPROFILE")
        if not value:
            drive, tail = environ.get("HOMEDRIVE", ""), environ.get("HOMEPATH")
            value = (drive + tail) if tail else None
    else:
        value = environ.get("HOME")
        if not value:
            try:
                import pwd

                value = pwd.getpwuid(os.getuid()).pw_dir
            except (ImportError, KeyError, AttributeError):  # pragma: no cover - POSIX only
                value = None
    if value is None or not _is_abs(value, platform):
        return None
    return flavour(platform)(value)


def _xdg_dir(
    environ: Mapping[str, str], var: str, fallback: PurePath | None, platform: Platform
) -> PurePath | None:
    """Read an XDG single-directory variable.

    The specification is explicit that a value which is unset, empty, or not an
    absolute path must be treated as unset, which is the part most
    implementations skip.
    """
    value = environ.get(var, "")
    if _is_abs(value, platform):
        return flavour(platform)(value)
    return fallback


def user_config_dirs(
    app: str,
    *,
    platform: Platform | None = None,
    environ: Mapping[str, str] | None = None,
) -> tuple[PurePath, ...]:
    """Return the per-user configuration directories for an application.

    Ordered most specific first. The directories are not required to exist; the
    caller decides what to do about that.

    The conventions differ per platform and there is genuine disagreement in the
    ecosystem about macOS, where the Apple answer is ``Application Support`` but
    a large share of command-line tools follow XDG. whence returns both, Apple's
    first, and additionally honours ``XDG_CONFIG_HOME`` there when the user has
    set it explicitly -- which is the only signal that they meant it.

    Args:
        app: The application name.
        platform: Override the platform; defaults to the running one.
        environ: Override the environment; defaults to ``os.environ``.

    Returns:
        Directories to search, most specific first, deduplicated.
    """
    system = current_platform() if platform is None else platform
    env = os.environ if environ is None else environ
    home = _home(env, system)
    pure = flavour(system)
    out: list[PurePath] = []

    if system == "win32":
        # Local before Roaming. Roaming is copied over the network and merged
        # last-writer-wins across machines, Microsoft has been retreating from
        # it since 1909, and Package State Roaming is gone in Windows 11.
        # platformdirs defaults to Local for the same reasons.
        for var in ("LOCALAPPDATA", "APPDATA"):
            value = env.get(var)
            if value and _is_abs(value, system):
                out.append(pure(value) / app)
        if not out and home is not None:
            out.append(home / "AppData" / "Local" / app)
    elif system == "darwin":
        # Apple says Application Support; a large share of command-line tools
        # follow XDG. Both are served, Apple's first, and an explicitly set
        # XDG_CONFIG_HOME is honoured because setting it is the only signal a
        # user can give. platformdirs reached the same position in 4.6.0.
        if home is not None:
            out.append(home / "Library" / "Application Support" / app)
        explicit = _xdg_dir(env, "XDG_CONFIG_HOME", None, system)
        if explicit is not None:
            out.append(explicit / app)
        if home is not None:
            out.append(home / ".config" / app)
    else:
        base = _xdg_dir(env, "XDG_CONFIG_HOME", None if home is None else home / ".config", system)
        if base is not None:
            out.append(base / app)
        # A value of ":" or "::" leaves nothing after filtering, which must mean
        # the default rather than nothing at all.
        site = [
            pure(part)
            for part in env.get("XDG_CONFIG_DIRS", "").split(":")
            if _is_abs(part, system)
        ] or [pure("/etc/xdg")]
        out.extend(path / app for path in site)

    # setdefault keeps the first path per normalised key, matching the order
    # these directories are searched in.
    unique: dict[str, PurePath] = {}
    for path in out:
        unique.setdefault(os.path.normcase(str(path)), path)
    return tuple(unique.values())


def same_file_key(path: Path) -> object:
    """Return a key that is equal for two paths naming the same file.

    macOS (APFS, HFS+) and Windows (NTFS) are case-insensitive but
    case-preserving, so ``app.toml`` and ``App.TOML`` are one file there and two
    on Linux. Discovery deduplicates candidates through this so a
    case-insensitive filesystem does not look like an ambiguity.

    Args:
        path: The path to key.

    Returns:
        The file's ``(device, inode)`` identity when it exists, which is exact on
        every platform, and a normalised path string when it does not.

    ``os.path.normcase`` alone is not enough: it is the identity function on
    macOS, which is precisely a platform where the filesystem folds case.
    """
    try:
        stat = path.stat()
    except OSError:
        return os.path.normcase(str(path))
    return (stat.st_dev, stat.st_ino)
