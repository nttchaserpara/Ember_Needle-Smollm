# Development

Use the project virtual environment and run commands from the repository root.
On Windows, replace `python` with `.\venv\Scripts\python.exe`; on Linux,
use `./venv/bin/python`. Install `requirements.txt` first.

## Regression checks

```bash
python -m unittest scripts.test_gui_launcher scripts.test_web_discovery -v
python scripts/test_directory_listing.py
python scripts/test_file_reference.py
python scripts/test_confirmation.py
python scripts/test_web_registry_security.py
python scripts/test_linux_adapters.py
```

These checks use mocks and/or temporary files. Some still import the native
Needle engine. Set `EMBER_DATA_DIR` to a disposable directory to isolate logs
and application data. Companion tests additionally require
`desktop/companion/requirements.txt`.

## Evaluation

```bash
python scripts/evaluate_memory.py --output logs/memory-content.json
python scripts/evaluate_routing.py --output logs/routing.json
python scripts/benchmark_local_llm.py --document experiments/fixtures/sample-summary.txt
```

The summary benchmark requires the [local model/runtime](docs/setup/LOCAL_LLM.md).
Memory evaluation uses a temporary database; routing records proposals without
executing tools. The memory suite includes a known failing case where traceback
paths match a discussion topic. Inspect failures instead of counting fallbacks
or storage success as correct recall. See [memory details](docs/CONVERSATION_MEMORY.md).

Keep the small JSON cases and sample documents in `experiments/`: evaluation
scripts, the GUI sample, and package tests reference them. Local ad hoc Python
experiments and recorded results are ignored by Git.

When changing tools, keep Needle schemas, registry implementations, and UI
confirmation rules consistent. Record the commit, platform, model/runtime,
command, and pass/fail counts when reporting results. Mock tests do not establish
model accuracy or Raspberry Pi performance. Keep credentials and user data out
of commits.
