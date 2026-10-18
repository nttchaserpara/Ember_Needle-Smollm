#!/usr/bin/env bash
set -euo pipefail

SOURCE_DIR=${SOURCE_DIR:-/src}
OUTPUT_DIR=${OUTPUT_DIR:-/out}
SUITE=${SUITE:?Set SUITE to bookworm, jammy, or noble}
ARCH=${ARCH:-$(dpkg --print-architecture)}
UPSTREAM_VERSION=${UPSTREAM_VERSION:-0.0.1}
DEB_VERSION=${DEB_VERSION:-${UPSTREAM_VERSION}-4}
BUILD_JOBS=${BUILD_JOBS:-2}
APP_ROOT=/opt/emberos

case "$SUITE" in
    bookworm|jammy|noble) ;;
    *) echo "Unsupported target suite: $SUITE" >&2; exit 2 ;;
esac
case "$ARCH" in
    arm64|amd64) ;;
    *) echo "Unsupported Debian architecture: $ARCH" >&2; exit 2 ;;
esac
if [[ -e "$APP_ROOT" ]]; then
    echo "$APP_ROOT already exists; build in a clean container." >&2
    exit 2
fi
if [[ ! -d "$SOURCE_DIR/.git" ]]; then
    echo "SOURCE_DIR must be a git checkout so only tracked project files are packaged." >&2
    exit 2
fi

export DEBIAN_FRONTEND=noninteractive
apt-get update
apt-get install -y --no-install-recommends \
    ca-certificates cmake curl g++ git make python3-dev python3-venv

mkdir -p "$APP_ROOT" "$OUTPUT_DIR"
git -c safe.directory="$SOURCE_DIR" -C "$SOURCE_DIR" ls-files -z \
    | tar --null -C "$SOURCE_DIR" -T - -cf - \
    | tar -C "$APP_ROOT" -xf -

python3 -m venv "$APP_ROOT/venv"
"$APP_ROOT/venv/bin/python" -m pip install \
    -r "$APP_ROOT/requirements.txt" \
    -r "$APP_ROOT/requirements-documents.txt"

case "$ARCH" in
    arm64) needle_tag=manylinux2014_aarch64 ;;
    amd64) needle_tag=manylinux2014_x86_64 ;;
esac
needle_package=$(
    "$APP_ROOT/venv/bin/python" -c 'import pathlib, needle; print(pathlib.Path(needle.__file__).resolve().parent)'
)
"$APP_ROOT/venv/bin/needle" fetch --out "$needle_package" --platform-tag "$needle_tag"

# Preinstall the checksum-pinned GGUF so first use does not need a download.
"$APP_ROOT/venv/bin/python" "$APP_ROOT/scripts/setup_local_llm.py" --model-only

# Build a generic CPU runtime for this package architecture. Native tuning is
# disabled because the build runner is not the same CPU model as every target.
llama_source=$(mktemp -d)
llama_build=$(mktemp -d)
trap 'rm -rf "$llama_source" "$llama_build"' EXIT
llama_version=$(python3 -c 'import json, pathlib; print(json.loads(pathlib.Path("/opt/emberos/models/manifest.json").read_text())["llama_cpp_version"])')
curl --fail --location --silent --show-error \
    "https://github.com/ggml-org/llama.cpp/archive/refs/tags/${llama_version}.tar.gz" \
    --output "$llama_source/source.tar.gz"
mkdir "$llama_source/source"
tar -xzf "$llama_source/source.tar.gz" --strip-components=1 -C "$llama_source/source"
# Keep the packaged worker independent of libraries left in the temporary build tree.
cmake -S "$llama_source/source" -B "$llama_build" \
    -DCMAKE_BUILD_TYPE=Release \
    -DBUILD_SHARED_LIBS=OFF \
    -DGGML_NATIVE=OFF \
    -DGGML_CUDA=OFF \
    -DGGML_BLAS=OFF \
    -DLLAMA_CURL=OFF \
    -DLLAMA_BUILD_TESTS=OFF
cmake --build "$llama_build" --target llama-server --parallel "$BUILD_JOBS"

runtime_dir="$APP_ROOT/runtimes/llama.cpp/build/bin"
mkdir -p "$runtime_dir"
cp -a "$llama_build/bin/." "$runtime_dir/"
install -m 0644 "$llama_source/source/LICENSE" \
    "$APP_ROOT/runtimes/llama.cpp/LICENSE"
"$runtime_dir/llama-server" --version

test -s "$APP_ROOT/models/SmolLM2-135M-Instruct-Q4_K_M.gguf"
printf '%s  %s\n' \
    "$(python3 -c 'import json, pathlib; print(json.loads(pathlib.Path("/opt/emberos/models/manifest.json").read_text())["sha256"])')" \
    "$APP_ROOT/models/SmolLM2-135M-Instruct-Q4_K_M.gguf" \
    | sha256sum --check --status

stage=$(mktemp -d)
trap 'rm -rf "$llama_source" "$llama_build" "$stage"' EXIT
install -d "$stage/DEBIAN" "$stage/opt" "$stage/usr/bin" "$stage/usr/share/doc/emberos"
cp -a "$APP_ROOT" "$stage/opt/emberos"
install -m 0755 "$SOURCE_DIR/packaging/bin/emberos" "$stage/usr/bin/emberos"
install -m 0755 "$SOURCE_DIR/packaging/bin/emberos-cli" "$stage/usr/bin/emberos-cli"
install -m 0755 "$SOURCE_DIR/packaging/bin/emberos-web" "$stage/usr/bin/emberos-web"
apache_license=$(find "$APP_ROOT/venv/lib" \
    -path '*/cactus_needle-2.0.11.dist-info/licenses/LICENSE' \
    -type f -print -quit)
if [[ -z "$apache_license" ]]; then
    echo "The cactus-needle wheel did not provide its Apache-2.0 license." >&2
    exit 1
fi
install -m 0644 "$apache_license" \
    "$stage/usr/share/doc/emberos/LICENSE.Apache-2.0"

installed_size=$(du -sk "$stage/opt/emberos" | cut -f1)
cat > "$stage/DEBIAN/control" <<EOF
Package: emberos
Version: $DEB_VERSION
Section: utils
Priority: optional
Architecture: $ARCH
Installed-Size: $installed_size
Depends: ca-certificates, libc6 (>= 2.31), libgomp1, libstdc++6, python3 (>= 3.10)
Maintainer: EmberOS Maintainers <nttchaserpara@users.noreply.github.com>
Homepage: https://github.com/nttchaserpara/Ember_Needle-Smollm
Description: local EmberOS assistant and tools
 EmberOS is a Python-based local assistant with Needle tool routing and a
 bundled SmolLM2 CPU runtime. User data is stored in the user's XDG data folder.
EOF
cat > "$stage/usr/share/doc/emberos/NOTICE" <<EOF
EmberOS package $UPSTREAM_VERSION

Bundled third-party components:
- SmolLM2 GGUF from bartowski/SmolLM2-135M-Instruct-GGUF (Apache-2.0);
  see LICENSE.Apache-2.0
- llama.cpp $llama_version (MIT); see /opt/emberos/runtimes/llama.cpp/LICENSE
- cactus-needle and Python dependencies; see the corresponding *.dist-info
  metadata and license files in /opt/emberos/venv/lib/python*/site-packages.
EOF
gzip -9 "$stage/usr/share/doc/emberos/NOTICE"

dpkg-deb --root-owner-group --build "$stage" \
    "$OUTPUT_DIR/emberos_${DEB_VERSION}_${SUITE}_${ARCH}.deb"
echo "Built $OUTPUT_DIR/emberos_${DEB_VERSION}_${SUITE}_${ARCH}.deb"
