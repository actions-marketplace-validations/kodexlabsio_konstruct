# Python API

```python
import konstruct

project = konstruct.Project('path/to/repo')
project.build(update=True)

walk = project.impact('stg_payments.amount')
print(walk.summary())  # 7 columns, 3 relations, 5 tests, 1 exposure and 2 tasks downstream.
for column in walk.columns:
    print(column.label, column.transform, column.path)

print(project.trace('customer_ltv.lifetime_value'))  # the same text the CLI prints
project.describe('orders').columns  # typed results
project.explain('orders.amount').composed  # "SUM(raw_payments.amount / 100)"
project.conflicts().conflicts[0].differences  # what two revenue columns disagree on
project.define('lifetime value').terms[0].owner  # {"name": "Priya Shah", "role": "Head of Finance"}
project.path_between('raw_payments', 'customer_dashboard').found
project.search('lifetime value', kinds=['column']).results
project.list(['model']).items
project.changed_inputs()  # files changed since the last build
project.diff(base='origin/main').breaking  # removed columns still read downstream
project.verify(seed=7).totals()  # {"claimed": 36, "confirmed": 36, "unexplained": 0, ...}
```

Results are dataclasses (`Walk`, `PathResult`, `SearchResult`, `Description`, `Listing`, `QueryResult` made of `Item`s, plus `Definitions` of `TermDefinition`s, `Explanation`, `Diff` and `Verification`). `str(result)` gives the CLI's text and `result.to_dict()` the JSON the CLI and MCP server return. A name that matches nothing, or several things, raises `konstruct.QueryError` with suggestions. `project.graph` is the underlying `konstruct.Graph` for anything the API does not cover.
