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

Start the terminal with `emberos`, check the installed version with
`emberos --version`, or start the local web interface with `emberos-web`.
State, notes, tasks, and logs live under `${XDG_DATA_HOME:-~/.local/share}/emberos`
for the current user. Removing the package leaves this user data in place.

## Check an installed device

On each Raspberry Pi or lab Linux machine, confirm the package architecture and
version, then check that the terminal starts as the ordinary login user:

```sh
dpkg --print-architecture
emberos --version
printf 'exit\n' | emberos
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
emberos
```

For example, enter `Create a note titled 'Pi smoke' with content 'EmberOS test'`,
then `Search my notes for Pi smoke`, `Add a task titled 'Pi smoke'`, and
`List my tasks`.

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
permissions, Needle routing inference, and a local model summary. Its arm64 job
runs on a hosted arm64 Linux runner; this does not replace testing on the actual
Raspberry Pi model and lab devices.

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
