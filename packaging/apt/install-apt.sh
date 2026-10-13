#!/bin/sh
set -eu

if [ "$(id -u)" -ne 0 ]; then
    echo "Run this setup script with sudo." >&2
    exit 1
fi
if ! command -v apt-get >/dev/null 2>&1 || ! command -v curl >/dev/null 2>&1; then
    echo "This setup requires apt and curl." >&2
    exit 1
fi

. /etc/os-release
suite=${VERSION_CODENAME:-}
case "$suite" in
    bookworm|jammy|noble) ;;
    *) echo "EmberOS 0.0.1 packages are not published for ${suite:-this Linux release}." >&2; exit 1 ;;
esac

arch=$(dpkg --print-architecture)
case "$arch" in
    arm64|amd64) ;;
    *) echo "EmberOS 0.0.1 packages do not support architecture $arch." >&2; exit 1 ;;
esac

repo_base=${EMBEROS_APT_BASE_URL:-https://nttchaserpara.github.io/Ember_Needle-Smollm/apt}
tmp_key=$(mktemp)
trap 'rm -f "$tmp_key"' EXIT HUP INT TERM
curl --fail --location --silent --show-error --proto '=https' --tlsv1.2 \
    "$repo_base/emberos-archive-keyring.gpg" --output "$tmp_key"
install -m 0644 "$tmp_key" /usr/share/keyrings/emberos-archive-keyring.gpg

cat > /etc/apt/sources.list.d/emberos.sources <<EOF
Types: deb
URIs: $repo_base
Suites: $suite
Components: main
Architectures: $arch
Signed-By: /usr/share/keyrings/emberos-archive-keyring.gpg
EOF

echo "EmberOS APT source configured for $suite/$arch."
