"""Tests for the namespaced Python package and its installed CLI wrapper."""

import contextlib
import importlib
import importlib.util
import io
import os
import socket
from pathlib import Path
import subprocess
import sys
import tempfile
import unittest
import uuid
from unittest.mock import patch


ROOT = Path(__file__).resolve().parents[1]


class PackageTests(unittest.TestCase):
    def setUp(self):
        # The source tree is root-mapped to agent_telemetry at build time. Load it
        # under a private package name here so these tests never occupy or alter
        # the real package name (or the checkout's legacy top-level modules).
        self.package_name = "_agent_telemetry_test_" + uuid.uuid4().hex
        spec = importlib.util.spec_from_file_location(
            self.package_name,
            ROOT / "__init__.py",
            submodule_search_locations=[str(ROOT)],
        )
        package = importlib.util.module_from_spec(spec)
        sys.modules[self.package_name] = package
        spec.loader.exec_module(package)
        self.package = package
        self.addCleanup(self._remove_test_package)

    def _remove_test_package(self):
        for name in tuple(sys.modules):
            if name == self.package_name or name.startswith(self.package_name + "."):
                sys.modules.pop(name, None)

    def import_module(self, name):
        return importlib.import_module(self.package_name + "." + name)

    def test_dashboard_dependencies_keep_one_namespaced_identity(self):
        bare_names = (
            "parser", "native", "session_names", "accounts", "local_servers",
            "local_servers_windows", "ledger_backups", "diagnostics", "session_timeline",
            "release_notes", "native_activity", "mcp_access", "mcp_backend", "mcp_protocol", "mcp_server", "mcp_tools",
            "telemetry_query", "telemetry_insights",
        )
        bare_before = {name: sys.modules.get(name) for name in bare_names}

        dashboard = self.import_module("dashboard")
        parser = self.import_module("parser")
        native = self.import_module("native")
        session_names = self.import_module("session_names")
        accounts = self.import_module("accounts")
        release_notes = self.import_module("release_notes")
        local_servers = self.import_module("local_servers")
        windows_servers = self.import_module("local_servers_windows")
        backups = self.import_module("ledger_backups")
        diagnostics = self.import_module("diagnostics")
        timeline = self.import_module("session_timeline")
        native_activity = self.import_module("native_activity")
        mcp_access = self.import_module("mcp_access")
        mcp_backend = self.import_module("mcp_backend")
        mcp_protocol = self.import_module("mcp_protocol")
        mcp_server = self.import_module("mcp_server")
        mcp_tools = self.import_module("mcp_tools")
        telemetry_query = self.import_module("telemetry_query")
        telemetry_insights = self.import_module("telemetry_insights")

        self.assertIs(dashboard.P, parser)
        self.assertIs(dashboard.N, native)
        self.assertIs(dashboard.SN, session_names)
        self.assertIs(dashboard.A, accounts)
        self.assertIs(dashboard.RN, release_notes)
        self.assertIs(dashboard.LB, backups)
        self.assertIs(dashboard.DG, diagnostics)
        self.assertIs(dashboard.ST, timeline)
        self.assertIs(dashboard.NA, native_activity)
        self.assertIs(dashboard.MA, mcp_access)
        self.assertIs(dashboard.MCP, mcp_backend)
        self.assertIs(mcp_access.native, native)
        self.assertIs(mcp_backend.mcp_access, mcp_access)
        self.assertIs(mcp_backend.mcp_tools, mcp_tools)
        self.assertIs(mcp_backend.telemetry_query, telemetry_query)
        self.assertIs(mcp_backend.telemetry_insights, telemetry_insights)
        self.assertIs(mcp_server.mcp_access, mcp_access)
        self.assertIs(mcp_server.protocol, mcp_protocol)
        self.assertIs(mcp_server.mcp_tools, mcp_tools)
        self.assertIs(telemetry_insights._query_module(), telemetry_query)
        self.assertIs(timeline.normalize_codex, parser.normalize_codex)
        self.assertIs(dashboard.LocalServerInventory, local_servers.LocalServerInventory)
        self.assertIs(local_servers._windows(), windows_servers)
        self.assertIs(parser.A, accounts)
        self.assertIs(accounts.N, native)
        self.assertIs(release_notes.native, native)
        self.assertIs(session_names.N, native)
        for name in bare_names:
            self.assertIs(sys.modules.get(name), bare_before[name],
                          "package imports must not create or replace a bare module alias")

    def test_cli_default_uses_a_per_user_sibling_without_help_or_launch_writes(self):
        native = self.import_module("native")
        cli = self.import_module("cli")
        dashboard = self.import_module("dashboard")

        with tempfile.TemporaryDirectory() as directory:
            home = Path(directory) / "user-home"
            support = home / "Library" / "Application Support" / "AgentTelemetryNative"
            ledger = support.parent / "AgentTelemetry"
            self.assertFalse(home.exists())

            with patch.object(native, "support_dir", return_value=support) as support_dir:
                with patch.object(sys, "argv", ["agent-telemetry", "--help"]):
                    output = io.StringIO()
                    with contextlib.redirect_stdout(output):
                        with self.assertRaises(SystemExit) as raised:
                            cli.main()
                    self.assertEqual(raised.exception.code, 0)
                    self.assertIn("--data-dir", output.getvalue())

                self.assertFalse(home.exists(), "--help must not create the user data directory")
                support_dir.assert_called_once_with()

            seen_argv = []
            with patch.object(native, "support_dir", return_value=support):
                with patch.object(dashboard, "main", side_effect=lambda: seen_argv.append(sys.argv[:]) or 23):
                    with patch.object(sys, "argv", ["agent-telemetry"]):
                        self.assertEqual(cli.main(), 23)

            self.assertEqual(seen_argv, [["agent-telemetry", "--data-dir", str(ledger)]])
            self.assertFalse(home.exists(), "choosing the default path must not create it")

    def test_explicit_data_dir_spellings_are_forwarded_unchanged(self):
        native = self.import_module("native")
        cli = self.import_module("cli")
        dashboard = self.import_module("dashboard")
        cases = (
            ["--data-dir", "/tmp/ledger path"],
            ["--data-dir="],
            ["--data-d", "/tmp/short-option"],
            ["--data-d=/tmp/short-option"],
        )

        for arguments in cases:
            with self.subTest(arguments=arguments):
                seen_argv = []
                with patch.object(native, "support_dir", side_effect=AssertionError("explicit path was replaced")):
                    with patch.object(dashboard, "main", side_effect=lambda: seen_argv.append(sys.argv[:])):
                        with patch.object(sys, "argv", ["agent-telemetry", *arguments]):
                            cli.main()
                self.assertEqual(seen_argv, [["agent-telemetry", *arguments]])

    def test_native_support_paths_are_per_user_and_do_not_create_directories(self):
        native = self.import_module("native")
        with tempfile.TemporaryDirectory() as directory:
            home = Path(directory) / "home"
            local = Path(directory) / "local-app-data"
            xdg = Path(directory) / "xdg-state"
            cases = (
                ("darwin", {}, home / "Library" / "Application Support" / "AgentTelemetryNative"),
                ("win32", {"LOCALAPPDATA": str(local)}, local / "AgentTelemetryNative"),
                ("linux", {}, home / ".local" / "state" / "AgentTelemetryNative"),
                ("linux", {"XDG_STATE_HOME": str(xdg)}, xdg / "AgentTelemetryNative"),
            )
            for platform_name, variables, expected in cases:
                with self.subTest(platform=platform_name, variables=variables):
                    with patch.object(native.sys, "platform", platform_name), \
                            patch.object(native.Path, "home", return_value=home), \
                            patch.dict(os.environ, variables, clear=True):
                        self.assertEqual(native.support_dir(), expected)
                    self.assertFalse(expected.exists(), "resolving a support path must not create it")

    def test_mcp_credentials_use_the_shared_namespaced_native_support_path(self):
        native = self.import_module("native")
        access = self.import_module("mcp_access")
        self.assertIs(access.native, native)
        with tempfile.TemporaryDirectory() as directory:
            support = Path(directory) / "AgentTelemetryNative"
            with patch.object(native, "support_dir", return_value=support):
                self.assertEqual(access._path(), support / "mcp-access.json")
            self.assertFalse(support.exists(), "resolving an MCP credential path must not create it")

    def test_busy_port_refuses_before_any_ledger_action_including_rebuild(self):
        dashboard = self.import_module("dashboard")
        cli = self.import_module("cli")
        with tempfile.TemporaryDirectory() as directory:
            ledger = Path(directory) / ".usage_cache.json"
            ledger.write_bytes(b'{"retained":"private mock archive"}')
            before = ledger.read_bytes()
            occupied = dashboard.Server(("127.0.0.1", 0), dashboard.Handler)
            self.addCleanup(occupied.server_close)
            with patch.object(sys, "argv", ["agenttelemetry", "--port", str(occupied.server_port),
                                               "--data-dir", directory, "--rebuild"]), \
                    patch.object(dashboard, "_configure_data_dir") as configure, \
                    patch.object(dashboard, "load_cache") as load, \
                    patch.object(dashboard, "refresh") as refresh, \
                    patch.object(dashboard, "save_cache") as save, \
                    patch.object(dashboard, "_load_mirrors") as mirrors, \
                    contextlib.redirect_stderr(io.StringIO()) as error:
                with self.assertRaises(SystemExit) as raised:
                    cli.main()
                self.assertEqual(raised.exception.code, 2)
                self.assertIn("cannot listen on 127.0.0.1:", error.getvalue())
                for operation in (configure, load, refresh, save, mirrors):
                    operation.assert_not_called()
            self.assertEqual(ledger.read_bytes(), before)

    def test_failed_data_directory_closes_reserved_socket_without_ledger_access(self):
        dashboard = self.import_module("dashboard")
        server_factory = dashboard.Server
        servers = []

        def reserve(address, handler):
            server = server_factory(address, handler)
            servers.append(server)
            return server

        with patch.object(sys, "argv", ["agenttelemetry", "--port", "0"]), \
                patch.object(dashboard, "Server", side_effect=reserve), \
                patch.object(dashboard, "_configure_data_dir", side_effect=OSError("mock permission denied")), \
                patch.object(dashboard, "load_cache") as load, \
                patch.object(dashboard, "save_cache") as save, \
                contextlib.redirect_stderr(io.StringIO()):
            with self.assertRaises(SystemExit) as raised:
                dashboard.main()
            self.assertEqual(raised.exception.code, 2)
            load.assert_not_called()
            save.assert_not_called()
        self.assertEqual(len(servers), 1)
        self.assertEqual(servers[0].fileno(), -1)

    def test_shutdown_during_initial_scan_flushes_before_releasing_port(self):
        dashboard = self.import_module("dashboard")
        server_factory = dashboard.Server
        servers = []
        handlers = {}
        events = []

        def reserve(address, handler):
            server = server_factory(address, handler)
            servers.append(server)
            return server

        def scan(**kwargs):
            events.append("scan")
            handlers[dashboard.signal.SIGTERM](dashboard.signal.SIGTERM, None)

        def save():
            events.append("save")
            # The listener must still be owned while the final ledger flush runs.
            self.assertGreaterEqual(servers[0].fileno(), 0)
            with socket.socket() as contender:
                with self.assertRaises(OSError):
                    contender.bind(("127.0.0.1", servers[0].server_port))
            return True

        with patch.object(sys, "argv", ["agenttelemetry", "--port", "0"]), \
                patch.object(dashboard, "Server", side_effect=reserve), \
                patch.object(dashboard, "_configure_data_dir"), \
                patch.object(dashboard, "load_cache", side_effect=lambda: events.append("load")), \
                patch.object(dashboard, "refresh", side_effect=scan), \
                patch.object(dashboard, "save_cache", side_effect=save), \
                patch.object(dashboard.signal, "signal", side_effect=lambda signum, handler: handlers.update({signum: handler})), \
                patch.object(dashboard, "_share_stop"), \
                patch.object(dashboard, "_load_mirrors") as mirrors, \
                contextlib.redirect_stderr(io.StringIO()):
            dashboard.main()
            mirrors.assert_not_called()
        self.assertEqual(events, ["load", "scan", "save"])
        self.assertEqual(servers[0].fileno(), -1)

    def test_legacy_checkout_imports_stay_top_level(self):
        script = (
            "import accounts, dashboard, native, native_activity, parser, session_names; "
            "assert dashboard.__package__ == ''; "
            "assert dashboard.P is parser and dashboard.N is native; "
            "assert dashboard.SN is session_names and dashboard.A is accounts; "
            "assert dashboard.NA is native_activity; "
            "assert parser.A is accounts and accounts.N is native"
        )
        environment = os.environ.copy()
        environment["AGENT_TELEMETRY_VERSION"] = "test"
        environment["PYTHONDONTWRITEBYTECODE"] = "1"
        result = subprocess.run(
            [sys.executable, "-c", script],
            cwd=str(ROOT),
            env=environment,
            capture_output=True,
            text=True,
            timeout=15,
        )
        self.assertEqual(result.returncode, 0, result.stderr)


if __name__ == "__main__":
    unittest.main()
