# Records docs/demo-cli.gif: build the example graph, then ask what a change would break.
# Run from the repository root after `uv sync` and `uv run .kodex/scripts/prepare_example.py`:
#   vhs .kodex/scripts/demo-cli.tape
Output docs/demo-cli.gif

Set Shell "bash"
Set FontSize 15
Set Width 1280
Set Height 760
Set Padding 18
Set Theme "Builtin Dark"
Set TypingSpeed 40ms

Hide
Type "export PATH=$PWD/.venv/bin:$PATH PS1='$ ' && cd examples/jaffle_shop && rm -rf kerplunk-out && clear"
Enter
Show

Type "kerplunk build"
Enter
Sleep 4s

Type "kerplunk impact stg_payments.amount"
Enter
Sleep 7s
