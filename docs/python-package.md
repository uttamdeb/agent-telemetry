# Python package

The Python package requires Python 3.9+ and runs the same local browser dashboard as
`python3 dashboard.py` (the source-checkout workflow still supports Python 3.8+).
It has no third-party runtime dependencies. The release wheels bundle the dashboard's
HTML, JavaScript, styles, icons, and vendored Chart.js. Usage stays local by default;
optional sharing and account lookups retain their existing explicit consent controls.
The package also includes the optional stdlib-only MCP bridge and Settings assets; MCP
access remains disabled until enabled and scoped in the dashboard.

The PyPI distribution is named `agenttelemetry-app`. Its installed commands are
`agenttelemetry-app`, `agenttelemetry`, and `agent-telemetry`;
`python -m agent_telemetry` is also supported.
The command starts the local service at `http://127.0.0.1:7878`. First launch parses
the existing local logs and may take a while on large collections.

Install into the active Python environment and start the dashboard:

```sh
python -m pip install agenttelemetry-app
agenttelemetry
```

Or, with [uv](https://docs.astral.sh/uv/getting-started/installation/) installed, run:

```sh
uvx agenttelemetry-app
```

`uvx` creates an isolated temporary environment for the command. Both launch modes keep
the durable ledger outside the installed package, alongside the native app's ledger when
one is present: `~/Library/Application Support/AgentTelemetry` on macOS,
`%LOCALAPPDATA%\AgentTelemetry` on Windows, and
`$XDG_STATE_HOME/AgentTelemetry` or `~/.local/state/AgentTelemetry` on Linux. The
`--data-dir PATH` option overrides that location. Running `python3 dashboard.py` from a
source checkout keeps the existing checkout-local default. If the requested service
port is already occupied, launch stops before loading or writing a ledger. Stop the
existing service before launching another copy; when intentionally running separate
services, give each a distinct `--port` and `--data-dir`.

## Maintainer release procedure

Build artifacts from a clean, reviewed source tree after setting the intended package
distribution version in `pyproject.toml`. For a coordinated release, keep the package
distribution version in `pyproject.toml`, the application version in `__init__.py`, and
the bundled MCP server version aligned (all are `2.1.0` for this release). PyPI files are immutable:

```sh
python -m pip install --upgrade build twine
python -m build
python -m twine check dist/*
python tests/check_python_distribution.py dist
```

The default build creates an sdist, then builds the wheel from that archive. CI runs the
archive checks and isolated installation/HTTP resource smoke test on Linux, macOS, and
Windows. It never publishes packages.

Inspect both archives before upload. The wheel should contain only the `agent_telemetry`
allowlisted modules and declared dashboard assets; the sdist should contain the explicit
files in `MANIFEST.in`. Neither archive may contain `.usage_cache.json`, `.peers.json`,
`.peers/`, `server.log`, credentials, tests, native build outputs, or website-only assets.
Verify a test installation in an isolated environment, including `agenttelemetry --help`
and a mock-data launch with an explicit temporary `--data-dir`.

For a release candidate, upload only to TestPyPI and verify the installation there first:

```sh
python -m twine upload --repository testpypi dist/*
```

Production publication is a separate manual release action. It requires explicit release
approval, a new immutable version, reviewed wheel/sdist contents, and production
credentials; no upload runs as part of this repository's normal build or CI checks.
