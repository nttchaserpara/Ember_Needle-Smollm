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

Local development decisions, session notes and implementation reports live in
`.local/` and are excluded from Git. For development on this machine, consult
`.local/PROJECT_MEMORY.md` and `.local/STAGE1.md`. These notes are optional for
running a fresh clone; public setup instructions live in `docs/setup/`.
