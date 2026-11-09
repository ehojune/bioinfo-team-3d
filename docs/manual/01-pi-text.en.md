[← Manual index](../manual.en.md)

## What the PI sees

`labhq` rewrites every notice the PI reads in the web and mobile inbox and in messengers into easy plain Korean (`labhq/pi_language.py`), and checks the style of reports and summaries (`labhq/korean_qc.py`, built-in `humanizer` skill). The check does not block publishing: when a must-be-zero expression such as translationese or a clipped statistics term remains, it is recorded as WARN under `korean_qc` in the edition record and publishing goes on (#1336).

- Approval cards, questions, messenger messages and report notifications are shown as plain text in the order `what it wants to do → why → what happens if approved / when finished`.
- Card summaries strip Markdown headings, bold and table marks. The command and the original code stay in the card's and messenger's "Details" fold.
- Tool, compute-server, budget and hiring cards show a one-line description, the reason and the result. The plain-language summary never changes the real approval authority or the command that runs.
- Simple shell and file commands are summarized by rules. For compound commands, the staff member's own explanation and the original command are both kept, and labhq never asserts on its own that nothing outside the work folder is affected.
- If a staff member answered in English, the card does not pass off a machine translation as the original: the Korean notice and the English original are shown separately.
