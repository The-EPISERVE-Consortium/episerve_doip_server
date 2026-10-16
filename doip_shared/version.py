"""CLI/server version constant.

The committed value is a placeholder used by source checkouts and editable
installs. The release workflow overwrites this file before building the
binaries, so a released ``episerve-doip-cli`` reports its git tag
(e.g. ``0.0.6``). See ``.github/workflows/build-doip-cli-binary.yml``.
"""

__version__ = "0.0.0.dev0"
