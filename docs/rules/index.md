# Prompt rules

`validia lint` checks prompts, and the tool descriptions a suite ships, against a set of
rules, without calling a model. A rule is data: a TOML file with a regular expression, a
fix and a severity, proved by a file of text it must fire on and text it must stay quiet
on.

Which rules apply, and how much each finding matters, depends on three things:

- **the model** you lint for, which picks the files read and the severity of each rule;
- **the provider** that serves it, whose files apply to every one of its models;
- **the release** of each category you pin, so that a new release never changes your
  lint gate until you move to it.

These pages follow the same layout as the rules themselves:

| Page | Shows |
|---|---|
| [Default](default.md) | every rule, as its default file defines it for every model |
| A provider, such as [Anthropic](anthropic/index.md) | what that provider's files change, for every one of its models |
| A model, such as [claude-opus-5-5](anthropic/claude-opus-5-5.md) | every rule as that model sees it, with its writing guidance |
| [Rule versions](releases.md) | how releases and pins work, and what changed in each release |
| [Your own rules](custom.md) | a project's `rules/` folder, and the rule file reference |

Every rules page has a release dropdown on each category: it shows the category as a pin
to that release reads it. The tables are generated from the installed package when the
site is built, so they always match the rules `validia lint` reads.

## Linting a prompt

```
$ validia lint evals/billing/prompt.md -m claude-sonnet-5-5
evals/billing/prompt.md:2:1  info   wording/capitals  'NEVER'  State the one real constraint plainly, ...
evals/billing/prompt.md:2:1  warn   wording/rule-without-reason  'NEVER'  Give the reason (because ..., so that ...): ...
evals/billing/prompt.md:3:1  error  reasoning/show-reasoning  'Show your reasoning'  Remove it: these models refuse ...
wording/anthropic/default 1.0.0: Current Claude models follow a plain instruction with its reason ...
  - Give the reason with the rule: Claude generalises from the reason to cases the rule never names.
  ...
rules 1.0.0 for anthropic:claude-sonnet-5-5: 3 findings (1 error, 1 warn, 1 info)
```

Give it prompt files, or suite files: a suite is linted through its prompt and every tool
description in its tools file. Findings go to standard output, one per line, as
`file:line:column  severity  rule  'excerpt'  fix`. Standard error carries the rest: the
model's writing guidance for each category with a finding (`--no-guidance` leaves it
out), a pointer to the most severe finding's rule, and the summary.

| Flag | Setting | Does |
|---|---|---|
| `-m MODEL` | `model` | the model the prompt is for; see [choosing the model](#choosing-the-model) |
| `--fail-on {error,warning}` | `lint.fail_on` | the lowest severity that makes the command exit 1; `error` by default |
| `--category NAME` | | only this category's rules; repeat for more |
| `--no-guidance` | | leave the model's writing guidance out |

There are three severities. `error` is for text that must not ship: a credential, or a
request the model refuses. `warn` is for text that usually makes the prompt worse, and
`info` for a habit worth knowing about.

## Choosing the model

The model comes from `-m`, or from the `model` setting, so a suite folder's own
`validia.toml` can lint its prompt for the model it runs on. It is written
`provider:model`, as in `anthropic:claude-sonnet-5-5`, and the provider may be left out
when the name says it:

| A name starting with | Is served by |
|---|---|
| `claude-` | `anthropic` |
| `gpt-` | `openai` |
| `gemini-` | `google` |
| `grok-` | `xai` |
| `deepseek-` | `deepseek` |

Any other name needs its provider written out: `mistral:mistral-large-3`. A name validia
cannot place is an error that says so, never a silent fallback.

The model decides two things:

1. **Which files are read.** Each rule is read from its `default/` folder, then its
   provider's (`anthropic/default/`), then its model's (`anthropic/claude-opus-5-5/`).
   The model folder is matched on the exact model id.
2. **How severe each rule is.** A provider file can set a severity on some of its models
   only, naming them with globs: `models = ["gpt-4.1*", "gpt-5"]`.

A missing folder is skipped. So a model validia has no folder for still gets its
provider's layer, and a provider with no folders, such as `mistral`, gets the defaults.
Without any model, only the default files are read: every rule at its default severity,
and no writing guidance.

The same prompt can therefore lint differently on two models of one provider. Asking to
see the model's reasoning is an error on Sonnet 5.5, whose safeguards refuse it, and a
warning on Haiku 4.5:

```
$ validia rules explain reasoning/show-reasoning -m claude-sonnet-5-5
reasoning/show-reasoning: Asks to see the model's reasoning
  category    reasoning, reads the prompt
  severity    error on claude-sonnet-5-5
  fix         Remove it: these models refuse requests to expose their reasoning; ask for a short justification instead.
  matches     Fires on every match.
  pattern     show your (thinking|reasoning)|<scratchpad>|<thinking>
  fires on    'Show your reasoning before the answer.'
              'Write notes in <scratchpad> tags.'
  quiet on    'Give the answer, then one sentence on why.'
  read as     default 1.0.0 -> anthropic/default 1.0.0 extend
  released    default 1.0.0
              anthropic/default 1.0.0
```

`read as` names every file laid over the rule, in order. `validia rules list -m MODEL`
shows every rule as one model sees it, as each model's page here does, and
`validia rules guidance -m MODEL` prints the writing guidance its provider and model files
carry, with sources.

## Provider and model layers

Inside a category, every rule has a folder of its own, and inside that, a folder for each
target it changes for:

```
reasoning/
├── show-reasoning/
│   ├── default/1.0.0.toml                  the rule, for every model
│   ├── default/1.0.0.cases.toml            text it must fire on, and stay quiet on
│   └── anthropic/default/1.0.0.toml        every anthropic model
├── self-verify/
│   ├── default/1.0.0.toml
│   ├── default/1.0.0.cases.toml
│   └── anthropic/claude-opus-5/1.0.0.toml  one model
├── examples/<target>/1.0.0.toml            whole prompts, and exactly which rules fire
└── guidance/<target>/1.0.0.toml            how to write for that target, with sources
```

A rule's id is its category and folder name, `reasoning/show-reasoning`; findings,
`validia rules explain` and examples all use it. Targets use franca's provider slug and the
exact model id.

For a model, each rule is read along its chain, most general first, and each file is laid
over the one before it:

```
default  ->  <provider>/default  ->  <provider>/<model>
```

| A file with | Does |
|---|---|
| a full definition (`pattern`, `fix`, ...) | defines the rule; above the default, replaces it |
| `extend = true` and some fields | changes those fields and keeps the rest; its cases add to the rule's |
| `enabled = false` | turns the rule off from there up |

A provider file that makes asking for the reasoning an error on five Claude models, and
leaves every other Claude model where the default put it:

```toml
# reasoning/show-reasoning/anthropic/default/1.0.0.toml
released = "2026-10-07"
changes = [
  "First release: asking to see the model's reasoning is an error on the five models whose safeguards refuse it.",
]

extend = true
severity = "error"
models = ["claude-fable-5", "claude-fable-5-1", "claude-opus-5", "claude-opus-5-5", "claude-sonnet-5-5"]
```

Severity in an extension reads the way it is written:

- `severity` alone applies to every model the file reaches;
- `severity` with `models` applies to the models those globs match, and every other model
  keeps what it had;
- `otherwise` sets the severity on the rest explicitly.

A default file is for every model, so it defines its rule and never names `models`:
model severities go in a provider or model file. A rule folder with no `default/` is a
rule only for the models that have a file.

## Providers and models

Every folder the core rules have, as of the latest release of each category. A model with
no folder of its own reads its provider's, and a provider with none reads the default.

<!-- rules-targets -->

## Commands

| Command | Does |
|---|---|
| `validia lint FILE... [-m MODEL]` | check prompts or suites against the rules for a model |
| `validia rules list [-m MODEL]` | every rule in use, as a model sees it |
| `validia rules explain RULE [-m MODEL]` | one rule: what it finds, its fix, how it matches, its cases, the files it was read from |
| `validia rules guidance -m MODEL` | the writing guidance for a model, target by target, with sources |
| `validia rules versions` | each category's releases and what changed in them |
| `validia rules test [--all]` | prove the rules and examples against their own cases |
| `validia rules extend RULE` | write a file in `rules/` that changes some fields or adds cases |
| `validia rules replace RULE` | copy a rule into `rules/` as a full definition, to edit |
| `validia rules disable RULE` | turn a rule off in `rules/` |
| `validia rules fallback RULE --to RELEASE` | keep one core rule as an older release had it |
| `validia rules new RULE` | add a rule of your own, asking for what is missing |

`RULE` is the rule's id, `wording/capitals`. `validia rules explain` also takes the name
alone, `capitals`, as long as it names one rule.
