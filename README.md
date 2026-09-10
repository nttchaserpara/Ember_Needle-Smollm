# Ember OS agent

Needle routes English requests to local tools. Document summaries use a managed
llama.cpp worker with SmolLM2-135M-Instruct Q4_K_M, loaded only for the document
job and stopped afterwards. If generation fails, Ember returns a labelled
extractive summary. No fine-tuning or new regex routing is involved.

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
The unused root `app_launcher.py` copy is excluded; the runtime imports
`use_cases/app_launcher.py`.

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

Needle also has a read-only `search_conversation_history` tool for natural
language history requests. Tool selection remains subject to routing confidence
and validation; use `/memory search <topic>` to test storage independently.
Natural-language recall is still inconsistent in local evaluations, including
false refusals on valid history requests; the explicit CLI commands are available
regardless of those routing failures.
History is not automatically appended to OS-action prompts. Reference resolution
such as "delete that file again" and multi-step follow-ups are not implemented.

Memory uses Python's standard SQLite library, with no embedding model or
SmolLM worker. It retains up to 1,000 turns, caps each request/answer/arguments
field at 2/4/2 KiB (previews are marked), and returns at most five excerpts within
6,000 bytes. SQLite's page cache target is 256 KiB and database page allocation
is capped at 16 MiB; these are not limits on total process RAM. SQLite journals
may temporarily take additional disk space. Topic search uses bounded text
matching, not semantic embeddings or model training. A storage failure reports
that the turn was not saved and does not rerun the tool.

Local development decisions, session notes and implementation reports live in
`.local/` and are excluded from Git. For development on this machine, consult
`.local/PROJECT_MEMORY.md` and `.local/STAGE1.md`. These notes are optional for
running a fresh clone; public setup instructions live in `docs/setup/`.
