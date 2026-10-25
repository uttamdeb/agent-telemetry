"""Keep wheel Python modules on an explicit runtime allowlist."""

from setuptools import setup
from setuptools.command.build_py import build_py


ALLOWED_MODULES = {
    "__init__",
    "__main__",
    "accounts",
    "antigravity",
    "cli",
    "cursor_data",
    "dashboard",
    "editor_snapshot",
    "ledger_backups",
    "diagnostics",
    "session_timeline",
    "native_activity",
    "local_servers",
    "local_servers_windows",
    "native",
    "parser",
    "release_notes",
    "session_names",
    "mcp_access",
    "mcp_backend",
    "mcp_protocol",
    "mcp_server",
    "mcp_tools",
    "telemetry_query",
    "telemetry_insights",
}


class AllowlistedBuildPy(build_py):
    def find_package_modules(self, package, package_dir):
        if package != "agent_telemetry":
            return []
        modules = super().find_package_modules(package, package_dir)
        return [module for module in modules if module[1] in ALLOWED_MODULES]


setup(cmdclass={"build_py": AllowlistedBuildPy})
