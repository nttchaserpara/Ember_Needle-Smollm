"""Command-line entry point for an installed EmberOS package."""

import sys

from emberos import __version__


def main():
    if sys.argv[1:] in (['--version'], ['-V']):
        print(f"EmberOS {__version__}")
        return
    if sys.argv[1:] in (['--help'], ['-h']):
        print("Usage: emberos [--version] [--help]\nStart the interactive EmberOS terminal.")
        return
    if sys.argv[1:]:
        raise SystemExit(f"Unknown option: {sys.argv[1]} (try --help)")

    from run_ember import main as run_terminal
    run_terminal()


if __name__ == "__main__":
    main()
