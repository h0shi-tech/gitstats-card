"""Allow ``python -m gitstats_card`` to behave like the console script."""

from __future__ import annotations

from .cli import main

if __name__ == "__main__":
    raise SystemExit(main())
