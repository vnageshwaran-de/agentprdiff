"""Tiny CLI so ``agentprdiff-studio`` (the console_scripts entry) does something useful.

For M1 it just delegates to uvicorn. Later we can add ``init``, ``migrate``,
``add-secret``, etc.
"""

from __future__ import annotations

import argparse
import sys


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(prog="agentprdiff-studio")
    sub = parser.add_subparsers(dest="cmd", required=False)

    serve = sub.add_parser("serve", help="Run the API server with uvicorn")
    serve.add_argument("--host", default="0.0.0.0")
    serve.add_argument("--port", type=int, default=8080)
    serve.add_argument("--reload", action="store_true")

    args = parser.parse_args(argv)

    cmd = args.cmd or "serve"
    if cmd == "serve":
        import uvicorn

        uvicorn.run(
            "agentprdiff_studio.main:app",
            host=args.host,
            port=args.port,
            reload=args.reload,
        )
        return 0

    parser.print_help()
    return 1


if __name__ == "__main__":
    sys.exit(main())
