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


from doip_client import StrictDOIPClient

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

    pass


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
        "--version",
        default="latest",
        help="Commit ID to retrieve (retrieve action only). Defaults to latest.",
    )

    args = parser.parse_args(argv)

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

    client = StrictDOIPClient(
        host=args.host,
        port=args.port,
        use_tls=not args.no_tls,
        verify_tls=args.secure,
    )

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
                r = client.retrieve(args.object_id, component_id=args.component, version=args.version)
                blocks = r.component_blocks
                if not blocks:
                    logging.getLogger().error("Component %s not found.", args.component)
                    return 1
                media_type = blocks[0].media_type
                content = blocks[0].content
                if args.output:
                    with open(args.output, "wb") as f:
                        f.write(content)
                    logging.getLogger().info(f"Wrote to file %s - contains media type '%s' ", args.output, media_type )
                    return 0
                # stdout binary
                sys.stdout.buffer.write(content)
                logging.getLogger().info("\n\n Output contains media type '%s'", media_type)
                return 0

            # Show only meta data - no binary data
            r = client.retrieve(args.object_id)
            print("Metadata:")
            print(json.dumps(r.metadata_blocks, indent=2))

            return 0

        if args.action == "versions":
            r = client.retrieve(args.object_id, "versions")
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


if __name__ == "__main__":
    sys.exit(main())
