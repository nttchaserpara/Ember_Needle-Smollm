"""Download the pinned model and install/build the native CPU runtime."""

import argparse
import hashlib
import json
import os
import platform
from pathlib import Path
import shutil
import subprocess
import tarfile
import urllib.request
import zipfile

ROOT = Path(__file__).resolve().parents[1]
MANIFEST = json.loads((ROOT / "models" / "manifest.json").read_text(encoding="utf-8"))


def checksum(path):
    digest = hashlib.sha256()
    with path.open("rb") as source:
        for block in iter(lambda: source.read(1024 * 1024), b""):
            digest.update(block)
    return digest.hexdigest()


def download(url, path, expected_sha=None):
    if path.exists() and (expected_sha is None or checksum(path) == expected_sha):
        print(f"Already available: {path}", flush=True)
        return
    if path.exists():
        raise RuntimeError(f"Checksum mismatch for existing file: {path}. Move it aside before retrying.")
    path.parent.mkdir(parents=True, exist_ok=True)
    partial = path.with_suffix(path.suffix + ".part")
    print(f"Downloading: {path.name}", flush=True)
    request = urllib.request.Request(url, headers={"User-Agent": "Ember-local-llm-setup"})
    try:
        with urllib.request.urlopen(request, timeout=60) as response, partial.open("wb") as output:
            shutil.copyfileobj(response, output, length=1024 * 1024)
        if expected_sha and checksum(partial) != expected_sha:
            raise RuntimeError(f"Downloaded file failed SHA-256 verification: {path.name}")
        partial.replace(path)
    finally:
        partial.unlink(missing_ok=True)


def install_model():
    model = ROOT / "models" / MANIFEST["filename"]
    url = (f"https://huggingface.co/{MANIFEST['repository']}/resolve/"
           f"{MANIFEST['revision']}/{MANIFEST['filename']}")
    download(url, model, MANIFEST["sha256"])
    print(f"Model verified: {model} ({model.stat().st_size} bytes)", flush=True)


def install_windows_runtime():
    if platform.machine().lower() not in ("amd64", "x86_64"):
        raise RuntimeError("This installer provides Windows x64 binaries. Set EMBER_LLAMA_SERVER to a compatible binary.")
    target = ROOT / "runtimes" / "llama.cpp"
    archive = ROOT / "runtimes" / MANIFEST["windows_archive"]
    download(
        f"https://github.com/ggml-org/llama.cpp/releases/download/{MANIFEST['llama_cpp_version']}/{archive.name}",
        archive, MANIFEST["windows_sha256"],
    )
    target.mkdir(parents=True, exist_ok=True)
    with zipfile.ZipFile(archive) as bundle:
        for member in bundle.infolist():
            destination = (target / member.filename).resolve()
            if not destination.is_relative_to(target.resolve()):
                raise RuntimeError("Unsafe runtime archive path")
        bundle.extractall(target)
    print(f"Runtime installed: {target}", flush=True)


def build_linux_runtime(jobs):
    if not shutil.which("cmake"):
        raise RuntimeError("Install build-essential and cmake first (see docs/setup/LOCAL_LLM.md).")
    target = ROOT / "runtimes" / "llama.cpp"
    source = target / f"llama.cpp-{MANIFEST['llama_cpp_version']}"
    archive = target / "source.tar.gz"
    # The source tag matches the Windows binary and the reference Pi experiment.
    download(f"https://github.com/ggml-org/llama.cpp/archive/refs/tags/{MANIFEST['llama_cpp_version']}.tar.gz", archive)
    with tarfile.open(archive) as bundle:
        # Python >= 3.11.8 supplies the data filter; reject links on older builds.
        if not hasattr(tarfile, "data_filter"):
            raise RuntimeError("Python 3.11.8+ is required for safe source extraction")
        bundle.extractall(target, filter="data")
    build = target / "build"
    subprocess.run([
        "cmake", "-S", str(source), "-B", str(build), "-DCMAKE_BUILD_TYPE=Release",
        "-DGGML_NATIVE=ON", "-DGGML_BLAS=OFF", "-DGGML_CUDA=OFF",
        "-DLLAMA_CURL=OFF", "-DLLAMA_BUILD_TESTS=OFF",
    ], check=True)
    subprocess.run(["cmake", "--build", str(build), "--config", "Release",
                    "--target", "llama-server", "-j", str(jobs)], check=True)
    print(f"Runtime built: {build / 'bin' / 'llama-server'}", flush=True)


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--model-only", action="store_true")
    parser.add_argument("--build-runtime", action="store_true", help="Build llama-server on Linux; slow on Pi Zero 2 W")
    parser.add_argument("--jobs", type=int, default=1, help="Build parallelism (default 1 for low RAM)")
    args = parser.parse_args()
    if args.jobs < 1:
        parser.error("--jobs must be positive")
    install_model()
    if args.model_only:
        return
    if os.name == "nt":
        install_windows_runtime()
    elif args.build_runtime:
        build_linux_runtime(args.jobs)
    else:
        print("Model ready. Set EMBER_LLAMA_SERVER to your existing llama-server, or rerun with --build-runtime.")


if __name__ == "__main__":
    main()
