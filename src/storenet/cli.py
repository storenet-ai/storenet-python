"""The `storenet` command: create, AI-edit and list shop designs from the shell."""

from __future__ import annotations

import argparse
import contextlib
import json
import sys
from datetime import datetime
from typing import Any

from storenet._version import __version__
from storenet.client import Edit, Site, StorenetClient, _is_uuid
from storenet.errors import StorenetConfigError, StorenetError

EXIT_OK = 0
EXIT_FAILED = 1
EXIT_USAGE = 2
SHORT_ID = 8
# The call may have finished on the server after the client stopped waiting or retrying.
MAYBE_DONE = frozenset({"timeout", "network_error", "409171", "503190", "503310"})


class _Exit(Exception):
    def __init__(self, code: int, message: str) -> None:
        super().__init__(message)
        self.code = code
        self.message = message


def _build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="storenet",
        description="Create, edit with AI and list shop designs via the StoreNet Partner API.",
        epilog="Environment: STORENET_BASE_URL, STORENET_KEY_ID, STORENET_SECRET.",
    )
    parser.add_argument("--version", action="version", version=f"storenet {__version__}")
    groups = parser.add_subparsers(dest="group", metavar="GROUP")
    groups.required = True
    site = groups.add_parser("site", help="web designs")
    actions = site.add_subparsers(dest="action", metavar="COMMAND")
    actions.required = True

    create = actions.add_parser("create", help="create a design from a shop description")
    create.add_argument("prompt", nargs="?", metavar="DESCRIPTION", help="shop description")
    create.add_argument(
        "--file", "-f", metavar="FILE", help="read the description from a file ('-' for stdin)"
    )
    _common_flags(create)

    update = actions.add_parser("update", help="edit a design with AI")
    update.add_argument("site_id", metavar="ID", help="full ID or its first 8 characters")
    update.add_argument(
        "prompt", metavar="REQUEST", help="edit request, e.g. 'switch to a red colour scheme'"
    )
    _common_flags(update)

    listing = actions.add_parser("list", help="list designs created through the API")
    listing.add_argument("--json", action="store_true", help="print the raw JSON payload")
    return parser


def _common_flags(parser: argparse.ArgumentParser) -> None:
    parser.add_argument(
        "--idempotency-key",
        metavar="KEY",
        help="deduplication key; retrying with the same key is safe",
    )
    parser.add_argument("--json", action="store_true", help="print the raw JSON payload")


def _amount(value: float) -> str:
    return f"{value:.4f}".rstrip("0").rstrip(".") or "0"


def _when(moment: datetime | None) -> str:
    return moment.astimezone().strftime("%Y-%m-%d %H:%M") if moment else "-"


def _print_json(payload: Any) -> None:
    print(json.dumps(payload, ensure_ascii=False, indent=2))


def _bullets(title: str, lines: list[str]) -> None:
    if lines:
        print(title)
        for line in lines:
            print(f"  - {line}")


def _read_prompt(args: argparse.Namespace) -> str:
    if args.prompt is not None and args.file is not None:
        raise _Exit(EXIT_USAGE, "pass DESCRIPTION or --file, not both.")
    if args.file is not None:
        try:
            if args.file == "-":
                return str(sys.stdin.read())
            with open(args.file, encoding="utf-8") as handle:
                return handle.read()
        except (OSError, UnicodeDecodeError) as exc:
            raise _Exit(EXIT_USAGE, f"cannot read file {args.file}: {exc}") from None
    if args.prompt is None:
        raise _Exit(EXIT_USAGE, "missing description: pass DESCRIPTION or --file FILE.")
    return str(args.prompt)


def _non_empty(prompt: str) -> str:
    prompt = prompt.strip()
    if not prompt:
        raise _Exit(EXIT_USAGE, "the text is empty.")
    return prompt


def _resolve_id(client: StorenetClient, ref: str) -> str:
    ref = ref.strip()
    if _is_uuid(ref):
        return ref
    if not ref:
        raise _Exit(EXIT_USAGE, "the ID is empty.")
    matches = [site for site in client.sites.list() if site.id.lower().startswith(ref.lower())]
    if not matches:
        raise _Exit(EXIT_FAILED, f"no design has an ID starting with '{ref}'.")
    if len(matches) > 1:
        ids = "\n".join(f"  {site.id}  {site.name}" for site in matches)
        raise _Exit(
            EXIT_USAGE, f"'{ref}' matches {len(matches)} designs, type more characters:\n{ids}"
        )
    return matches[0].id


def _print_site(site: Site) -> None:
    print(f"Created: {site.name} ({site.industry})")
    print(f"ID: {site.id}")
    print(f"Preview: {site.url}")


def _print_edit(edit: Edit) -> None:
    if edit.applied:
        _bullets("Changed:", edit.changed)
    else:
        print(f"The AI changed nothing ({edit.outcome}).")
    _bullets("Not done:", edit.refused + edit.unsupported)
    if edit.applied:
        credit = f"Credits: -{_amount(edit.credits_used)}"
        if edit.credits_available is not None:
            credit += f" ({_amount(edit.credits_available)} left)"
        print(credit)
    print(f"Preview: {edit.url}")


def _print_table(sites: list[Site]) -> None:
    header = ("ID", "NAME", "INDUSTRY", "UPDATED", "LINK")
    rows = [
        (site.id[:SHORT_ID], site.name, site.industry, _when(site.updated_at), site.url)
        for site in sites
    ]
    widths = [max(len(row[col]) for row in [header, *rows]) for col in range(len(header) - 1)]
    for row in [header, *rows]:
        cells = [cell.ljust(width) for cell, width in zip(row[:-1], widths, strict=True)]
        print("  ".join([*cells, row[-1]]))
    print(f"Total: {len(sites)} designs")


def _run(args: argparse.Namespace) -> int:
    if args.action == "create":
        prompt = _non_empty(_read_prompt(args))
        client = StorenetClient.from_env()
        data = client.sites._create_data(prompt, args.idempotency_key)
        if args.json:
            _print_json(data)
        else:
            _print_site(client._site(data))
        return EXIT_OK
    if args.action == "update":
        prompt = _non_empty(args.prompt)
        client = StorenetClient.from_env()
        site_id = _resolve_id(client, args.site_id)
        data = client.sites._update_data(site_id, prompt, args.idempotency_key)
        edit = client._edit(data)
        if args.json:
            _print_json(data)
        else:
            _print_edit(edit)
        return EXIT_OK if edit.applied else EXIT_FAILED
    client = StorenetClient.from_env()
    items = client.sites._list_data()
    if args.json:
        _print_json(items)
        return EXIT_OK
    if not items:
        print('No designs yet. Create the first one: storenet site create "<description>"')
        return EXIT_OK
    _print_table([client._site(item) for item in items])
    return EXIT_OK


def _tolerate_unencodable_output() -> None:
    # Shop names and server messages may be non-ASCII; a non-UTF-8 console must not crash.
    for stream in (sys.stdout, sys.stderr):
        reconfigure = getattr(stream, "reconfigure", None)
        if reconfigure is not None:
            with contextlib.suppress(ValueError, OSError):
                reconfigure(errors="replace")


def main(argv: list[str] | None = None) -> int:
    """Run the CLI and return its exit code."""
    _tolerate_unencodable_output()
    try:
        args = _build_parser().parse_args(argv)
    except SystemExit as exc:
        return exc.code if isinstance(exc.code, int) else (EXIT_OK if exc.code is None else 1)
    try:
        return _run(args)
    except _Exit as exc:
        print(f"Error: {exc.message}", file=sys.stderr)
        return exc.code
    except StorenetConfigError as exc:
        print(f"Error: {exc.message}", file=sys.stderr)
        return EXIT_USAGE
    except StorenetError as exc:
        print(f"Error {exc.code}: {exc.message}", file=sys.stderr)
        key = exc.details.get("idempotency_key")
        if exc.code in MAYBE_DONE and isinstance(key, str):
            print(
                "This request may have completed on the server. Run again with "
                f"--idempotency-key {key} to get the result without being charged again.",
                file=sys.stderr,
            )
        return EXIT_FAILED
    except KeyboardInterrupt:
        return 130


if __name__ == "__main__":
    sys.exit(main())
