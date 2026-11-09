# The GitHub Action

Konstruct is also a GitHub Action. It reviews every pull request column by column, posts the review as a comment (updated in place on each push), writes it to the job summary, annotates each breaking change, and fails the check when a change removes a column that something downstream still reads:

```yaml
# .github/workflows/konstruct.yml
name: konstruct
on: pull_request
permissions:
  contents: read
  pull-requests: write
jobs:
  lineage:
    runs-on: ubuntu-latest
    steps:
      - uses: actions/checkout@v7
      - uses: kodexlabsio/konstruct@v0
```

| Input | Default | Purpose |
|-------|---------|---------|
| `working-directory` | `.` | Where the dbt project, SQL or DAGs are |
| `base` | the pull request's base branch | Commit to compare with |
| `comment` | `true` | Post the review on the pull request |
| `fail-on-breaking` | `true` | Fail the check on a breaking change |
| `build-args` | `--no-warehouse --no-dbt` | Arguments for `konstruct build`. The default needs no credentials and never runs the pull request's dbt macros |
| `extras` | none | Konstruct extras, such as `snowflake` |

The outputs `breaking`, `summary`, `report` (markdown) and `json` let later steps act on the result. CI runs the action against a breaking change and a safe one on every commit. Pin a release (`@v0.7.0`) for a fixed version; `@v0` moves to each new 0.x release. [`docs/examples/pull-request.yml`](../examples/pull-request.yml) shows the same steps written out, for adding a `dbt parse` or other setup first.
