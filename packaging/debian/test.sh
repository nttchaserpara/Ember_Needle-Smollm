#!/usr/bin/env bash
set -euo pipefail

DEB_FILE=${1:?Pass the package file to install}
SUITE=${2:?Pass the Debian or Ubuntu suite}
ARCH=${3:?Pass the Debian architecture}
export DEBIAN_FRONTEND=noninteractive

apt-get update
apt-get install -y --no-install-recommends apt-utils dpkg-dev gnupg passwd util-linux

# Create a signed temporary APT repository so this checks the requested
# `apt-get install emberos` flow, including signature verification.
repo=$(mktemp -d)
gnupg_home=$(mktemp -d)
trap 'userdel --remove emberos-smoke 2>/dev/null || true; rm -rf "$repo" "$gnupg_home"' EXIT
chmod 0700 "$gnupg_home"
gpg --homedir "$gnupg_home" --batch --pinentry-mode loopback --passphrase '' \
    --quick-generate-key "EmberOS CI Test <ci@example.invalid>" rsa2048 sign 1d
fingerprint=$(gpg --homedir "$gnupg_home" --with-colons --list-keys \
    | awk -F: '$1 == "fpr" { print $10; exit }')
install -d "$repo/pool/main/e/emberos" \
    "$repo/dists/$SUITE/main/binary-$ARCH" /usr/share/keyrings
cp "$DEB_FILE" "$repo/pool/main/e/emberos/"
(cd "$repo" && dpkg-scanpackages --arch "$ARCH" pool /dev/null \
    > "dists/$SUITE/main/binary-$ARCH/Packages")
gzip -9 -c "$repo/dists/$SUITE/main/binary-$ARCH/Packages" \
    > "$repo/dists/$SUITE/main/binary-$ARCH/Packages.gz"
apt-ftparchive \
    -o APT::FTPArchive::Release::Origin=EmberOS \
    -o APT::FTPArchive::Release::Label=EmberOS \
    -o APT::FTPArchive::Release::Suite="$SUITE" \
    -o APT::FTPArchive::Release::Codename="$SUITE" \
    -o APT::FTPArchive::Release::Architectures="$ARCH" \
    -o APT::FTPArchive::Release::Components=main \
    release "$repo/dists/$SUITE" > "$repo/dists/$SUITE/Release"
gpg --homedir "$gnupg_home" --batch --yes --local-user "$fingerprint" \
    --clearsign --output "$repo/dists/$SUITE/InRelease" \
    "$repo/dists/$SUITE/Release"
gpg --homedir "$gnupg_home" --batch --yes --local-user "$fingerprint" \
    --detach-sign --output "$repo/dists/$SUITE/Release.gpg" \
    "$repo/dists/$SUITE/Release"
gpg --homedir "$gnupg_home" --export "$fingerprint" > /usr/share/keyrings/emberos-ci.gpg
chmod -R a+rX "$repo"
printf 'deb [arch=%s signed-by=/usr/share/keyrings/emberos-ci.gpg] file:%s %s main\n' \
    "$ARCH" "$repo" "$SUITE" > /etc/apt/sources.list.d/emberos-ci.list
apt-get update
apt-get install -y --no-install-recommends emberos

useradd --create-home emberos-smoke

version=$(runuser -u emberos-smoke -- env HOME=/home/emberos-smoke /usr/bin/emberos --version)
test "$version" = "EmberOS 0.0.1"

# Confirm `emberos` is the graphical Web GUI entry point and retain an explicit
# CLI command for headless terminal use.
web_help=$(runuser -u emberos-smoke -- env HOME=/home/emberos-smoke /usr/bin/emberos --help)
printf '%s\n' "$web_help" | grep -q 'EmberOS Web GUI Server'
cli_version=$(runuser -u emberos-smoke -- env HOME=/home/emberos-smoke /usr/bin/emberos-cli --version)
test "$cli_version" = "EmberOS 0.0.1"

# Start the packaged GUI in headless mode and verify the page and API auth gate.
runuser -u emberos-smoke -- env HOME=/home/emberos-smoke \
    EMBER_DATA_DIR=/home/emberos-smoke/.local/share/emberos \
    PYTHONPATH=/opt/emberos \
    /opt/emberos/venv/bin/python - <<'PY'
import socket
import subprocess
import time
import urllib.error
import urllib.request

with socket.socket() as probe:
    probe.bind(("127.0.0.1", 0))
    port = probe.getsockname()[1]

process = subprocess.Popen(
    ["/usr/bin/emberos", "--host", "127.0.0.1", "--port", str(port)],
    stdout=subprocess.DEVNULL,
    stderr=subprocess.PIPE,
    text=True,
)
base = f"http://127.0.0.1:{port}"
try:
    deadline = time.monotonic() + 15
    while True:
        try:
            with urllib.request.urlopen(base + "/", timeout=1) as response:
                page = response.read().decode("utf-8")
                assert response.status == 200 and "ZEVION Labs" in page
            break
        except (OSError, urllib.error.URLError):
            if process.poll() is not None or time.monotonic() >= deadline:
                raise RuntimeError("Packaged EmberOS GUI did not start")
            time.sleep(0.1)

    try:
        urllib.request.urlopen(base + "/api/status", timeout=2)
    except urllib.error.HTTPError as error:
        assert error.code == 401, error.code
    else:
        raise AssertionError("GUI API accepted a request without its token")
finally:
    process.terminate()
    process.wait(timeout=5)
PY

# Launch the actual terminal as an unprivileged user and initialize its data.
printf 'exit\n' | runuser -u emberos-smoke -- env HOME=/home/emberos-smoke /usr/bin/emberos-cli
test -f /home/emberos-smoke/.local/share/emberos/conversation.sqlite3
test ! -e /opt/emberos/data

# Load the native Needle engine and make one tool-selection inference.
runuser -u emberos-smoke -- env HOME=/home/emberos-smoke \
    EMBER_DATA_DIR=/home/emberos-smoke/.local/share/emberos \
    PYTHONPATH=/opt/emberos \
    /opt/emberos/venv/bin/python -c '
import json
from needle_router import needle_agent
result = needle_agent.complete("Hello. Do not call a tool.")
assert isinstance(result, dict) and result.get("type") in {"text", "call"}, result
print(json.dumps({"needle_response_type": result["type"]}))
'

# Exercise the bundled GGUF and llama-server together; require model output and
# verify the managed child exits when the request finishes.
result=$(runuser -u emberos-smoke -- env HOME=/home/emberos-smoke \
    EMBER_DATA_DIR=/home/emberos-smoke/.local/share/emberos \
    /opt/emberos/venv/bin/python /opt/emberos/scripts/benchmark_local_llm.py \
    --direct-tool --document /opt/emberos/experiments/fixtures/sample-summary.txt)
printf '%s\n' "$result"
RESULT_JSON="$result" /opt/emberos/venv/bin/python - <<'PY'
import json
import os
from pathlib import Path

report = json.loads(os.environ["RESULT_JSON"])
if report["generation_outcome"] != "model_summary_completed":
    log_path = Path("/home/emberos-smoke/.local/share/emberos/logs/llama-server.log")
    print(f"llama-server diagnostic log: {log_path}")
    if log_path.is_file():
        lines = log_path.read_text(encoding="utf-8", errors="replace").splitlines()
        print("\n".join(lines[-100:]))
    else:
        print("The llama-server log file was not created.")
    raise AssertionError(report["generation_outcome"])
assert not report["children_still_running"], report["children_still_running"]
PY
