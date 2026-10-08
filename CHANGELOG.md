# Changelog

All notable changes to this project are documented in this file.

The format is based on [Keep a Changelog](https://keepachangelog.com/en/1.1.0/),
and this project adheres to [Semantic Versioning](https://semver.org/spec/v2.0.0.html).

## [Unreleased]

### Added

- Project scaffolding: `src/` layout, strict typing, async test plumbing, CI,
  docs, and a trusted-publishing release workflow.
- Runtime dependencies on the two sibling packages, consumed from PyPI:
  [`franca`](https://pypi.org/project/franca/) (`>=0.1.1,<1`), the model
  communication layer, and [`whence`](https://pypi.org/project/whence/)
  (`>=1.0,<2`), the configuration layer.
- The `validia` command (also `python -m validia`). `init` writes a starter
  `validia.toml` and an example suite, asking which folder and name to use
  (`--evals` and `--name` answer without asking). An existing `validia.toml` is
  kept, so running `init` again adds another suite. `config` shows every resolved
  setting and the flag, variable or file it came from. `run --dry-run` validates
  the suite and prints what it would run.
- `validia run` evaluates a suite: every case, `--reps` times, sent through franca
  (Anthropic, OpenAI, Google, xAI, DeepSeek) and graded by the suite's own check.
  It reports the pass rate with a 95% Wilson interval, results by group, the failing
  cases with why, tokens and latency, and writes every trial to `trials.jsonl` and
  the totals to `summary.json` under `run.output`. Retryable failures are retried
  after the provider's `Retry-After` or a backoff (`run.retries`); a call that still
  fails is an error, not a wrong answer, and fails the run, as does a pass rate under
  `run.fail_under`. A failure every trial would hit alike -- `auth`, `unsupported`,
  `selection` -- stops the run at the first one instead of sending the rest. Real HTTP is the `validia[http]` extra. Tool suites are refused
  until franca carries tools. The runner is public API (`build_model`, `run_trial`,
  `summarize`) and loop-neutral; the command line drives it on asyncio.
- Suite files: one TOML file per suite with the prompt under test in its own file,
  and `[[cases]]` with ids, ordered tags and an `expected` answer whose shape
  follows the suite's answer type: `label`, `json` (fields, plus `required` keys),
  `text` (`equals`, `contains`, `not_contains`, `matches`) or `tool` (a tool and its
  arguments, or `none`, against tools defined in a JSON file). Every check is code;
  an empty reply always fails. Loading reports every problem at once, and refuses
  checks that would pass any reply. `init` writes an example suite per type.
- `validia create` builds a suite of your own from questions: where it goes, its
  answer type, the labels, keys or tools grading needs, the prompt (typed in, an
  existing file the suite points at, or a placeholder), and its cases. Nothing is
  written until the last answer, and only if the suite loads. Every choice in
  `init`, `create` and `add` is a numbered menu that also takes the option's name.
- `validia lint` checks prompt files, or suites' prompts and tool descriptions,
  against the rules for a model: 41 regex rules in seven categories (`wording`,
  `context`, `reasoning`, `output`, `tools`, `security`, `maintenance`), with
  `--category` and `--fail-on`. Every rule has its own cases: text it must fire on
  and text it must stay quiet on. Whole before-and-after prompts list the exact
  rules each must fire, and how severe each must be on its model.
  `validia rules test` proves them, `validia rules list` shows them.
- Rules are named for what they catch, and a rule's id is its category and name:
  `reasoning/show-reasoning`, `wording/capitals`, `tools/missing-when-not`. Findings,
  `validia rules explain` (which also takes the name alone) and project folders use it.
- A folder per rule. Each rule lives in `<category>/<rule>/`, with a folder per
  target: `default/` for every model, `<provider>/default/` for a vendor and
  `<provider>/<model>/` for one model. Its cases sit in a separate file beside each
  definition (`1.0.0.cases.toml`). Whole-prompt examples (`examples/<target>/`) and
  prompting guidance with sources (`guidance/<target>/`) have folders of their own
  per category. A model reads each rule along its chain, falling back to its vendor's
  file and then the default, and each file extends, replaces or turns off the rule
  beneath it. `validia rules explain` shows which files a rule was read from. `lint`
  prints the guidance for the categories it found something in, and
  `validia rules guidance -m MODEL` prints all of it. Files ship for Anthropic
  (wording, context, reasoning, output), OpenAI (wording, tools) and Google (wording).
- Category releases in semantic versions. Each file is named for the release it
  changed in and frozen once released. A patch only removes false positives, a minor
  release adds only `info` findings, and a major release can add a warning or an
  error, or remove a rule (`validia rules versions`). `lint.rules.<category>` pins a
  category to a release (`"latest"` by default, `"1"`, `"1.2"` or `"1.2.3"`). Every
  file is then read as it was at that release, so a rule added later does not exist
  yet. Retiring a rule is one default file with `enabled = false`: older vendor
  and model files about it are skipped, and examples that name it are left out.
- A project `rules/` folder with the core's own layout
  (`rules/<category>/<rule>/default/1.0.0.toml`, cases beside it, vendor and model
  folders, `examples/` and `guidance/`), read by the same code after the core. A project
  can extend, replace or turn off any rule, for every model or for one; add rules and
  categories; or fall back with `from = "1"`, which takes one rule along its whole core
  chain as an older release had it. Its files need no `released` or `changes`, a file
  that would never be read is reported, and `validia rules test --all` proves the
  project's files for every target they are written for. Reports name the rules they
  used, as `rules 1.0.0 for anthropic:claude-opus-5-5 + rules/`.
- `validia rules extend`, `replace`, `disable`, `fallback` and `new` write a project's
  rule files: in `default/`, a provider's folder (`-m MODEL --vendor`) or a model's
  (`-m MODEL`). Each loads the project and proves the rule on its target, and leaves
  nothing written if either fails. `replace` copies the rule as the target reads it,
  cases included; `new` asks for whatever its flags leave out; `--version` writes a
  newer file that starts from the older one. The same functions -- `extend_rule`,
  `replace_rule`, `disable_rule`, `fall_back`, `new_rule` -- are public API.
- Security rules: a credential in the prompt (an error), user input placed without
  delimiters, an instruction to obey whatever the input says, and "never reveal
  these instructions", which is a request rather than a control (`info`).
- A guided prompt builder in `validia create`: one question per section (role, job,
  context, rules with reasons, tone for free text), a closing instruction that fits
  the answer type and its grader, and a preview to accept or redo. Core checks --
  capitals, a rule without a reason, a rule that only forbids, vague words -- run on
  each answer as it is typed and never block. Sections, checks and instructions are
  a TOML template; `validia template` prints the built-in one, and a `prompt.toml` in
  the project replaces it.
- A public, front-end-agnostic API in the `validia` package: `SuiteSpec` and
  `CaseSpec` built from plain JSON (`from_dict`), `create_suite`, `add_case`,
  `check_reply`, `find_case`, `locate_suite`, `parse_reply` and `describe_suite`; and prompt building
  as data -- `prompt_questions`, `review_answer`, `review_answers`, `render_prompt`.
  Failures are `SpecError` / `BuildError` with a `problems` list. The `validia`
  command is one front end over these; a REST service or notebook can be another.
  Every path a request can name -- a suite, a `prompt_file`, a `tools_file` -- must
  resolve inside the project, symlinks included.
- `validia add` builds a case interactively in the suite's shape, appends it to
  `suite.toml` (rolled back if the suite would no longer load), and lets you try
  replies against it. `validia check` grades a supplied reply against one case
  without calling a model.
- Model access: `model` as `provider:model` (the provider inferred for `claude-`,
  `gpt-`, `gemini-`, `grok-`, `deepseek-` names), franca's `[providers.<name>]`
  settings, and API keys read in franca's order from the environment, then from the
  project's `.env` (the shell wins; an empty variable counts as unset).
  `validia config --keys` lists every provider's key source, and `run --dry-run`
  names the one it would use, or what to set; neither ever prints a key.
- Per-suite settings: a `validia.toml` in a suite's folder outranks the project's;
  `validia config --suite` shows a suite's view.
- Settings through whence: `validia.toml`, `[tool.validia]` in `pyproject.toml`,
  `VALIDIA_*` variables, `.env`, profiles (`-p`), `--set key=value` and per-command
  flags, with unknown keys and out-of-range values reported against their origin.
- A Prompt rules section on the docs site, in the core rules' own layout: a
  default page, a page per provider and per model showing every rule as that model
  sees it with its writing guidance, rule versions with each category's release
  history, and project rules with the rule file reference. Every rules page has a
  release dropdown per category, showing it as a pin to that release reads it. The
  tables are generated from the installed package at build time, and the build fails
  when a provider or model the rules ship has no page.
- Examples for every rule on the Prompt rules pages: its cause and effect, the text it
  flags (negative) with the exact words flagged, the text it stays quiet on (positive),
  and its fix, plus each category's whole prompts before and after. Cause and effect
  live in `docs/rules/effects.toml`, grounded in the research behind each rule; the
  build fails when a rule has no entry.

### Changed

- The repository is a single package again. `franca` and `whence` were extracted
  into repositories of their own ([izmailov-labs/franca](https://github.com/izmailov-labs/franca),
  [izmailov-labs/whence](https://github.com/izmailov-labs/whence)); the uv workspace,
  `libs/` layout and package-scoped release tags went with them. Tags are plain
  `vX.Y.Z` from here on.

[Unreleased]: https://github.com/izmailov-labs/validia/compare/main...HEAD
