# Records docs/demo-claude-code.gif: impact analysis through the /kerplunk skill in Claude Code.
# Needs Claude Code installed and signed in, and the skill installed for this project:
#   uv run .kodex/scripts/prepare_example.py
#   uv run kerplunk build examples/jaffle_shop
#   uv run kerplunk install --project examples/jaffle_shop
#   vhs .kodex/scripts/demo-claude-code.tape
# The model's answer varies between runs; record until the take is clear, then commit the GIF.
Output docs/demo-claude-code.gif

Set Shell "bash"
Set FontSize 15
Set Width 1280
Set Height 820
Set Padding 18
Set Theme "Builtin Dark"
Set TypingSpeed 35ms

Hide
Type "export PATH=$PWD/.venv/bin:$PATH && cd examples/jaffle_shop && clear"
Enter
Type "claude"
Enter
Sleep 4s
Show

Type "/kerplunk what breaks if I change how stg_payments.amount is calculated?"
Sleep 500ms
Enter
Wait+Screen@120s /customer_dashboard/
Sleep 12s
