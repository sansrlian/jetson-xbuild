@AGENTS.md

## Claude Code

- Run board recordings and image or sysroot builds with `run_in_background`; a board reset shows up as a lost
  connection, which jxb explains.
- Put files the user has to run (sudo steps) in `.work/`: the user's terminal can't see the session scratchpad, and
  commands typed into the terminal panel by a tool may get lost. To wait for the result, run an `until` loop in the
  background that checks for the file the script produces.
- The built-in browser can't load `127.0.0.1` URLs in `ui.perfetto.dev`. Open timelines from a local page that
  embeds Perfetto in an iframe and sends the trace with `postMessage`, or let the user open the file.
