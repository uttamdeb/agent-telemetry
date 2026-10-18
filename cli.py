"""Console entry point for an installed AgentTelemetry dashboard."""

import os
import sys

from . import __version__
from . import native


def _has_data_dir_option(arguments):
    """Recognize the explicit option, including argparse's long-option prefixes."""
    for argument in arguments:
        if argument == "--":
            return False
        option = argument.split("=", 1)[0]
        if option.startswith("--") and len(option) > 2 and "--data-dir".startswith(option):
            return True
    return False


def main():
    """Start the server with installed assets and a durable, per-user ledger."""
    old_version = os.environ.get("AGENT_TELEMETRY_VERSION")
    os.environ["AGENT_TELEMETRY_VERSION"] = __version__
    try:
        from . import dashboard
    finally:
        if old_version is None:
            os.environ.pop("AGENT_TELEMETRY_VERSION", None)
        else:
            os.environ["AGENT_TELEMETRY_VERSION"] = old_version

    original_argv = sys.argv[:]
    arguments = original_argv[1:]
    if not _has_data_dir_option(arguments):
        # Match the native clients' ledger location while keeping their control
        # manifest in the separate AgentTelemetryNative sibling directory.
        data_dir = str(native.support_dir().parent / "AgentTelemetry")
        separator = arguments.index("--") if "--" in arguments else len(arguments)
        arguments[separator:separator] = ["--data-dir", data_dir]
        sys.argv = [original_argv[0], *arguments]
    try:
        return dashboard.main()
    finally:
        sys.argv[:] = original_argv
