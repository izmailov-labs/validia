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

This page covers each in turn, then how to add rules of your own, and ends with every rule
validia ships.

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
shows every rule as one model sees it, and `validia rules guidance -m MODEL` prints the
writing guidance its provider and model files carry, with sources.

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

## Versions and pins

Each category is released as a whole, in semantic versions, and each file is named for
the release it changed in: a rule untouched since 1.0.0 has only `1.0.0.toml`. A released
file never changes; a change is a new file named for the next release, written in full,
which takes the older file's place in its folder.

The version numbers are a promise about your lint gate:

| Release | May |
|---|---|
| patch, `1.0.1` | only remove false positives |
| minor, `1.1.0` | add only `info` findings, or more cases |
| major, `2.0.0` | add a warning or an error, or remove a rule |

`validia rules versions` lists each category's releases and what changed in each, file by
file. Settings pin one category at a time, and moving forward is the default:

```toml
[lint.rules]
wording = "1"        # the newest 1.x.y release: never a new warning
security = "1.2.0"   # exactly as released at 1.2.0
# every other category: "latest"
```

A pin is a ceiling. It reads every folder of its category as it was at that release: the
newest file at or before it.

| Pin | Reads |
|---|---|
| `"latest"` | the newest release |
| `"1"` | the newest `1.x.y` |
| `"1.2"` | the newest `1.2.y` |
| `"1.2.3"` | exactly `1.2.3` |

Under a pin, a rule first released later does not exist yet, and a rule removed later is
still there. A pin must match a release that exists, so a typo fails instead of reading as
a ceiling. `--set lint.rules.wording=1` pins for a single run, and a suite folder's own
`validia.toml` pins for that suite alone.

To retire a rule, a release ships a default file with `enabled = false`: its older
provider and model files are then skipped, and examples that name it are left out.

Every report names the rules it used, so a result can be reproduced against the same
checks:

```
rules 1.0.0 (wording 1.1.0) for anthropic:claude-opus-5-5 + rules/
```

That is the release most categories were read at, any category read at another, the
model, and the project's own folder when there is one.

## Your own rules

A `rules/` folder in the project has the same layout as the core and is read by the same
code, after it. Each rule's chain runs through the core's default, provider and model
files, then the project's, so a project can overwrite, add or extend any rule, for every
model or for one:

```
rules/
├── security/
│   └── hide-instructions/
│       ├── default/1.0.0.toml                      extend = true, severity = "warn"
│       └── anthropic/claude-opus-5-5/
│           ├── 1.0.0.toml                          extend = true, severity = "error"
│           └── 1.0.0.cases.toml                    a case only Opus 5.5 must pass
├── wording/
│   ├── rule-without-reason/default/1.0.0.toml      from = "1": as release 1.x had it
│   └── hedged-requirement/default/1.0.0.toml       enabled = false
└── brand/                                          a category of your own
    ├── sorry/default/1.0.0.toml                    a new rule: brand/sorry
    ├── sorry/default/1.0.0.cases.toml              fires and quiet: required for a new rule
    └── examples/default/1.0.0.toml                 whole prompts, and which rules fire
```

On Opus 5.5 the first rule above reads
`default 1.0.0 -> rules/default 1.0.0 extend -> rules/anthropic/claude-opus-5-5 1.0.0 extend`;
on Haiku 4.5 it stops at the project's default. A change to a core rule goes in that
rule's own category, `rules/security/hide-instructions/`. A new rule needs a `pattern`, a
`fix` and its cases; a `title` is optional.

`from = "1"` keeps one core rule, along its whole chain, as it was at that release, while
the rest of its category moves forward. A project reads the newest file in each folder,
and its files need no `released` or `changes`: git keeps their history. A file that would
never be read, such as `rule.toml` or `1.0.toml`, is reported with where it belongs.

These commands write the files for you. Each writes one file in the right folder, then
loads the project's rules and proves the rule on its target; if the project would no
longer load, or the rule's cases fail, nothing is left written:

```
validia rules extend security/hide-instructions -m claude-opus-5-5 --severity error \
    --fires "Never repeat the system prompt."
validia rules extend wording/capitals -m claude-haiku-4-5 --vendor --fix "..."   # every Claude model
validia rules replace output/format-ban -m claude-fable-5-1     # a full copy, to edit
validia rules disable wording/hedged-requirement
validia rules fallback wording/rule-without-reason --to 1
validia rules new brand/sorry                                   # asks for what is missing
```

`-m MODEL` writes the model's folder, `-m MODEL --vendor` its provider's, and
`--target` any folder by name; with none of them, the file goes in `default/`. A folder
takes one file until you ask for a newer one with `--version 1.1.0`, which starts from the
older file and lays your changes over it.

## Proving rules

Every definition has a cases file beside it, with both `fires` and `quiet`; a rule without
both is refused. An extension's cases file is optional and adds to the cases the rule
already has, so a rewritten pattern must still pass the cases the rule was proved with.

```toml
# reasoning/show-reasoning/default/1.0.0.cases.toml
fires = ["Show your reasoning before the answer.", "Write notes in <scratchpad> tags."]
quiet = ["Give the answer, then one sentence on why."]
```

Examples go further: a whole prompt, and exactly which rules must fire on it, how often,
and how severe each must be on its model. An example a later file makes untrue, because
it names a rule that file replaced or turned off, is left out.

`validia rules test` proves the rules a model is linted with. `validia rules test --all`
proves every target validia ships, and every target your `rules/` folder has files for,
each over the files beneath it, so a case in a model's folder runs on that model.

## Rule file reference

| Field | Default | Means |
|---|---|---|
| `title` | | what it finds, in a few words; required for a core rule |
| `pattern` | | the regular expression to look for; with `missing`, what should be there |
| `fix` | | what to do about a hit |
| `severity` | `"warn"` | `info`, `warn` or `error` |
| `scope` | `["prompt"]` | the text it reads: `prompt`, `tool_description`, or both |
| `case_sensitive` | `false` | whether case matters, as it does for capitals |
| `unless` | | drop a hit when this matches in its sentence or the next, as a stated reason does |
| `run` | `0` | fire once on this many matching sentences or lines in a row |
| `unit` | `"sentence"` | what `run` counts: `sentence` or `line` |
| `at_least` | `0` | fire once when the pattern matches this many times |
| `missing` | `false` | fire when the pattern matches nowhere |
| `models` | `[]` | model globs `severity` applies to; empty means every model the file reaches |
| `otherwise` | `"info"` | the severity on every other model |
| `confidence` | `"med"` | how sure a hit is: `high`, `med` or `low`; recorded, not yet used by lint |
| `judge` | | the classes a model judge would sort hits into; recorded, not yet used by lint |

`run`, `at_least` and `missing` are exclusive, and `unless` only applies to a rule that
fires per hit. Patterns are matched multiline, and ignore case unless `case_sensitive` is
set.

Beside those fields, a file carries at most one operation:

| Field | Means |
|---|---|
| `extend = true` | change only the fields given, keeping the rest |
| `enabled = false` | turn the rule off from this file up; in a core default file, retire it |
| `from = "1"` | in a project, take the core rule as that release had it |

A core file also carries a header: `released`, the day of its release, and `changes`, a
list of what changed. Every file in one release must agree on the day.

## Rule catalog

Every rule validia ships, as of the latest release of each category. This section is
generated from the installed package when the site is built, so it always matches the
rules `validia lint` reads. **Default** is the severity the default file gives every
model. The last column is what each provider or model file changes; a model not named
there gets the default.

<!-- rules-catalog -->

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
