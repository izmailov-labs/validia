# Changelog

All notable changes to this project are documented in this file.

The format is based on [Keep a Changelog](https://keepachangelog.com/en/1.1.0/),
and this project adheres to [Semantic Versioning](https://semver.org/spec/v2.0.0.html).

## [Unreleased]

### Added

- Provenance spine: `Origin`, `Tracked`, and a merge that records what each
  value shadowed.
- `Source` protocol with a `SourceChain` supporting name-addressed
  insertion, plus environment, `.env`, file, mapping and secrets-directory
  sources.
- Format loaders for TOML, JSON, `.properties` and XML on the standard library,
  and YAML behind the `whence[yaml]` extra. YAML, `.env` and `.properties`
  carry exact line and column numbers.
- `Discovery`: a declarative five-step search chain over explicit paths,
  `$APP_CONFIG`, project roots, per-user config directories and
  `pyproject.toml`, with cross-platform directory conventions.
- `Config` with `get`, `require`, `origin`, `explain`, `dump`, `bind` and
  `with_fallback`. `get` mirrors `dict.get`: the second positional argument is
  the default, and coercion is the named option (`get(key, type_=int)`).
- Interpolation with `${a.b}`, `${a.b:-default}`, `${?a.b}`, `${env:VAR}` and
  `${file:/path}`, resolved lazily after the merge.
- Profiles, profile groups, and the invariant that profiles never reorder
  sources.
- Binding to frozen dataclasses (standard library) and to pydantic models when
  pydantic is installed, with batched errors that carry origins.
- `Secret`, `unlock_secrets()`, `_FILE` indirection and a sanitizer applied to
  every dump.
- `@settings` and `@from_config` decorators. `@settings` works bare or called,
  like `@dataclass`. `@from_config` takes its markers in `Annotated` --
  `Annotated[Db, Injected]`, `Annotated[int, Value("http.retries")] = 3` -- so
  the annotation stays exact and the default stays a default.
- `ArgvSource`, reading `--set key=value` out of `sys.argv`.
- `RelativePath`, a path resolved against the file that declared it rather than
  the process working directory -- possible only because values carry origins.
- A `whence` CLI: `explain`, `dump` and `discovery`.

### Design notes

- **Loading is synchronous, and there is no reload.** Configuration is read once
  before the application runs, where there is no event loop for an `await` to
  yield to. Reload was cut with it: a poll loop means whence owning a thread or a
  loop and mutating a generation other code holds, which is precisely where
  .NET's `ChangeToken`, viper's `WatchConfig` and koanf's `Watch()` each went
  wrong. A process that must pick up a change calls `Config.load(...)` again on a
  schedule it owns.

- **The injection markers live in `Annotated`, not in the default.** A sentinel
  *as* the default -- FastAPI's pre-`Annotated` spelling, and Spring's `@Value`
  translated literally -- forces the annotation to `Any` and hides the real
  default inside the sentinel, where nothing but the decorator can reach it. The
  cost of moving them is that a marked parameter with no default is *required*
  in the signature a type checker reads, and nothing in the type system can say
  "these keyword parameters are now optional"; `@from_config` therefore returns
  `Callable[..., R]`. The runtime signature is untouched, so `inspect.signature`
  and every framework that reads it still see the parameters as written.
- **`@settings` attaches nothing to the class it decorates.** It records a
  prefix and a strictness flag, both of which `Config.bind` reads. An earlier
  draft also added a `load()` method, which no type checker can see -- so the
  convenient spelling and the correct one were different spellings, and they had
  already disagreed about strictness once. `load_settings(cls, config)` is the
  only one.

[Unreleased]: https://github.com/izmailov-labs/validia/compare/main...HEAD
