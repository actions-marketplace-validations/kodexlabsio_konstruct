#!/usr/bin/env bash
# Prepares /tmp/konstruct-diff-demo for demo-diff.tape: a git copy of the jaffle_shop example
# with a branch that changes an expression, renames a column still read downstream, adds a
# filter and reformats a model. Run from the repository root after prepare_example.py.
set -euo pipefail
DEMO=/tmp/konstruct-diff-demo
rm -rf "$DEMO"
cp -R examples/jaffle_shop "$DEMO"
cd "$DEMO"
rm -rf konstruct-out logs
printf 'target/\nlogs/\nkonstruct-out/\n*.duckdb\n' > .gitignore
git init -q -b main
git add -A
git -c user.name=demo -c user.email=demo@example.com -c commit.gpgsign=false commit -qm "base"
"$OLDPWD/.venv/bin/konstruct" build -q > /dev/null
python3 - <<'PY'
from pathlib import Path
payments = Path("models/staging/stg_payments.sql")
text = payments.read_text()
text = text.replace("payment_method,\n", "payment_method as method,\n")
text = text.replace("amount / 100 as amount", "amount / 100.0 * 1.2 as amount")
payments.write_text(text)
orders = Path("models/orders.sql")
orders.write_text(orders.read_text().replace("from orders\n", "from orders\n    where orders.status != 'returned'\n", 1))
customers = Path("models/staging/stg_customers.sql")
customers.write_text(customers.read_text().replace("    select\n", "    SELECT\n"))
PY
