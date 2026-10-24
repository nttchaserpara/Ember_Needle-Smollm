# EmberOS

A local OS-agent prototype for Windows and Linux/Raspberry Pi. Needle routes
English requests to Python tools; SmolLM2-135M-Instruct runs through llama.cpp
on the CPU for document summaries and short replies.

Features include system information, file operations, tasks, notes, local
conversation history, and a web GUI with a companion for headless devices.

## Install with APT

Supported: Debian/Raspberry Pi OS 12 **bookworm** (`arm64`, `amd64`), Ubuntu
22.04 **jammy** and 24.04 **noble** (`amd64`). Raspberry Pi requires a 64-bit OS.

Run in the Linux/Pi terminal. Configure the EmberOS repository once, then install:

```bash
sudo apt update
sudo apt install curl ca-certificates
curl -fsSL https://nttchaserpara.github.io/Ember_Needle-Smollm/install-apt.sh \
  -o /tmp/emberos-install-apt.sh && sudo sh /tmp/emberos-install-apt.sh
sudo apt update
sudo apt install emberos
```

Continue only after repository setup and `apt update` succeed. The package
includes Python dependencies, Needle, SmolLM2, and llama.cpp; no separate
model setup is needed. APT installs the published release, not the latest `main`.

Run as your normal user:

```bash
emberos-cli     # Terminal interface, including over SSH
emberos        # GUI; use the companion for a headless Pi
```

See the [APT guide](docs/setup/DEBIAN_PACKAGE.md) for updates, troubleshooting,
and companion setup.

## Run from source

Use Python 3.11.8 or newer. Windows PowerShell:

```powershell
git clone https://github.com/nttchaserpara/Ember_Needle-Smollm.git
cd Ember_Needle-Smollm
python -m venv venv
.\venv\Scripts\python.exe -m pip install -r requirements.txt
.\venv\Scripts\python.exe run_ember.py
```

For SmolLM summaries and replies, run
`.\venv\Scripts\python.exe scripts/setup_local_llm.py` before launching Ember.
See [local model setup](docs/setup/LOCAL_LLM.md) for Linux/Pi source installation.
Initial setup requires internet access. PDF/Office support uses
`requirements-documents.txt`; Windows audio uses `requirements-audio.txt`.

## Usage

At the `You>` prompt, enter one request at a time:

```text
How much free disk space do I have?
List the files and folders in my home directory.
/memory
exit
```

This is a prototype: routing and natural-language recall remain imperfect.
Summary generation may fall back to labelled source extracts. Desktop tools
depend on the OS and hardware; actions run on the backend computer.

## Documentation

- [Usage and examples](docs/USAGE.md)
- [Document tools](docs/setup/DOCUMENT_TOOLS.md), [audio](docs/setup/AUDIO_TOOLS.md), and [undo](docs/setup/UNDO.md)
- [Development and evaluation](CONTRIBUTING.md)

Source lives in `emberos/` and `use_cases/`; setup and evaluation scripts are
in `scripts/`. `experiments/` contains only shared evaluation cases and sample
inputs. Personal notes, runtime data, downloaded models, and build output are
excluded from Git.
