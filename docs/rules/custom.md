# Your own rules

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
