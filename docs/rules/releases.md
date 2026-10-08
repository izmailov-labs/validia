# Rule versions

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


## Release history

Every release of every category, newest first. `validia rules versions` prints the same.

<!-- rules-releases -->
