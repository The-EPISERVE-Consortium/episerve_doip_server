"""Lightweight CLI for exercising the strict DOIP client."""
# Example:
# python -m client_cli.main --action retrieve --object-id Q6190920 --output .

from __future__ import annotations

import json
import logging
import os
import sys

from argparse import (
    ArgumentParser,
    RawDescriptionHelpFormatter,
    ArgumentDefaultsHelpFormatter,
)


from client_cli import update_check
from doip_client import StrictDOIPClient
from doip_shared.constants import MSG_TYPE_ERROR
from doip_shared.version import __version__

logging.basicConfig(
    level=logging.DEBUG,
    format="%(asctime)s %(levelname)s %(message)s",
    force=True
)


def _print_banner() -> None:
    if os.name == "nt":
        import ctypes
        ctypes.windll.kernel32.SetConsoleMode(
            ctypes.windll.kernel32.GetStdHandle(-11), 7
        )
    CYAN = "\033[36m"
    RESET = "\033[0m"
    banner = (
        "\n"
        "██████  ████▄   ██   ████  ██████  ████▄   ██   ██  ██████\n"
        "██▄▄    ██▄▄█▀  ██  ▄▄▄██  ██▄▄    ██▄▄█▀   ██ ██   ██▄▄  \n"
        "██████  ██      ██  ████▀  ██████  ██  ▀█    ▀█▀    ██████\n"
        "\n"
        "        Epidemiological Surveillance Platform\n"
    )
    print(CYAN + banner + RESET, file=sys.stderr)


# Combine both formatters to allow newlines and showing default arguments
class RawDescriptionDefaultsHelpFormatter(
    RawDescriptionHelpFormatter,
    ArgumentDefaultsHelpFormatter,
):
    """Argument formatter combining defaults with raw description rendering."""


def _resolve_cli_read_token(explicit_token: str | None) -> str | None:
    """Resolve the read token for restricted objects from CLI input or the environment.

    Args:
        explicit_token: Token passed via ``--read-token``.

    Returns:
        str | None: ``--read-token``, else ``DOIP_READ_TOKEN``, else the update token
        ``DOIP_UPDATE_TOKEN`` (the server accepts it for restricted objects by default),
        or ``None`` when unavailable.
    """
    return explicit_token or os.getenv("DOIP_READ_TOKEN") or os.getenv("DOIP_UPDATE_TOKEN") or None


def _range_kwargs(args) -> dict:
    """Return the ``size``/``offset`` keyword arguments for ``client.retrieve``.

    Only options that were given are included, so a plain retrieve is unchanged.

    Raises:
        ValueError: If ``--size`` is not positive, or is combined with a negative ``--offset``.
    """
    kwargs = {}
    if args.size is not None:
        if args.size < 1:
            raise ValueError("--size must be a positive integer.")
        kwargs["size"] = args.size
    if args.offset is not None:
        if args.offset < 0 and args.size is not None:
            raise ValueError("--size cannot be combined with a negative --offset.")
        kwargs["offset"] = args.offset
    return kwargs


def _range_info(r) -> dict:
    """Summarise a partial retrieve (offset, total size, partial flag) from the response metadata."""
    meta = r.metadata_blocks[0] if r.metadata_blocks else {}
    return {k: meta[k] for k in ("offset", "totalSize", "partial") if k in meta}


def _raise_if_range_unsatisfiable(r) -> None:
    """Raise ValueError when the server reports the requested range starts beyond the component's end."""
    meta = r.metadata_blocks[0] if r.metadata_blocks else {}
    if meta.get("rangeNotSatisfiable"):
        raise ValueError(f"Requested range is beyond the end of the component ({meta.get('totalSize')} bytes).")


def _raise_if_denied(response) -> None:
    """Raise a clear error when the server refused access to a restricted object."""
    if response.header.msg_type != MSG_TYPE_ERROR:
        return
    block = response.metadata_blocks[0] if response.metadata_blocks else {}
    if block.get("error") == "AccessDeniedError":
        raise ValueError("Access denied: this object is restricted. Pass --read-token or set DOIP_READ_TOKEN.")


def _raise_if_error(response) -> None:
    """Raise when the server answered with an error message (for example an unknown object).

    The server reports such errors as a response of type ``MSG_TYPE_ERROR`` whose first metadata block is
    ``{"error": <kind>, "message": <text>}``; without this check the CLI would report them as a success.
    """
    _raise_if_denied(response)
    if response.header.msg_type != MSG_TYPE_ERROR:
        return
    block = response.metadata_blocks[0] if response.metadata_blocks else {}
    kind, message = block.get("error"), block.get("message")
    raise ValueError(f"{kind or 'Error'}: {message}" if message else str(kind or "The server returned an error."))


def _resolve_cli_update_token(explicit_token: str | None) -> str | None:
    """Resolve the update token from CLI input or the environment.

    Args:
        explicit_token: Token passed via the CLI.

    Returns:
        str | None: Resolved update token, or ``None`` when unavailable.
    """
    if explicit_token:
        return explicit_token
    env_token = os.getenv("DOIP_UPDATE_TOKEN")
    return env_token or None


def _emit_json(action: str, object_id: str | None, ok: bool,
               result=None, error: str | None = None) -> None:
    """Print the single machine-readable result envelope to stdout.

    Args:
        action: The action that was requested.
        object_id: Target object identifier, or None for object-less actions.
        ok: Whether the action succeeded.
        result: Success payload (omitted when ``ok`` is False).
        error: Error message (omitted when ``ok`` is True).

    Returns:
        None
    """
    payload: dict = {"action": action, "object_id": object_id, "ok": ok}
    if ok:
        payload["result"] = result
    else:
        payload["error"] = error
    print(json.dumps(payload, indent=2))


def _maybe_announce_update(args) -> None:
    """Print an update notice on stderr when a newer release exists.

    Best-effort and silent on every failure, so it never affects the action's
    output or exit code.
    """
    if not update_check.should_check(
        force_json_output=args.force_json_output,
        no_cli_update_check=args.no_cli_update_check,
    ):
        return
    info = update_check.check_for_update(__version__)
    if info:
        print(update_check.format_notice(info), file=sys.stderr)


def _run_cli_version() -> int:
    """Handle ``--cli-version``: print the version, then report on updates.

    The version always goes to stdout so it can be captured; the update status
    goes to stderr. As this is the only way to check for updates outside an
    interactive run, it queries the network directly (bypassing the cache) and
    reports failures rather than staying silent.

    Returns:
        int: 0 when up to date (or un-stamped), 1 when a newer release exists,
        2 when the lookup itself failed.
    """
    print(f"episerve-doip-cli {__version__}")

    if update_check._is_placeholder(update_check.parse_version(__version__)):
        print(
            "No update check was performed: this is a development build.",
            file=sys.stderr,
        )
        return 0
    try:
        info = update_check.check_explicitly(__version__)
    except Exception:
        print(f"Cannot reach {update_check.HOST} to check for updates.", file=sys.stderr)
        return 2
    if info:
        print(update_check.format_notice(info), file=sys.stderr)
        return 1
    print("This is the latest release.", file=sys.stderr)
    return 0


def _run_json_action(client: StrictDOIPClient, args) -> tuple[str | None, object]:
    """Execute the requested action and return ``(object_id, result_payload)``.

    Raises on any failure so the caller can render a uniform error envelope.

    Args:
        client: Connected DOIP client.
        args: Parsed CLI arguments.

    Returns:
        tuple[str | None, object]: The object id echoed back (None for
        object-less actions) and the JSON-serialisable result payload.
    """
    action = args.action
    oid = None if action in ("hello", "list_ops") else args.object_id

    if action == "hello":
        return oid, client.hello()
    if action == "list_ops":
        return oid, client.list_ops()
    if action == "purge":
        return oid, client.purge(args.object_id)
    if action == "versions":
        r = client.retrieve(args.object_id, "versions")
        _raise_if_error(r)
        versions = r.metadata_blocks[0].get("versions", []) if r.metadata_blocks else []
        return oid, {"versions": versions}
    if action == "retrieve":
        if args.component:
            if not args.output:
                raise ValueError(
                    "--output is required for a component retrieve when "
                    "--force-json-output is set (binary content cannot share stdout)."
                )
            r = client.retrieve(args.object_id, component_id=args.component, version=args.version,
                                token=_resolve_cli_read_token(args.read_token), **_range_kwargs(args))
            _raise_if_error(r)
            _raise_if_range_unsatisfiable(r)
            if not r.component_blocks:
                raise ValueError(f"Component {args.component} not found.")
            block = r.component_blocks[0]
            try:
                with open(args.output, "wb") as fh:
                    fh.write(block.content)
            except OSError as exc:
                raise ValueError(f"Could not write {args.output}: {exc}") from exc
            return oid, {
                "saved_to": args.output,
                "media_type": block.media_type,
                "bytes": len(block.content),
                **_range_info(r),
            }
        r = client.retrieve(args.object_id)
        _raise_if_error(r)
        return oid, {"metadata_blocks": r.metadata_blocks}
    if action == "invoke":
        try:
            params = json.loads(args.params)
        except Exception:
            params = {}
        r = client.invoke(args.object_id, args.workflow, params=params)
        _raise_if_error(r)
        return oid, {"metadata_blocks": r.metadata_blocks}
    if action == "update":
        if not args.component:
            raise ValueError("--component is required for update.")
        if not args.input:
            raise ValueError("--input is required for update.")
        update_token = _resolve_cli_update_token(args.update_token)
        if not update_token:
            raise ValueError("Update authorization requires --update-token or DOIP_UPDATE_TOKEN.")
        try:
            with open(args.input, "rb") as fh:
                content = fh.read()
        except OSError as exc:
            raise ValueError(f"Could not read {args.input}: {exc}") from exc
        media_type = args.media_type or "application/octet-stream"
        r = client.update_component(
            args.object_id, args.component, content,
            media_type=media_type, update_token=update_token,
        )
        _raise_if_error(r)
        return oid, {"metadata_blocks": r.metadata_blocks}
    raise ValueError(f"Action {action!r} is not supported with --force-json-output.")


def main(argv: list[str] | None = None) -> int:
    """CLI entrypoint for interacting with the strict DOIP client.

    Args:
        argv: Optional list of arguments (defaults to ``sys.argv``).

    Returns:
        int: Process exit code (0 on success, non-zero on error).
    """
    parser = ArgumentParser(
        prog="episerve-doip-cli",
        description=(
            "This is the EPISERVE DOIP client.\n\n"
            "This client enables direct interaction with the EPISERVE DOIP server for retrieving object "
            "metadata or content, and executing predefined server workflows.\n"
            "To see a demo with standard values, execute: python -m client_cli.main --action demo\n"
            "For more information see: https://doip.episerve.zib.de"
        ),
        add_help=False,
        formatter_class=RawDescriptionDefaultsHelpFormatter,
    )

    parser.add_argument("-h", "--help", action="store_true", default=False,
                        help="show extended help and exit")
    parser.add_argument("--host", default="doip.episerve.zib.de", help="DOIP Server hostname")
    parser.add_argument("--port", type=int, default=3567, help="Server port")
    parser.add_argument("--no-tls", action="store_true", help="Disable TLS wrapping")
    parser.add_argument(
        "--force-json-output",
        action="store_true",
        help="Emit a single machine-readable JSON envelope on stdout "
             "({action, object_id, ok, result|error}); suppress the banner and debug logging.",
    )
    parser.add_argument("--secure", action="store_true", help="Enable TLS verification (if you do not use a self-certified cert)")
    parser.add_argument("--object-id", default="Q123", help="Object identifier")
    parser.add_argument("--component", default=None, help="Component ID for selective retrieve; if absent, list components")
    parser.add_argument(
        "--action",
        choices=["demo", "hello", "list_ops", "retrieve", "versions", "update", "invoke", "purge"],
        help="Action to execute",
    )
    # component removed: server no longer supports component selection
    parser.add_argument(
        "--output",
        default=None,
        help="Path to save first component (retrieve only). If not specified, component is not saved.",
    )
    parser.add_argument(
        "--input",
        default=None,
        help="Path to the file to upload for update.",
    )
    parser.add_argument(
        "--media-type",
        default=None,
        help="Media type for update uploads. If omitted, application/octet-stream is used.",
    )
    parser.add_argument(
        "--read-token",
        default=None,
        help="Token for restricted objects (component retrieve). Defaults to DOIP_READ_TOKEN, then DOIP_UPDATE_TOKEN, when omitted.",
    )
    parser.add_argument(
        "--update-token",
        default=None,
        help="Shared secret for update uploads. Defaults to DOIP_UPDATE_TOKEN when omitted.",
    )
    parser.add_argument(
        "--workflow",
        default="equation_extraction",
        help="Workflow name (for invoke)",
    )
    parser.add_argument(
        "--params",
        default="{}",
        help="Workflow params as JSON string (for invoke)",
    )
    parser.add_argument(
        "--size",
        type=int,
        default=None,
        help="Component retrieve only: fetch just this many bytes (from --offset, default the start) "
             "instead of the whole component.",
    )
    parser.add_argument(
        "--offset",
        type=int,
        default=None,
        help="Component retrieve only: first byte to fetch. Negative counts from the end "
             "(e.g. --offset -8 for the last 8 bytes; cannot be combined with --size). "
             "Without --size, fetches from the offset to the end.",
    )
    parser.add_argument(
        "--version",
        default="latest",
        help="Commit ID to retrieve (retrieve action only). Defaults to latest.",
    )
    parser.add_argument(
        "--cli-version",
        action="store_true",
        default=False,
        help="Print this client's version, check whether a newer release exists, "
             "and exit (exit codes: 0 up to date, 1 update available, "
             "2 check failed).",
    )
    parser.add_argument(
        "--no-cli-update-check",
        action="store_true",
        default=False,
        help="Skip the automatic startup update check (also "
             f"{update_check.ENV_OPT_OUT}=1).",
    )

    args = parser.parse_args(argv)

    if args.size is not None or args.offset is not None:
        if not (args.action == "retrieve" and args.component):
            parser.error("--size and --offset only apply to --action retrieve with --component.")
        try:
            _range_kwargs(args)
        except ValueError as exc:
            parser.error(str(exc))

    if args.cli_version:
        return _run_cli_version()

    if args.force_json_output:
        logging.disable(logging.INFO)  # keep WARNING+ on stderr, drop the DEBUG/INFO stream

    if args.help:
        _print_banner()
        parser.print_help()
        return 0

    if args.action is None:
        _print_banner()

    logging.getLogger().debug("Handling action: %s", args.action)

    if args.action is None:
        print(parser.format_usage(), end="")
        print(parser.description)
        print()
        print("options:")
        print("  -h, --help            show extended help and exit")
        return 1

    _maybe_announce_update(args)

    client = StrictDOIPClient(
        host=args.host,
        port=args.port,
        use_tls=not args.no_tls,
        verify_tls=args.secure,
    )

    if args.force_json_output:
        oid = None if args.action in ("hello", "list_ops") else args.object_id
        try:
            oid, result = _run_json_action(client, args)
            _emit_json(args.action, oid, True, result=result)
            return 0
        except Exception as exc:
            _emit_json(args.action, oid, False, error=str(exc))
            return 1

    try:
        if args.action == "hello":
            r = client.hello()
            print(json.dumps(r, indent=2))
            return 0

        if args.action == "list_ops":
            r = client.list_ops()
            print(json.dumps(r, indent=2))
            return 0

        if args.action == "retrieve":
            if args.component:
                r = client.retrieve(args.object_id, component_id=args.component, version=args.version,
                                    token=_resolve_cli_read_token(args.read_token), **_range_kwargs(args))
                _raise_if_error(r)
                try:
                    _raise_if_range_unsatisfiable(r)
                except ValueError as exc:
                    logging.getLogger().error("%s", exc)
                    return 1
                blocks = r.component_blocks
                if not blocks:
                    logging.getLogger().error("Component %s not found.", args.component)
                    return 1
                media_type = blocks[0].media_type
                content = blocks[0].content
                if args.output:
                    with open(args.output, "wb") as f:
                        f.write(content)
                    logging.getLogger().info("Wrote to file %s - contains media type '%s' ", args.output, media_type )
                    return 0
                # stdout binary
                sys.stdout.buffer.write(content)
                logging.getLogger().info("\n\n Output contains media type '%s'", media_type)
                return 0

            # Show only meta data - no binary data
            r = client.retrieve(args.object_id)
            _raise_if_error(r)
            print("Metadata:")
            print(json.dumps(r.metadata_blocks, indent=2))

            return 0

        if args.action == "versions":
            r = client.retrieve(args.object_id, "versions")
            _raise_if_error(r)
            if not r.metadata_blocks:
                logging.getLogger().error("Object %s not found.", args.object_id)
                return 1
            versions = r.metadata_blocks[0].get("versions", [])
            print(json.dumps(versions, indent=2))
            return 0

        if args.action == "invoke":
            try:
                p = json.loads(args.params)
            except Exception:
                p = {}
            r = client.invoke(args.object_id, args.workflow, params=p)
            _raise_if_error(r)
            print(json.dumps(r.metadata_blocks, indent=2))
            return 0

        if args.action == "update":
            if not args.component:
                logging.getLogger().error("--component is required for update.")
                return 1
            if not args.input:
                logging.getLogger().error("--input is required for update.")
                return 1
            update_token = _resolve_cli_update_token(args.update_token)
            if not update_token:
                logging.getLogger().error(
                    "Update authorization requires --update-token or DOIP_UPDATE_TOKEN."
                )
                return 1

            with open(args.input, "rb") as f:
                content = f.read()

            media_type = args.media_type or "application/octet-stream"
            r = client.update_component(
                args.object_id,
                args.component,
                content,
                media_type=media_type,
                update_token=update_token,
            )
            _raise_if_error(r)
            print(json.dumps(r.metadata_blocks, indent=2))
            return 0

        if args.action == "purge":
            r = client.purge(args.object_id)
            print(json.dumps(r, indent=2))
            return 0

        if args.action == "demo":
            logging.getLogger().info("Contacting DOIP server (using: %s:%s)...", args.host, args.port )
            r = client.hello()
            print(json.dumps(r, indent=2))
            meta = client.retrieve(args.object_id)
            print(json.dumps(meta.metadata_blocks, indent=2))
            return 0

    except Exception as exc:
        sys.stderr.write(
            f"Error contacting DOIP server {args.host}:{args.port}: {exc}\n"
        )
        return 1

    # Unreachable while --action is restricted to the choices above, but keeps
    # the declared return type honest if that ever changes.
    return 1


if __name__ == "__main__":
    sys.exit(main())
