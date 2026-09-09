# Local summaries with SmolLM2 Q4 and llama.cpp

Ember uses the existing SmolLM2-135M-Instruct model in GGUF Q4_K_M format.
No training or conversion is performed. Needle still selects tools.
PyTorch and Transformers are not imported by the local summary path.

## Repository layout

```text
models/
  manifest.json                           # committed: version and SHA-256
  README.md                               # committed
  SmolLM2-135M-Instruct-Q4_K_M.gguf         # downloaded, ignored by Git
runtimes/llama.cpp/                        # native runtime, ignored by Git
scripts/setup_local_llm.py                 # reproducible setup
scripts/benchmark_local_llm.py             # parent + child memory sampler
docs/setup/                              # public setup guides
use_cases/local_llm.py                     # managed worker and token budgeting
logs/llama-server.log                      # last worker's diagnostics
```

Push the code and manifest, then download/copy the model after cloning.
Windows executables cannot run on the Pi; the Pi needs a Linux ARM64 runtime.
The GGUF model itself is shared between both platforms.

## Windows setup

From the repository root:

```powershell
python -m venv venv
.\venv\Scripts\python.exe -m pip install -r requirements.txt
.\venv\Scripts\python.exe scripts/setup_local_llm.py
.\venv\Scripts\python.exe run_ember.py
```

If `venv` already exists, use it. Setup downloads the pinned model and the
official llama.cpp **b7898 Windows x64 CPU** archive, verifying both SHA-256
checksums. It uses `models/` and `runtimes/` beside the code, regardless of the
working directory. Ordinary queries never download models.

For PDF, Word, and spreadsheet support, also install:

```powershell
.\venv\Scripts\python.exe -m pip install -r requirements-documents.txt
```

Windows audio tools additionally use `requirements-audio.txt`.

## Pi Zero 2 W setup after cloning

Use **64-bit Raspberry Pi OS Lite** and Python 3.11.8 or newer. Check `uname -m`
reports `aarch64`. The instructions below configure local text processing;
they do not port Windows desktop/window/audio tools to Linux.

```sh
sudo apt update
sudo apt install -y python3-venv build-essential cmake
python3 -m venv venv
./venv/bin/python -m pip install -r requirements.txt
./venv/bin/python scripts/setup_local_llm.py --model-only
```

If your previous llama.cpp build includes `llama-server`, reuse it:

```sh
export EMBER_LLAMA_SERVER="$HOME/projects/llama.cpp/build/bin/llama-server"
./venv/bin/python run_ember.py
```

`llama-cli` alone is not the server executable. If necessary, build the server
target from the existing source/build directory:

```sh
cmake --build "$HOME/projects/llama.cpp/build" --target llama-server -j1
```

If no compatible runtime exists, the setup script can download the pinned
llama.cpp b7898 source and build only the server target:

```sh
./venv/bin/python scripts/setup_local_llm.py --build-runtime --jobs 1
./venv/bin/python run_ember.py
```

Building natively on a Zero 2 W can take many hours and need swap. The script
does not change swap or OS settings. An existing compatible ARM64 build avoids
this compilation step. Keep the shared libraries beside the server executable.
Prefer b7898 when reproducing these results; a different version must be tested
for endpoint/flag compatibility.

If the Pi is offline, copy the verified GGUF into `models/` and provision the
Linux runtime/dependencies beforehand. Copying the Windows `venv/` is not valid.

## First headless Pi test

This is an initial compatibility and memory trial, not a completed Linux port.
Connect from another computer over SSH, then run all commands below in the Pi's
terminal from the cloned project root. Files, processes and resource readings
will belong to the Pi. Windows paths such as `D:\Downloads` do not refer to files
on the Pi; use the cloned fixtures or copy files there first.

Start with the base requirements and plain text. Additional PDF/OCR/desktop
packages are not needed for these fixtures. `psutil` may require compilation
if no compatible wheel exists; install `python3-dev` if pip reports missing
Python development headers. Needle also needs its platform-specific native
engine, fetched on first use; test its import before attempting full requests:

```sh
uname -m
free -h
./venv/bin/python -c 'import needle_router; print("Needle initialized")'
./venv/bin/python scripts/evaluate_routing.py --output logs/pi-routing.json
```

The routing evaluation records decisions without executing real tools. Several
non-action inputs still trigger incorrect proposed actions; inspect this report
before experimenting with arbitrary conversation in the interactive agent.

Next measure actual document jobs, one at a time:

```sh
./venv/bin/python scripts/benchmark_local_llm.py --document experiments/fixtures/sample-summary.txt
./venv/bin/python scripts/benchmark_local_llm.py --document experiments/fixtures/pi-long-report.txt
```

The long fixture is a fictional English workshop review with facts spread across
the beginning, middle and end. Check whether the result retains the decision
to reopen ten of twelve machines, keep two unavailable until repair and another
inspection, and hold the next review on 28 November 2026. A short summary need
not include every number. Reject invented facts and reversed decisions.
An extractive fallback demonstrates failure handling, not successful generative
summarization. Inspect `result.route`, `result.tool`, `result.status` and the
actual output alongside RAM figures; a refused request did not benchmark model
generation. `children_still_running` should be empty after the job.

The supplied long fixture contains 1,637 words (11,008 bytes). An initial Windows
run reached the generation token limit, returned a very short extractive result,
and left no model processes running. Its sampled parent-plus-children peak was
264.88 MiB and elapsed time was 5.71 seconds. This checks lifecycle/fallback
behavior; the result omitted major facts and is not a passing summary-quality
example. Measure the Pi independently rather than treating these figures as
its expected memory use or speed.

For a repeat of the long job, invoke the command again. Also monitor `free -h`
and `vmstat 1` in a second SSH terminal. Record available system RAM, swap
activity, total request time, and whether the machine remains responsive. The
benchmark's RSS sum does not include the whole OS and can count shared pages
more than once. Do not infer that the Pi fits from a Windows result.

For interactive summarization, run `realpath experiments/fixtures/pi-long-report.txt`
in the shell, start `./venv/bin/python run_ember.py`, and enter `Summarize` followed
by that absolute path in double quotes at `You>`. Shell commands belong at the
shell prompt, not inside Ember's `You>` prompt.

Source inspection also identifies file read/write/listing, file metadata,
SQLite task storage, RAM/disk queries and uptime as candidates for Linux tests.
They still need device validation and their natural-language routing can fail.
Windows launcher, Notepad, desktop/window controls and Windows audio/brightness
adapters are not ported. Unsupported tools are not yet automatically removed
from the headless router's catalogue. CPU name detection still uses WMIC and
may report an unknown name on Linux.

For software on the Pi, install a Linux package built for its architecture or
build the Linux source. Compressing a Windows executable into ZIP does not
convert it to an ARM Linux program. Python source is reusable when its
dependencies and OS calls support Linux. Adding a Linux application alone does
not give the current Windows launcher a Linux implementation.

Remote terminal/file transfer reference:
[Raspberry Pi remote access](https://www.raspberrypi.com/documentation/computers/remote-access.html).

## Behavior and memory limits

1. Extract text and check for missing/empty/unreadable input.
2. On the first generation request, launch one CPU `llama-server` on loopback
   with an ephemeral port and per-job access key.
3. Apply the model chat template and count tokens with its own tokenizer.
4. Split source text into fitting chunks. Summarize sequentially and reduce
   section summaries in further passes when needed. Every source character
   within the extraction limit is retained during chunking.
5. Stop and reap the worker at the end of the document job, including failures
   and Ctrl+C. A document with multiple passes uses one model load.

Defaults: 2048 context tokens, 192 maximum output tokens per generation, 4 CPU
threads, 1 server slot, batch 128, micro-batch 64. Context shifting is disabled;
input must fit with space reserved for the output. Repetition penalty is 1.0
so repeated source facts are not penalized. These are initial settings, not a
guarantee of a particular RAM peak on every platform/document.

Failures (missing runtime/model, timeout, worker exit, empty output, token-limit
termination, or context truncation) produce an **extractive summary selected
from the source**, labelled in the result. There is no retry loop or extra LLM.
Extraction errors are reported directly, not summarized as if they were source.
Normal completion is not a factual-accuracy check: this small Q4 model can still
omit details or invent facts. Quality evaluation remains separate from memory
and lifecycle validation.

The extraction path retains its 80,000-character summary limit; exceeding it is
now reported in the result. Existing CSV/XLSX readers limit output to 100 rows,
and XLSX reads the first sheet. Plain text reads are bounded; PDF text stops at
the extraction limit and page caches are released. Some document formats still
parse XML/archive content in memory; this change is not a full large-file parser
rewrite. The extractive fallback also selects a subset of source sentences.

## Configuration

| Environment variable | Default | Purpose |
| --- | --- | --- |
| `EMBER_MODEL_PATH` | `models/SmolLM2-135M-Instruct-Q4_K_M.gguf` | Override model location |
| `EMBER_LLAMA_SERVER` | Auto-discovered in `runtimes/llama.cpp`, then PATH | Native executable |
| `EMBER_LLM_CONTEXT` | `2048` | Total prompt/output context; minimum 512 |
| `EMBER_LLM_THREADS` | `4` | Generation threads |
| `EMBER_LLM_TIMEOUT` | `180` seconds | Individual HTTP request timeout |
| `EMBER_LLM_STARTUP_TIMEOUT` | `120` seconds | Worker startup timeout |
| `EMBER_LLM_JOB_TIMEOUT` | `600` seconds | Whole model job budget |

Use an absolute path for overrides that must work from any working directory.
The worker is local only; it does not connect to a cloud service or an externally
managed model server. The process is serialized across local document jobs.

## Tests and benchmark

The interactive `run_ember.py` terminal also samples parent and descendant RSS
every 50 ms during each request. Its `sampled peak RSS (Ember + children)` field
shows the largest observed simultaneous sum, with both components at that same
sample. For summary jobs, children include the native model worker; other tools
may launch different child processes. `Ember RSS now` is measured after the job,
when the model has already been unloaded. `whole-system RAM` includes all OS and
application memory; do not add it to Ember's RSS. This sampler is an estimate,
can miss brief peaks, and can double-count shared pages in the RSS sum.

On the development PC, if the local test files are present, run backend/fallback
tests without loading a model. These tests are excluded from Git and are not
required for setup or available in a fresh Pi clone:

```sh
./venv/bin/python -m unittest experiments.test_local_llm -v
```

Run an actual English document job through Ember:

```sh
./venv/bin/python scripts/benchmark_local_llm.py
./venv/bin/python scripts/benchmark_local_llm.py --document experiments/fixtures/sample-summary.txt
```

On Windows use `.\venv\Scripts\python.exe` instead. The benchmark reports sampled
peak RSS for the parent and its descendants, remaining children after the job,
generation result, elapsed time, and whether Torch/Transformers were imported.
Sampling can miss brief peaks and summed RSS can count shared pages more than
once. System swap change is system-wide, not attributable solely to Ember.
Compare quality and long-document cases as well as short prompts. Windows
results do not substitute for measurements on the physical Pi.

Local validation on Windows 11, Python 3.13.2 (2048 context, 4 threads):

| Input | Sampled parent + children peak RSS | Outcome |
| --- | --- | --- |
| 103-byte English audio test report | 247.26 MiB | Completed summary; 2.33 seconds |
| Approximately 7 KB of this setup guide | 247.69 MiB | Token-limit exit; labelled extractive fallback; 9.37 seconds |

Both jobs left no child processes running and imported neither Torch nor
Transformers. These figures exclude the rest of the operating system. A separate
6,389-character input at 1024 context also reached the output limit and correctly
fell back. Successful multi-pass reduction is covered by a controlled unit test;
these real long-input trials do not establish long-document model quality.

Sources: [reference Pi setup](https://github.com/ravijo/pi-llm),
[llama.cpp b7898 server](https://github.com/ggml-org/llama.cpp/blob/b7898/tools/server/README.md),
[model source](https://huggingface.co/bartowski/SmolLM2-135M-Instruct-GGUF).
