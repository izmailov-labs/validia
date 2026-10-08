# Core rules

One folder per rule, inside its category. Inside a rule's folder, one folder per
target: who the file is for.

```
<category>/
├── <rule>/
│   ├── default/1.0.0.toml                  the rule, for every model
│   ├── default/1.0.0.cases.toml            text it must fire on, and stay quiet on
│   ├── <provider>/default/1.0.0.toml       every model of one vendor
│   └── <provider>/<model>/1.0.0.toml       one model
├── examples/<target>/1.0.0.toml            whole prompts, and exactly which rules fire
└── guidance/<target>/1.0.0.toml            how to write for that target
```

The folder is the rule's name, and its id is its category and name
(`reasoning/show-reasoning`): findings, `validia rules explain` and examples use it. Inside a
category's examples, the name alone is enough. Targets use franca's provider slug and
the exact model id: `anthropic/default`, `anthropic/claude-opus-5-5`.

## How files stack

For a model, each rule is read along its chain, most general first:

```
default  ->  <provider>/default  ->  <provider>/<model>
```

A missing folder is skipped. Each file is laid over the one before it:

| File says | It does |
|---|---|
| a full definition (`pattern`, `fix`, ...) | defines the rule; above the default, replaces it |
| `extend = true` and some fields | changes those fields, keeps the rest; its cases are added |
| `enabled = false` | turns the rule off from here up |

A default file defines its rule and never names `models`: model severities go in a
vendor or model file. In an extension, `severity` alone applies to every model;
`severity` with `models` applies to those, and every other model keeps what it had. A
rule folder with no `default/` is a rule only for the models that have a file.

Every definition has a cases file beside it, with both `fires` and `quiet`. An
extension's cases file is optional and adds to the cases the rule already has, so a
changed pattern must still pass the cases the rule was proved with.

## Releases

A category is released as a whole, and each file is named for the release it changed
in: a rule untouched since 1.0.0 has only `1.0.0.toml`. A released file never changes;
a change is a new file named for the next release.

- a **patch** release only removes false positives;
- a **minor** release adds only `info` findings, or more cases;
- a **major** release can add a warning or an error, or remove a rule.

A newer file takes the older one's place in its folder; it does not add to it, so it is
written in full. A pin (`lint.rules.wording = "1"`) reads every folder as it was at that
release: its newest file at or before it. A rule first released later does not exist yet.
To retire a rule, release a default file with `enabled = false`: its older vendor and model
files are then skipped, and examples that name it are left out. A file released at or after
the retirement that still extends it is an error.

## Projects

A project's `rules/` folder has this same layout, and is read by the same code, after
the core: each rule's chain runs through the core's default, vendor and model files,
then the project's. So a project can overwrite, add or extend any rule, for every model
or for one:

```
rules/<category>/<rule>/default/1.0.0.toml               every model
rules/<category>/<rule>/default/1.0.0.cases.toml
rules/<category>/<rule>/<provider>/<model>/1.0.0.toml    one model
rules/<category>/examples/<target>/1.0.0.toml
```

A project file can also say `from = "1"`: one core rule, along its whole chain, as it
was at that release. A rule the core does not have is a new rule, and a category it does
not have is a new category. A project reads its newest file in each folder, and its
files need no `released` or `changes`.

Prove everything, the project's files included, with `validia rules test --all`; `validia rules explain <rule>` shows
which files a rule was read from.
