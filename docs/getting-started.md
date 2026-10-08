# Getting started

## Using validia

```bash
uv add validia
```

```python
import validia

print(validia.__version__)
```

## The `validia` command

```bash
validia init                      # validia.toml and an example suite
validia create                    # a suite of your own: prompt, grader, cases
validia add evals/ticket-triage/suite.toml       # add a test case, one question at a time
validia check evals/ticket-triage/suite.toml outage-login --reply "Urgent."
validia config                    # every setting, and where its value came from
validia run evals/ticket-triage/suite.toml -m claude-sonnet-5-5 --dry-run
validia run evals/ticket-triage/suite.toml -m claude-sonnet-5-5 -n 3   # call the model
```

`init` asks three questions -- the folder for suites, the answer type, and the
example suite's name -- and Enter keeps each default. `--evals DIR`, `--answer TYPE`
and `--name NAME` answer them up front, and without a terminal (CI, scripts) the
defaults are used without asking. An existing `validia.toml` is kept, so running
`init` again adds another suite:

```
validia.toml
evals/
└── ticket-triage/
    ├── suite.toml      # the grader and the cases, with their expected answers
    ├── prompt.md       # the prompt under test; the model never sees suite.toml
    └── validia.toml    # optional: settings for this suite alone
```

### Creating a suite

`validia create` builds a suite of your own from questions, with numbered options
wherever there is a choice:

```
$ validia create
Folder for eval suites [evals]:
Suite name: agent-call
What does the prompt answer with?
  1  label  a category from a fixed list
  2  json   a JSON object
  3  text   free text
  4  tool   a call to one of its tools
Choose 1-4 [1]: 4
```

It then asks for what grading needs -- the labels, the keys every JSON reply must
have, or the tools (described on the spot, or an existing JSON file) -- and for the
prompt: built from questions, typed in, an existing file the suite points at (so it
tests the prompt you ship), or a placeholder to fill in later.

Building the prompt asks one question per section -- who the model is, its job, what
it needs to know, its rules with their reasons, and for free text, its tone -- then
offers a closing "how to answer" instruction that fits the answer type and stays
within what the grader accepts. As you type, core checks point out weak instructions
without blocking them:

```
A rule to follow, with its reason (empty to finish): NEVER guess
  hint caps: capitals add pressure, not meaning: say it plainly, and say why
  hint reason: give the reason (because ..., so that ...): a rule with its reason covers cases it never names
  hint forbid-only: say what to do instead: a rule that only forbids leaves the model guessing what is wanted
Keep it as it is? [y/N]:
```

The sections, the checks and the closing instructions are all data in a TOML template.
`validia template > prompt.toml` saves the built-in one into the project, and from then
on `create` builds from that file: change a question, add a check, reword an
instruction. Then the cases, one at a time, in the
answer type's shape. Nothing is written until the last answer, and only if the suite
loads; `--evals`, `--answer` and `--name` answer their questions up front.

### From code, or a REST API

Everything the command does is a function in the `validia` package, with no terminal
attached: the console is one front end, and a REST service, a web form or a notebook
can be another. Specs come from plain JSON, results go back with `dataclasses.asdict`
or `describe_suite`, and every failure is a `SpecError` whose `problems` list is ready
to return as a 422.

```python
from dataclasses import asdict
from pathlib import Path

from fastapi import FastAPI, HTTPException  # any web framework; not a validia dependency

import validia

app = FastAPI()
PROJECT = Path(".")


@app.post("/suites")
def create(body: dict) -> dict:
    try:
        suite = validia.create_suite(validia.SuiteSpec.from_dict(body), PROJECT)
    except validia.SpecError as exc:
        raise HTTPException(422, exc.problems) from exc
    return validia.describe_suite(suite, PROJECT)


@app.post("/suites/{name}/cases")
def add(name: str, body: dict) -> dict:
    try:
        case = validia.CaseSpec.from_dict(body)
        suite = validia.add_case(validia.locate_suite(PROJECT, name), case)
    except validia.SpecError as exc:
        raise HTTPException(422, exc.problems) from exc
    return validia.describe_suite(suite, PROJECT)


@app.post("/suites/{name}/cases/{case_id}/check")
def check(name: str, case_id: str, body: dict) -> dict:
    try:
        suite = validia.load_suite(validia.locate_suite(PROJECT, name))
        verdict = validia.check_reply(suite, case_id, validia.parse_reply(body))
    except validia.SpecError as exc:
        raise HTTPException(422, exc.problems) from exc
    return asdict(verdict)


@app.get("/prompt-questions")
def questions(answer: str = "label", labels: str = "") -> list[dict]:
    grade = validia.Grade(answer, labels=tuple(filter(None, labels.split(","))))
    return [asdict(q) for q in validia.prompt_questions(validia.default_template(), grade)]
```

Take a suite's name from a URL through `locate_suite`, never by joining paths: it
refuses a name such as `..` that would reach outside the project, and `create_suite`
likewise refuses a `prompt_file` or `tools_file` that leads outside it, symlinks
included.

A form renders the `Question` objects -- each has an `id`, `text`, `kind` (`text`,
`many` or `choice`), `options` and an `example` -- `review_answer` returns the core
checks' hints for one answer as it is typed, and `render_prompt` turns the submitted
answers into the same prompt the console builds.

### Answer types

A suite's `[grade] type` says what the prompt answers with, and each case's
`expected` takes the matching shape. Every check is code, and an empty reply always
fails.

| Type | The reply is | A case's `expected` |
| --- | --- | --- |
| `label` | a category from `labels` | `"urgent"` |
| `json` | a JSON object | `{ category = "bug", priority = "high" }`; `required` in `[grade]` lists keys every reply needs |
| `text` | free text | `{ contains = ["Export"], not_contains = ["Import"], matches = '(?i)zip' }`, and `equals` |
| `tool` | a call to one of the suite's tools | `{ tool = "lookup_order", args = { order_id = "48213" } }`, or `{ tool = "none" }` |

A `tool` suite names its tools in a JSON file (`tools = "tools.json"`) with
`name`, `description` and `parameters` (a JSON Schema), the same fields franca's
`ToolDef` takes. `init` writes one example per type.

### Adding and checking cases

`validia add SUITE` asks for a case's id, input and tags, then for the expected
answer in the suite's shape -- a label, one value per JSON field, text checks, or a
tool and its arguments -- appends it to `suite.toml`, and lets you try replies
against it. A case that would break the suite is never written.

`validia check SUITE CASE` grades one reply you supply, without calling a model:
the quickest way to test a regex or a contains list before any run. It exits 0 when
the reply passes and 1 when it fails. The reply comes from `--reply`, or `--tool`
and `--args` for a tool call, and otherwise from the terminal or standard input.

### Checking prompts: `validia lint`

`validia lint` checks prompt files, or whole suites -- their prompt and every tool
description -- against the rules for a model, without calling it:

```
$ validia lint evals/billing/prompt.md -m claude-sonnet-5-5
evals/billing/prompt.md:2:1  info   wording/capitals  'NEVER'  State the one real constraint plainly, ...
evals/billing/prompt.md:2:1  warn   wording/rule-without-reason  'NEVER'  Give the reason (because ..., so that ...): ...
evals/billing/prompt.md:3:1  error  reasoning/show-reasoning  'Show your reasoning'  Remove it: these models refuse ...
rules 1.0.0 for anthropic:claude-sonnet-5-5: 3 findings (1 error, 1 warn, 1 info)
```

It exits 1 when a finding reaches `--fail-on` (`lint.fail_on`). Which rules apply, and
how severe each is, depends on the model, the provider that serves it and the release of
the rules you pin. [Prompt rules](rules.md) covers all three, how to add rules of your
own, and lists every rule validia ships.

### Models and API keys

`model` is `provider:model`, as in `anthropic:claude-sonnet-5-5`. The provider may be
left out when the name says it: `claude-`, `gpt-`, `gemini-`, `grok-` and `deepseek-`.

API keys never go in a settings file. They are read from the environment when a run
calls the model: the variable `api_key_env` names, then `FRANCA_<PROVIDER>_API_KEY`,
then the vendor's own (`ANTHROPIC_API_KEY`, `OPENAI_API_KEY`, ...). The rest of a
provider's access is franca's own `[providers.<name>]` table:

```toml
[providers.anthropic]
api_key_env = "TEAM_ANTHROPIC_KEY"   # the variable that holds the key; never the key
base_url = "https://gateway.example/v1"
timeout_s = 60
```

The simplest place for keys is a `.env` file in the project, which `.gitignore`
already keeps out of the repository:

```
ANTHROPIC_API_KEY=...
OPENAI_API_KEY=...
GEMINI_API_KEY=...
```

A key exported in the shell outranks the same key in `.env`, and an empty variable
counts as unset. `validia config --keys` lists every provider and where its key comes
from -- `anthropic  key from ANTHROPIC_API_KEY in .env:2` -- or what to set, and never
prints a key itself. `run --dry-run` does the same for the model it would call, before
anything is spent.

### Per-suite settings

A `validia.toml` in a suite's folder outranks the project's for that suite, so one
suite can use another model, more reps or a different provider.
`validia config --suite SUITE` shows settings as that suite sees them.

### Running a suite: `validia run`

`validia run` sends every case to the model, `--reps` times each, and grades every reply
with the suite's own check -- the same one `validia check` uses:

```
$ validia run evals/ticket-triage/suite.toml -m claude-sonnet-5-5 -n 3
ticket-triage on anthropic:claude-sonnet-5-5: 18 of 24 passed, 75.0% (95% interval 55.1%-88.0%)
  served by claude-sonnet-5-5-20261001
  by group: urgent 6/12, normal 12/12
  failing:
    data-loss-invoices      0 of 3  expected 'urgent', got 'normal'
    security-unknown-login  0 of 3  expected 'urgent', got 'normal'
  tokens: 2,880 in, 48 out
  latency: p50 820 ms, p95 1,900 ms
  written to .validia/runs/20261007T143012Z-ticket-triage/
```

The suite's prompt is the system text and each case's input the user turn. Repetitions
make the pass rate an estimate, so it comes with a 95% interval: with eight cases and one
rep, 75% means "somewhere between 41% and 93%". A call that fails -- a rate limit that
outlives `--retries`, a bad key -- is counted as an error, never as a wrong answer, and
fails the run. A failure every trial would hit the same way -- a bad key, a retired model
-- stops the run at the first one: calls already in flight finish, and nothing more is
sent. `--fail-under 90` (`run.fail_under`) also fails it when the pass rate is
below 90%, for CI. Every trial -- reply, tokens, latency, attempts, served model -- is
written to `trials.jsonl`, and the totals to `summary.json`, in a folder of the run's own
under `run.output`.

Calling a model needs an HTTP client: `pip install 'validia[http]'`. The key comes from the
environment or the project's `.env`, as `validia config --keys` shows. `--dry-run` calls
nothing: it validates the suite, reporting every problem in it at once, checks model
access, and prints what would run. Suites with tools are refused for now: franca, the
layer validia calls models through, sends text turns only in its current release, so the
tools would never reach the model.

Settings resolve through [whence](https://github.com/izmailov-labs/whence). Highest
precedence first:

| Source | Example |
| --- | --- |
| A command's own flags | `validia run suite.toml --reps 3` |
| `--set KEY=VALUE` | `validia config --set run.reps=3` |
| Environment variables | `VALIDIA_RUN__REPS=3` (`__` separates sections) |
| `.env` in the working directory | `VALIDIA_RUN__REPS=3` |
| A settings file | `--config FILE` or `$VALIDIA_CONFIG`, then the suite folder's `validia.toml`, then the project's |
| `pyproject.toml` | a `[tool.validia]` table |
| Defaults | what `validia init` writes |

`-p NAME` activates a profile, overlaying `validia.NAME.toml` on `validia.toml`. A
misspelled key or an out-of-range value fails with the file it came from and a
suggestion, and `validia config --discovery` lists every place that was searched.

## Local development

The repository is a single package managed with [uv](https://docs.astral.sh/uv/) (0.12 or
newer). Every command below runs from the repository root.

```bash
git clone https://github.com/izmailov-labs/validia
cd validia
make install     # sync dev + docs groups, install pre-commit hooks
```

```
validia/
├── pyproject.toml        # package metadata and the lint, type, test and coverage gate
├── src/validia/          # the package on PyPI
│   ├── suites/           # suites, graders, suite files, example suites, the suite API
│   ├── prompts/          # the guided prompt builder and its template
│   ├── rules/            # the lint engine, the core rules (core/), project rule files
│   ├── runs/             # model access, and running a suite against a model
│   └── cli/              # the validia command, its questions and its settings
├── tests/
└── docs/                 # this site
```

validia depends on two sibling packages, [`franca`](https://github.com/izmailov-labs/franca)
and [`whence`](https://github.com/izmailov-labs/whence), which resolve from PyPI like any
other dependency.

Common tasks:

| Command | What it does |
| --- | --- |
| `make lint` | `ruff check` + `ruff format --check` |
| `make fmt` | Autofix and format |
| `make typecheck` | `mypy` in strict mode |
| `make test` | Run the test suite |
| `make cov` | Tests with coverage (fails under 90%) |
| `make docs` | Build these docs with `--strict` |
| `make build` | Build the sdist + wheel and validate metadata |
| `make all` | Everything CI runs |

Tests run under `pytest-asyncio` in auto mode, so `async def test_*` functions
need no decorator.
