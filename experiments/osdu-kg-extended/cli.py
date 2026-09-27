"""Command line entry point for the extended Volve graph pipeline."""

from __future__ import annotations

import argparse
import json

from load import load_package
from prepare import prepare
from sources import client_from_env, download_all
from verify import verify


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("command", choices=["download", "prepare", "load", "verify", "all"])
    parser.add_argument("--force", action="store_true", help="redownload remote files")
    args = parser.parse_args()
    result = None
    if args.command in {"download", "all"}:
        result = download_all(client_from_env(), args.force)
    if args.command in {"prepare", "all"}:
        result = prepare()
    if args.command in {"load", "all"}:
        result = load_package(client_from_env())
    if args.command in {"verify", "all"}:
        result = verify(client_from_env())
    print(json.dumps(result, ensure_ascii=False, indent=2))


if __name__ == "__main__":
    main()
