#!/usr/bin/env bash
set -euo pipefail

DEB_DIR=${1:?Pass the directory containing built .deb files}
SITE_DIR=${2:?Pass the output directory for the Pages site}
SOURCE_DIR=${3:?Pass the repository checkout}
SIGNING_KEY=${APT_SIGNING_KEY:?Set APT_SIGNING_KEY to the armored private key}
SIGNING_PASSPHRASE=${APT_SIGNING_PASSPHRASE:?Set APT_SIGNING_PASSPHRASE}

export DEBIAN_FRONTEND=noninteractive
for command in apt-ftparchive dpkg-scanpackages gpg; do
    command -v "$command" >/dev/null || {
        echo "Install apt-utils, dpkg-dev, and gnupg before publishing." >&2
        exit 2
    }
done

mkdir -p "$SITE_DIR/apt/pool/main/e/emberos" "$SITE_DIR/apt/dists"
cp "$SOURCE_DIR/packaging/apt/install-apt.sh" "$SITE_DIR/install-apt.sh"
chmod 0644 "$SITE_DIR/install-apt.sh"

gnupg_home=$(mktemp -d)
passphrase_file=$(mktemp)
trap 'rm -rf "$gnupg_home"; rm -f "$passphrase_file"' EXIT
chmod 0700 "$gnupg_home"
chmod 0600 "$passphrase_file"
printf '%s' "$SIGNING_PASSPHRASE" > "$passphrase_file"
export GNUPGHOME=$gnupg_home
printf '%s\n' "$SIGNING_KEY" | gpg --batch --import
fingerprint=$(gpg --batch --with-colons --list-secret-keys \
    | awk -F: '$1 == "fpr" { print $10; exit }')
if [[ ! "$fingerprint" =~ ^[A-F0-9]{40}$ ]]; then
    echo "Could not read a signing key fingerprint." >&2
    exit 1
fi
printf '%s\n' "$fingerprint" > "$SITE_DIR/apt/signing-key-fingerprint.txt"
gpg --batch --yes --export "$fingerprint" \
    > "$SITE_DIR/apt/emberos-archive-keyring.gpg"

for suite in bookworm jammy noble; do
    case "$suite" in
        bookworm) arches="amd64 arm64" ;;
        jammy|noble) arches="amd64" ;;
    esac
    for arch in $arches; do
        deb="$DEB_DIR/emberos_0.0.1-2_${suite}_${arch}.deb"
        if [[ ! -s "$deb" ]]; then
            echo "Missing build artifact: $deb" >&2
            exit 1
        fi
        suite_pool="$SITE_DIR/apt/pool/main/e/emberos/$suite"
        package_index="$SITE_DIR/apt/dists/$suite/main/binary-$arch"
        mkdir -p "$suite_pool" "$package_index"
        cp "$deb" "$suite_pool/"
        (cd "$SITE_DIR/apt" && dpkg-scanpackages --arch "$arch" \
            "pool/main/e/emberos/$suite" /dev/null \
            > "dists/$suite/main/binary-$arch/Packages")
        gzip -9 -c "$package_index/Packages" > "$package_index/Packages.gz"
    done
    apt-ftparchive \
        -o APT::FTPArchive::Release::Origin=EmberOS \
        -o APT::FTPArchive::Release::Label=EmberOS \
        -o APT::FTPArchive::Release::Suite="$suite" \
        -o APT::FTPArchive::Release::Codename="$suite" \
        -o APT::FTPArchive::Release::Architectures="$arches" \
        -o APT::FTPArchive::Release::Components=main \
        release "$SITE_DIR/apt/dists/$suite" \
        > "$SITE_DIR/apt/dists/$suite/Release"
    gpg --batch --yes --pinentry-mode loopback \
        --passphrase-file "$passphrase_file" --local-user "$fingerprint" \
        --clearsign --output "$SITE_DIR/apt/dists/$suite/InRelease" \
        "$SITE_DIR/apt/dists/$suite/Release"
    gpg --batch --yes --pinentry-mode loopback \
        --passphrase-file "$passphrase_file" --local-user "$fingerprint" \
        --detach-sign --output "$SITE_DIR/apt/dists/$suite/Release.gpg" \
        "$SITE_DIR/apt/dists/$suite/Release"
done

cat > "$SITE_DIR/index.html" <<'EOF'
<!doctype html>
<html lang="en"><meta charset="utf-8"><title>EmberOS packages</title>
<body><h1>EmberOS 0.0.1</h1>
<p>See the README for supported Linux systems and APT installation steps.</p>
<a href="install-apt.sh">Configure the EmberOS APT repository</a>
</body></html>
EOF

echo "Signed APT repository created in $SITE_DIR/apt"
