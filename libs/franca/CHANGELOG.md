# Changelog

All notable changes to this package are documented in this file.

The format is based on [Keep a Changelog](https://keepachangelog.com/en/1.1.0/),
and this project adheres to [Semantic Versioning](https://semver.org/spec/v2.0.0.html).

## [Unreleased]

### Added

- Package scaffolding as a member of the validia.dev workspace.
- `core/settings.py`: `Settings`, `ProviderSettings`, `RoutePolicy`, `RetryPolicy`,
  `load_settings()` and `SettingsKeyProvider`. Loading is delegated to `whence`,
  so a bad setting reports the file it came from; API keys are read at call time
  and never stored in a model.

[Unreleased]: https://github.com/izmailov-labs/validia/compare/main...HEAD
