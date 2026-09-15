"""Compatibility entry point for the production reply-worker benchmark."""

from scripts.bench_short_response import main


if __name__ == "__main__":
    raise SystemExit(main())
