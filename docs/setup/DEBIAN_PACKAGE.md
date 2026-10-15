# EmberOS Debian package

EmberOS 0.0.1 is built as one `.deb` per CPU architecture and Linux suite. The
initial matrix is Raspberry Pi OS/Debian 12 (`bookworm`, `arm64` and `amd64`),
Ubuntu 22.04 (`jammy`, `amd64`), and Ubuntu 24.04 (`noble`, `amd64`). Each
package bundles the app's Python environment, the Needle native engine, the
SmolLM2 model, and a generic CPU `llama-server`. The model makes the download
large; the first use does not need to fetch these runtime files.

After the signed APT repository has been published, configure it once and then
install EmberOS by package name:

```sh
curl -fsSL https://nttchaserpara.github.io/Ember_Needle-Smollm/install-apt.sh \
  -o /tmp/emberos-install-apt.sh
sudo sh /tmp/emberos-install-apt.sh
sudo apt-get update
sudo apt-get install emberos
```

Start the chat GUI with `emberos`; on a Linux desktop it opens a standalone
Chromium/Chrome/Edge/Brave app window when one is installed, and otherwise uses
the default browser. The launcher detects available browsers, so the same
package works across the supported Debian and Ubuntu desktop systems. A
headless Pi has no local display to show a popup; use the companion on a desktop
computer on the same network. Download the `emberos-companion-linux-amd64` or
`emberos-companion-windows` artifact from a successful GitHub Actions run, then
launch it. On Linux, run it from an active graphical session. For saved SSH
passwords, enable a Secret Service-compatible desktop keyring; without one, the
companion still connects but may ask for the password again later. On Windows,
unzip and launch `EmberOS-Connect.exe`. The prebuilt Linux companion is amd64;
on an arm64 Linux desktop, install `python3-tk` and `python3-venv`, create a
virtual environment, install `desktop/companion/requirements.txt`, then run
`desktop/companion/emberos_connect.py` with that environment's Python.
On the Pi, run `emberos` as your normal login user and leave it running. The
web server stays on the Pi's loopback interface; the companion discovers it on
the same local network and reaches it through SSH. On first use, it asks for
the Linux username/password and SSH host fingerprint. Compare the displayed
fingerprint with this command on the Pi before accepting:

```sh
for key in /etc/ssh/ssh_host_*_key.pub; do sudo ssh-keygen -lf "$key"; done
```

The password is saved in the operating system's credential manager; on Linux,
this requires a Secret Service-compatible desktop keyring. The short-lived web
token is read over SSH and is never announced on the network. Later, the
companion opens the app window automatically when it finds the paired Pi. Its
optional startup checkbox can keep discovery running after desktop login.
Allow the companion on Private networks if Windows Firewall asks. Discovery
needs the computer and Pi on the same network, with mDNS and SSH (port 22)
available.

If you prefer direct browser access from another device, run `emberos --lan`;
it binds the web server to the LAN and prints the manual URL and token. Find
the Pi address with `hostname -I`.
Use `emberos-cli` (or `emberos --cli`) for the terminal interface. Check the
installed version with `emberos --version`.
State, notes, tasks, and logs live under `${XDG_DATA_HOME:-~/.local/share}/emberos`
for the current user. Removing the package leaves this user data in place.

## Check an installed device

On each Raspberry Pi or lab Linux machine, confirm the package architecture and
version, then check the GUI launcher and terminal fallback as the ordinary
login user:

```sh
dpkg --print-architecture
emberos --version
printf 'exit\n' | emberos-cli
```

To test Needle's natural-language tool selection without performing real
actions, run the routing cases with a recording executor:

```sh
EMBER_DATA_DIR="${XDG_DATA_HOME:-$HOME/.local/share}/emberos" \
  /opt/emberos/venv/bin/python /opt/emberos/scripts/evaluate_routing.py \
  --output /tmp/emberos-routing.json --without-memory
```

The report must show the expected routes and no unexpected proposed actions.
These cases do not execute the selected tools, so also try the core tools in
the actual terminal, such as creating/searching a note and adding/listing a
task:

```sh
emberos-cli
```

For example, enter `Create a note titled 'Pi smoke' with content 'EmberOS test'`,
then `Search my notes for Pi smoke`, `Add a task titled 'Pi smoke'`, and
`List my tasks`.

For an isolated pass through the registered Web tools, run:

```sh
/opt/emberos/venv/bin/python /opt/emberos/scripts/smoke_all_tools.py --skip-model
```

It reports working tools, platform-unavailable tools, policy-blocked actions,
and checks needing external setup. It uses temporary notes, tasks, and files;
it does not run host-wide power/window actions or create a real Google document.
The local model path is checked separately below.

To test the bundled SmolLM2/`llama-server` summary path separately, run this
from the shell and check that the report says `model_summary_completed` and
lists no `children_still_running` processes:

```sh
EMBER_DATA_DIR="${XDG_DATA_HOME:-$HOME/.local/share}/emberos" \
  /opt/emberos/venv/bin/python /opt/emberos/scripts/benchmark_local_llm.py \
  --direct-tool --document /opt/emberos/experiments/fixtures/sample-summary.txt
```

On the Pi, also record `free -h` before and during this model test. An arm64 CI
pass checks package compatibility but cannot establish that the workload fits
the Pi's memory or runs at an acceptable speed.

The package currently targets these Debian-family releases and architectures
only. It preserves the Linux capabilities already present in EmberOS; Windows
audio, brightness, and other unported desktop tools are not added by packaging.
The build workflow tests installation from a signed test APT repository, user
permissions, the packaged GUI page and API authentication, Needle routing
inference, the registered Web tools in an isolated headless smoke run, and a
local model summary. Unsafe host controls are checked at the confirmation gate;
Google Docs is reported as requiring account setup. Its arm64 job runs on a
hosted arm64 Linux runner; this does not replace testing on the actual Raspberry
Pi model and lab devices.

## Maintainer release

Enable GitHub Pages with **GitHub Actions** as the publishing source. Create a
dedicated OpenPGP signing key for the APT repository, then save its armored
private key in the GitHub Actions secret `APT_SIGNING_KEY` and its passphrase in
`APT_SIGNING_PASSPHRASE`. Keep the private key outside the repository. Publish
the first version by pushing the tag `v0.0.1`; the workflow builds and smoke
tests all four packages, creates GitHub release downloads, signs the APT index,
and deploys the repository to Pages.

The APT setup script selects the package suite from `/etc/os-release` and the
CPU architecture from `dpkg`. It rejects other suites and architectures with a
clear message rather than configuring an incompatible package source.
