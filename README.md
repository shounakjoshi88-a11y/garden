# garden

A daily engineering rhythm that shows up on the contribution graph.

## Why this exists

GitHub's contribution calendar counts **commits per day**, not lines changed.
Two consequences that hurt a real project:

1. You work in bursts. Two hundred commits land on one Saturday, then the graph
   is empty for three weeks. The work was real; the calendar cannot see it.
2. You batch. Forty files become one `fix: stuff` commit, so a day of serious
   work shows up as a single faint square.

`garden` fixes both, and it does it by producing **real commits with real
content** - test reports, lint results, dependency audits, metrics history,
engineering notes - rather than empty marker commits.

```
git commit --allow-empty -m "chore: update"   # a lie, and it shows in review
garden day                                    # six real commits, every day
```

## What it does

| Command | Purpose |
| --- | --- |
| `garden split` | Turns one messy batch of changes into several logical commits. |
| `garden day` | Runs the configured checks, writes the artifacts, commits each one. |
| `garden rhythm` | Shows the activity pattern the rhythm plans for a date range. |
| `garden backfill` | Fills days that are below their target with real, backdated reports. |
| `garden rebuild` | Rebuilds the whole history so every day has its planned shape. |
| `garden status` | Renders your contribution calendar locally. |
| `garden tasks` | Lists the available checks. |
| `garden install-actions` | Writes the GitHub Actions workflow. |

### The rhythm

A calendar where every day has exactly the same number of commits is its own
kind of suspicious. Real work comes in clusters: a day with one tidy commit, a
day where you could not stop, a day you spent on something else entirely.

`garden rhythm` decides, for each date, what that day should look like. It is
deterministic - the same date always plans the same day, so a rebuild is
reproducible - but it varies from day to day the way real activity does:

```
  2026-01-01   2  ##
  2026-01-02   2  ##
  2026-01-03   8  ########
  2026-01-04   6  ######
  2026-01-05  11  ###########
  2026-01-06   1  #
  2026-01-07   6  ######
  2026-01-08   4  ####
  ...
  2026-01-26  quiet
```

The shape comes from a weighted distribution, not a random number: mostly small
days, a long tail of bright ones, and the occasional day off - never two in a
row. The commit *times* vary too, spread across the working day with a bias
towards the evening, so the git log reads like a person rather than a cron
job.

Change `rhythm_salt` in the config to reshuffle which days are busy.

### `garden split`

The batch splitter. It classifies every changed file (path patterns first,
diff content second), groups them by conventional-commit type and directory,
and writes one commit per group with a real message.

```
$ garden split
3 commit(s) planned:

   1. [chore] chore(api): add delete_user
        src/api/auth.py
        src/api/users.py
   2. [docs] docs(docs): update readme.md
        docs/readme.md
   3. [test] test(tests): add test_delete_user
        tests/test_users.py
```

One sprint day now lands as three contributions instead of one. Use
`--dry-run` first; nothing is written until you drop the flag.

### `garden day`

The daily engine. It runs a bounded set of checks against your repositories
and commits each artifact separately, so a single day produces several
contributions - and each one is a file you can open and read.

Checks available:

| Task | Artifact |
| --- | --- |
| `metrics` | Lines of code, language mix, a daily row in `ops/metrics.csv` |
| `todos` | Every TODO/FIXME/HACK in the tree, with file and line |
| `tests` | The project's own test suite, run and summarised |
| `lint` | The project's own linter, run and summarised |
| `coverage` | Coverage summary, when the project already emits one |
| `deps` | Declared dependencies and what is behind latest |
| `changelog` | Conventional commits grouped into a changelog |
| `notes` | The daily engineering note (authored by you, or drafted) |
| `cards` | One knowledge card per day from a folder of your notes |

### `garden backfill`

Finds the days that are below their target and fills them. For each one it
runs the checks and commits the results with a backdated **author** date - the
same field GitHub's calendar reads.

The content is genuinely computed from your code. Only the timestamp moves.
This only ever adds new commits on top of the current branch; it never
rewrites or drops existing history.

### `garden rebuild`

Backfilling leaves the days that were already filled with the shape they had.
Rebuilding produces the whole history in one pass, so every day - including
the quiet ones - gets exactly the shape the rhythm planned.

It runs in a scratch clone. The live repository is untouched until the new
history has been built, and only then is it force-pushed over the default
branch.

```
garden rebuild --from 2026-01-01 --to 2026-06-30 --push
```

## Setup

```bash
git clone <this repo> && cd garden
pip install -e .

# create the ops repository and a config
garden init --ops-repo ./ops-repo --repo shounakjoshi88-a11y/meridian

# see what a day would produce
garden day --dry-run

# preview the calendar
garden status
```

### GitHub Actions

`garden install-actions` writes `.github/workflows/garden.yml` into the ops
repository. It runs three times a day, deliberately off the hour:

```yaml
- cron: "13 4 * * *"    # 09:43 IST
- cron: "37 12 * * *"   # 18:07 IST
- cron: "9 20 * * *"    # 01:39 IST
```

The workflow needs `contents: write`, which the built-in `GITHUB_TOKEN`
already has for the repository the workflow lives in.

**The hub always works on its own.** The workflow token cannot write to other
repositories, so without a personal token garden keeps every artifact in the
ops repository - around a dozen commits a day, which is enough for a solid
square. Nothing is wasted and nothing fails.

**To also write into your project repositories**, add a `GARDEN_PAT` secret: a
classic personal access token with `repo` scope
(Settings → Secrets and variables → Actions → New repository secret). The
workflow uses it when present, and garden then commits an `ops/` folder into
each monitored repository as well. Without it, garden says so and keeps
everything in the hub - a fleet push that cannot succeed is a configuration
gap, not a failed run.

### Configuration

`garden.config.json`:

```json
{
  "ops_repo": { "path": "." },
  "tasks": ["metrics", "todos", "notes"],
  "max_commits_per_day": 12,
  "author_name": "Your Name",
  "author_email": "you@example.com",
  "fleet": [
    { "name": "meridian", "clone": "owner/meridian",
      "tasks": ["metrics", "todos", "notes"], "write_ops_here": true }
  ]
}
```

`write_ops_here` puts the artifacts in the monitored repository's own `ops/`
folder. The same artifacts are always mirrored into the ops hub, so the hub
holds the complete history in one place.

`max_commits_per_day` is a hard budget. The engine runs checks lazily and
stops when the budget is spent, so a run never turns into fifty commits. Seven
or more commits a day puts you in GitHub's darkest green.

`author_email` must be a **verified** address on the account. GitHub
attributes a contribution to the account whose verified email authored the
commit - a bot identity produces commits that never appear on the graph.

Which check runs first for each repository rotates by day, so over a week
every check gets exercised instead of one metric eating the whole budget.

## Writing your own notes

The `notes` task looks for `notes/YYYY-MM-DD.md` in the monitored repository.
If you wrote it, that file is used verbatim. If you did not, a draft is
generated from the day's commits with the parts only you can write left
blank. An authored note always wins.

## Honest limits

- The artifacts are real but they are **generated by a tool**, not by you
  thinking. That is the same relationship a CI badge has to its project: it
  reports on the work, it does not replace the work.
- `garden backfill` moves timestamps. The analysis behind each backfilled day
  is real; the claim "I did this work on that date" is not. Use it to fill
  quiet stretches, not to claim a sprint you did not run.
- Nothing here fakes content. If a day has no real changes, `metrics` still
  records a genuine snapshot, but the history will show a tool's cadence
  rather than yours. That is the honest version of a green square, and it is
  also the one that survives an interviewer asking "what is this repo?"

## Development

```bash
pwsh -File scripts/smoke-test.ps1
```

Builds a throwaway sandbox and exercises every command.
