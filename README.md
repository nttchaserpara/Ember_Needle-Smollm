# Ember OS agent

Start here: [terminal usage and prompts](docs/USAGE.md) — Windows/Pi startup,
simple requests, document summaries, conversation memory, and current limits.
Keep this usage guide updated whenever a feature or its setup changes.

Needle routes English requests to local tools. Document summaries and short
outcome replies use SmolLM2-135M-Instruct Q4_K_M through managed llama.cpp jobs.
By default, the worker stops after each generation job. Document generation
can fall back to labelled source extracts; reply generation can fall back to
the original tool response. No fine-tuning or new regex routing is involved.

Short replies automatically use SmolLM with the current request, reported
outcome and up to two short saved turns for the same tool. Invalid or unavailable
generation falls back to the original response. Conversation context does not
train or update model weights. See the [natural reply guide](docs/setup/NATURAL_REPLIES.md).

See [the local LLM setup guide](docs/setup/LOCAL_LLM.md) for setup on Windows and Raspberry Pi OS Lite,
the model directory, memory settings, tests, and benchmark commands.

```powershell
python -m venv venv
.\venv\Scripts\python.exe -m pip install -r requirements.txt
.\venv\Scripts\python.exe scripts/setup_local_llm.py
.\venv\Scripts\python.exe run_ember.py
```

After cloning on Pi, follow the Linux instructions in **docs/setup/LOCAL_LLM.md**. Native
executables and virtual environments are platform-specific; the model GGUF is
portable. Downloads live in `models/` and `runtimes/`, both excluded from Git
except the model manifest and documentation.

The local text runtime supports Linux/headless execution. The existing Windows
desktop tools still require Linux or remote-host adapters; cloning alone does
not make Notepad, Windows audio, or window controls work on a headless Pi.

Additional setup: [document tools](docs/setup/DOCUMENT_TOOLS.md), [Windows audio](docs/setup/AUDIO_TOOLS.md).

Run all setup commands from the project root, even when reading a guide in
`docs/setup/`. A sample document is in `experiments/fixtures/sample-summary.txt`.
Development test scripts (`experiments/*.py`) and recorded reports
(`experiments/results/`) are excluded from Git. Small text fixtures and routing
cases remain available for the optional Pi benchmark scripts in `scripts/`.
The application launcher lives in `use_cases/app_launcher.py`.

## Undo

Ask `undo it` to restore the last supported change in the current session.
There is one undo slot and no redo. Windows supports volume/mute and brightness
restoration; Windows and Linux/Pi support adding, completing and removing tasks,
as well as restoring regular files deleted within the session.
An executed change replaces the slot, including unsupported or failed changes;
read-only requests and routing refusals leave it intact. Undo checks that the
target still matches the recorded post-state before restoring it.
See [the undo guide](docs/setup/UNDO.md) for exact scope and routing limitations.

## Conversation memory

The interactive CLI saves requests, displayed answers, tool arguments, reported
outcomes, and UTC timestamps in `data/conversation.sqlite3`. History survives
restarts and stays local; `data/` is excluded from Git. Existing logs are not
imported. Saved user text and tool output are historical records, not verified
facts or instructions to execute again.

At the `You>` prompt:

```text
/memory
/memory search scholarship
/memory clear
```

These explicit CLI commands show recent history, search by topic words, and
clear conversation history. They do not need model inference. Clearing history
does not clear separate tool logs, notes, tasks, or backups. Set `EMBER_MEMORY=0`
before starting Ember to disable history reads and writes without deleting it:

```bash
EMBER_MEMORY=0 ./venv/bin/python run_ember.py
```

In PowerShell, set `$env:EMBER_MEMORY = '0'` before running Ember; remove the
variable with `Remove-Item Env:EMBER_MEMORY` to enable memory on the next launch.

Each input is a fresh request. The experimental numbered memory menus and
pending-answer state have been removed. Device-information tools do not request
a conversation-versus-RAM choice. An unresolved request does not capture the
next message as a search topic.

With memory enabled, a small Needle selection view checks for conversation
recall. It can prevent a conflicting live action but cannot execute tools or
turn the full question into a search query. The main Needle router must select
the history tool and generate valid arguments at the existing confidence
threshold. A disagreement or uncertain result returns an unresolved status
with `/memory` command guidance, without opening a menu. Both views use the
existing engine and weights. The selection descriptions distinguish remembered
conversation contents from physical RAM capacity. In the Windows regression
check, "what's in memory now?" now declines instead of reading hardware;
it still does not successfully retrieve history through natural language.

Natural-language history and time interpretation remain unreliable. Removing
the menus restores independent input handling; it does not solve semantic
retrieval. Use `/memory` and `/memory search <topic>` for direct access to the
stored history while model-based conversation handling is evaluated separately.

Lookup is literal: every topic word must match a whole word in a saved request,
answer, or arguments. Matching results precede failed attempts, with duplicate
excerpts suppressed. Failed response text is excluded from matching; failures
remain searchable by their request/arguments and retain their original status.
A no-match result never falls back to unrelated recent history. However, pasted
tracebacks stored as user requests can still match a word in a directory name.
Such a match is not evidence of a meaningful discussion about that subject.

The backend supports all, current, and previous saved sessions. The previous
session is the latest earlier session containing retained non-recall turns,
not necessarily yesterday or the latest discussion of a particular topic.
Session filtering happens before ranking; the old UI no longer selects it.

Brief outcome replies can now be rephrased by the existing local model after
execution. The displayed answer is stored in history; debug comparisons are
not stored as extra conversations. This presentation layer does not resolve
history intent, reference resolution or multi-step clarification. Evaluate
faithfulness, latency and total RAM on the physical Pi before relying on it.

Evaluate storage and actual fresh-request routing separately:

```bash
python scripts/evaluate_memory.py --output logs/memory-content.json
python scripts/evaluate_memory.py --with-routing --output logs/memory-routing.json
```

These checks use disposable history and stub all non-history tools. They never
open the user's database. A nonzero exit code reports unmet expectations;
known history-routing failures are not counted as successes. The routing-only
suite is `scripts/evaluate_routing.py`; `--without-memory` measures the full
catalogue alone.

History is not automatically appended to OS-action prompts. Reference resolution
such as "delete that file again" and multi-step OS actions are not implemented.

Memory uses Python's standard SQLite library, with no embedding model or
SmolLM worker. It retains up to 1,000 turns, caps each request/answer/arguments
field at 2/4/2 KiB (previews are marked), and returns at most five excerpts within
6,000 bytes. SQLite's page cache target is 256 KiB and database page allocation
is capped at 16 MiB; these are not limits on total process RAM. SQLite journals
may temporarily take additional disk space. Topic search uses bounded text
matching, not semantic embeddings or model training. A storage failure reports
that the turn was not saved and does not rerun the tool.

`get_system_info` uses `platform` and `psutil` for CPU, installed RAM, and OS
information on Windows and Linux. GPU fields are explicitly unprobed; no GPU
runtime or desktop session is loaded to produce this report.

Local development decisions, session notes and implementation reports live in
`.local/` and are excluded from Git. For development on this machine, consult
`.local/PROJECT_MEMORY.md` for current status and priorities. Dated decisions
and old results are in `.local/PROJECT_HISTORY.md` and `.local/STAGE1.md`.
These notes are optional for running a fresh clone; public setup instructions
live in `docs/setup/`.
