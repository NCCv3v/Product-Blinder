"""Compatibility entrypoint: the UI is now a real native Qt desktop program."""

from .desktop import main

if __name__ == "__main__":
    raise SystemExit(main())
