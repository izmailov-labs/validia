"""``@settings`` and ``@from_config``: declaring configuration, and receiving it.

``@settings`` is Spring's ``@ConfigurationProperties`` without the container --
it records which subtree a class binds to, and nothing else.

``@from_config`` fills declared parameters from the ambient configuration. It is
a convenience for whence's *users*; whence itself, and the packages in this
workspace, construct their objects explicitly. Reflective dependency injection
is a design franca deliberately rejected, and offering this decorator does not
reopen that decision.

Both markers live in :data:`~typing.Annotated`, not in the parameter default::

    @from_config
    def connect(*, db: Annotated[Db, Injected], retries: Annotated[int, Value("http.retries")] = 3) -> Conn: ...

The older spelling -- a sentinel *as* the default -- forces the annotation to be
``Any`` (the sentinel is not a ``Db``), and moves the real default inside the
sentinel where nothing but the decorator can reach it. In ``Annotated`` the type
stays exact, the default stays a default, and a function keeps working normally
for anyone who passes both arguments.

The signature is read once, at decoration time, and only parameters the caller
omitted are filled -- an explicit argument always wins. That guarantee is why a
marked parameter must be keyword-only, which ``@from_config`` checks at
decoration: filling happens through ``kwargs``, so a positional argument is
invisible to it. The ambient configuration lives in a
:class:`~contextvars.ContextVar`, so a test can swap it without leaking into
another task.
"""

import functools
import inspect
from collections.abc import Callable, Generator
from contextlib import contextmanager
from contextvars import ContextVar
from dataclasses import dataclass
from typing import Annotated, Any, cast, get_args, get_origin, get_type_hints, overload

from .binding.coerce import strip_annotated
from .config import Config
from .errors import ConfigError

__all__ = [
    "Injected",
    "Value",
    "configure",
    "current_config",
    "from_config",
    "load_settings",
    "settings",
]

_CURRENT: ContextVar[Config | None] = ContextVar("whence_config", default=None)

# A parameter with no default at all. `None` cannot stand in for this: `None` is
# a legitimate default, and the difference decides whether a missing key falls
# back or raises.
_NO_DEFAULT = object()


@dataclass(frozen=True, slots=True)
class Value:
    """Annotation metadata: fill this parameter from one configuration key.

    Written inside :data:`~typing.Annotated`, so the parameter keeps both its
    real type and its real default::

        retries: Annotated[int, Value("http.retries")] = 3

    A marked parameter with no default is required: a missing key raises
    :class:`~whence.MissingKeyError` rather than passing ``None`` on.

    Attributes:
        key: The dotted key to read.
    """

    key: str


@dataclass(frozen=True, slots=True)
class _Inject:
    """The type of :data:`Injected`."""

    def __repr__(self) -> str:
        """Render as the name it is exported under."""
        return "Injected"


Injected = _Inject()
"""Annotation metadata: bind this parameter's own type from configuration.

``Annotated[Db, Injected]`` binds ``Db`` from the subtree its ``@settings``
prefix names. Binding always happens, so a default on such a parameter would be
unreachable -- ``@from_config`` rejects one at decoration rather than ignoring
it.
"""

_Marker = Value | _Inject


def configure(config: Config | None) -> None:
    """Set the ambient configuration for this context.

    Args:
        config: The configuration, or ``None`` to clear it.
    """
    _CURRENT.set(config)


@contextmanager
def current_config(config: Config) -> Generator[Config]:
    """Use a configuration for the duration of a block.

    Args:
        config: The configuration to install.

    Yields:
        The same configuration.
    """
    token = _CURRENT.set(config)
    try:
        yield config
    finally:
        _CURRENT.reset(token)


def _ambient() -> Config:
    """Return the ambient configuration.

    Raises:
        ConfigError: If none has been installed.
    """
    config = _CURRENT.get()
    if config is None:
        msg = (
            "no ambient configuration: call whence.configure(cfg), use "
            "`with whence.current_config(cfg):`, or pass the config explicitly"
        )
        raise ConfigError(msg)
    return config


@overload
def settings[T](prefix: type[T], /) -> type[T]: ...


@overload
def settings[T](prefix: str = "", *, strict: bool = True) -> Callable[[type[T]], type[T]]: ...


def settings(prefix: Any = "", *, strict: bool = True) -> Any:
    """Mark a class as binding to a subtree of configuration.

    Usable bare or called, like :func:`~dataclasses.dataclass`: ``@settings``
    binds the whole tree, ``@settings("db")`` binds one subtree.

    Args:
        prefix: The dotted subtree, such as ``"db"``.
        strict: Whether a key under ``prefix`` that this class does not declare
            is an error. True is the right default -- a typo'd key is the most
            common configuration bug and is otherwise invisible -- but a class
            that deliberately covers only part of its subtree sets it False.

    Returns:
        The decorated class when used bare, otherwise a class decorator.
    """

    def decorate(cls: type[Any], subtree: str) -> type[Any]:
        # Class-level metadata, read by `Config.bind`. The same shape the
        # standard library uses for `__dataclass_fields__`.
        cls.__whence_prefix__ = subtree
        cls.__whence_strict__ = strict
        return cls

    if isinstance(prefix, type):
        return decorate(prefix, "")
    return lambda cls: decorate(cls, prefix)


def load_settings[T](
    cls: type[T], config: Config | None = None, *, strict: bool | None = None
) -> T:
    """Bind a ``@settings`` class from a configuration.

    The typed spelling of ``config.bind(cls)``, and the only one: ``@settings``
    attaches no methods to the class it decorates, because an attribute added by
    a decorator is invisible to a type checker.

    Args:
        cls: A class decorated with :func:`settings`.
        config: The configuration; defaults to the ambient one.
        strict: Override the strictness ``@settings`` recorded on the class.

    Returns:
        An instance of ``cls``.
    """
    target = config if config is not None else _ambient()
    return cast("T", target.bind(cls, strict=strict))


def _marker_of(annotation: Any, *, owner: str, name: str) -> _Marker | None:
    """Return the whence marker in an annotation, if it carries one.

    Args:
        annotation: The parameter's resolved annotation.
        owner: The decorated function's qualified name, for the error message.
        name: The parameter's name, for the error message.

    Returns:
        The marker, or None when the parameter is not whence's business.

    Raises:
        ConfigError: If the annotation carries more than one marker.
    """
    if get_origin(annotation) is not Annotated:
        return None
    found = [meta for meta in get_args(annotation)[1:] if isinstance(meta, (Value, _Inject))]
    if len(found) > 1:
        markers = ", ".join(repr(marker) for marker in found)
        msg = (
            f"{owner}: parameter {name!r} carries more than one whence marker ({markers}); keep one"
        )
        raise ConfigError(msg)
    return found[0] if found else None


def _resolved_annotations(
    fn: Callable[..., Any], parameters: dict[str, inspect.Parameter]
) -> dict[str, Any]:
    """Return each parameter's annotation as an object rather than a string.

    ``get_type_hints`` is called only when a *parameter* annotation is actually a
    string -- under ``from __future__ import annotations`` every one of them is.
    Resolving unconditionally would also resolve the return annotation, so a
    function returning a ``TYPE_CHECKING``-only type (which ruff's own TC rules
    push you towards) would fail to decorate for a reason having nothing to do
    with configuration.

    Args:
        fn: The decorated function.
        parameters: Its signature parameters.

    Returns:
        A mapping of parameter name to annotation.
    """
    if any(isinstance(p.annotation, str) for p in parameters.values()):
        return get_type_hints(fn, include_extras=True)
    return {
        name: p.annotation
        for name, p in parameters.items()
        if p.annotation is not inspect.Parameter.empty
    }


def _plan(fn: Callable[..., Any]) -> list[tuple[str, _Marker, Any, Any]]:
    """Work out, once, what each call will have to fill.

    Args:
        fn: The decorated function.

    Returns:
        One ``(name, marker, type, default)`` tuple per marked parameter.

    Raises:
        ConfigError: If a marked parameter is not keyword-only, or if an
            ``Injected`` parameter carries a default that could never be used.
    """
    owner = fn.__qualname__
    parameters = dict(inspect.signature(fn).parameters)
    annotations = _resolved_annotations(fn, parameters)

    plan: list[tuple[str, _Marker, Any, Any]] = []
    positional: list[str] = []
    unreachable: list[str] = []
    for name, parameter in parameters.items():
        marker = _marker_of(annotations.get(name), owner=owner, name=name)
        if marker is None:
            continue
        if parameter.kind is not inspect.Parameter.KEYWORD_ONLY:
            positional.append(name)
        default = _NO_DEFAULT if parameter.default is inspect.Parameter.empty else parameter.default
        if isinstance(marker, _Inject) and default is not _NO_DEFAULT:
            unreachable.append(name)
        plan.append((name, marker, strip_annotated(annotations[name]), default))

    # Filling goes through kwargs, so an argument passed positionally is invisible
    # to it: the parameter would be filled a second time and the call would die of
    # a TypeError at a site that never mentioned configuration. Keyword-only is the
    # only shape in which "an explicit argument always wins" is true, so require it
    # here rather than letting the caller discover it later.
    if positional:
        names = ", ".join(repr(name) for name in positional)
        plural = "s" if len(positional) > 1 else ""
        msg = (
            f"{owner}: injected parameter{plural} {names} must be keyword-only; "
            f"put a bare `*` before {'them' if plural else 'it'} in the signature. "
            "Otherwise an argument passed positionally is filled a second time and the "
            "call raises TypeError."
        )
        raise ConfigError(msg)
    if unreachable:
        names = ", ".join(repr(name) for name in unreachable)
        msg = (
            f"{owner}: parameter{'s' if len(unreachable) > 1 else ''} {names} "
            "combine `Injected` with a default, which can never be used -- Injected "
            "always binds. Drop the default, or use `Value(key)` if you wanted a fallback."
        )
        raise ConfigError(msg)
    return plan


def from_config[**P, R](fn: Callable[P, R]) -> Callable[..., R]:
    """Fill parameters annotated :data:`Injected` or :class:`Value` from configuration.

    The wrapper is typed ``Callable[..., R]`` rather than ``Callable[P, R]``,
    and that is the one real cost of putting the markers in ``Annotated``. A
    marked parameter with no default is *required* in the signature a type
    checker reads, so ``connect()`` would be an error at every call site even
    though filling it is the entire point. Nothing in the type system can say
    "these particular keyword parameters are now optional", so the return type
    says the honest thing instead: after decoration, the arguments are whichever
    subset the caller chooses to pass. The return type is still checked, and the
    runtime signature is untouched -- ``inspect.signature`` and every framework
    that reads it see the parameters exactly as written.

    Args:
        fn: The function to wrap; sync or async.

    Returns:
        The wrapped function, accepting any subset of its own arguments.

    Raises:
        ConfigError: If a marked parameter is not keyword-only, carries two
            markers, or combines ``Injected`` with an unreachable default.
    """
    plan = _plan(fn)

    def fill(kwargs: dict[str, Any]) -> None:
        # Nothing left to fill means nothing to look up: a caller who passes every
        # marked argument -- a test, typically -- needs no ambient configuration at
        # all, and demanding one would make `@from_config` contagious.
        pending = [entry for entry in plan if entry[0] not in kwargs]
        if not pending:
            return
        config = _ambient()
        for name, marker, annotation, default in pending:
            if isinstance(marker, Value):
                kwargs[name] = (
                    config.require(marker.key, type_=annotation)
                    if default is _NO_DEFAULT
                    else config.get(marker.key, default, type_=annotation)
                )
            else:
                kwargs[name] = config.bind(annotation)

    if inspect.iscoroutinefunction(fn):

        @functools.wraps(fn)
        async def async_wrapper(*args: P.args, **kwargs: P.kwargs) -> Any:
            fill(cast("dict[str, Any]", kwargs))
            return await fn(*args, **kwargs)

        return cast("Callable[..., R]", async_wrapper)

    @functools.wraps(fn)
    def wrapper(*args: P.args, **kwargs: P.kwargs) -> R:
        fill(cast("dict[str, Any]", kwargs))
        return fn(*args, **kwargs)

    return wrapper
