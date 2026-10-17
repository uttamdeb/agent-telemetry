# Local Server Controls Design

**Status:** Approved for implementation planning
**Date:** 2026-10-17

## Problem

AgentTelemetry can discover, list, and stop recent local servers. It cannot start a server again after its process exits, because the inventory stores process identity and working directory but no launch recipe. The menu bar popover also has no way to keep selected servers visible.

## Goals

- Start, stop, and restart managed local servers from the dashboard and the menu bar popover.
- Let the user save a launch command for a server once and reuse it for future starts and restarts.
- Let the user pin servers so they remain easy to find in the menu bar popover across process restarts.
- Keep server lifecycle actions local to this Mac and consistent across both interfaces.

## User experience

### Dashboard

- A running server with a saved launch profile offers **Stop** and **Restart**.
- A running server without a profile offers **Stop** and **Configure**.
- A stopped server with a profile offers **Start**; without a profile it offers **Configure**.
- Configure saves the working directory, expected port, and a user-entered launch command. The command is never inferred from process arguments.
- Each server row has a pin control. Pins remain attached to the saved server profile when its listener PID changes.
- Stop and Restart confirm before terminating a running process. Start requires an explicit user action.

### Menu bar popover

- Keep the existing **Details & settings** disclosure and **Manage all servers** link.
- Show pinned servers before the existing unpinned live/recent server entries.
- Pinned rows show current state and the applicable **Start**, **Stop**, or **Restart** actions, plus a pin toggle.
- Keep the popover usable with several pins by constraining the server list and providing access to the full dashboard list when it overflows.

## Lifecycle behavior

- **Stop:** retain the current SIGTERM behavior and process start-time validation so a stale row cannot signal a reused PID.
- **Start:** run the saved command in its configured working directory, then poll the expected port until it is listening or startup times out. Report success only after the listener is observed.
- **Restart:** confirm, stop the current listener, wait for the expected port to become free, run the saved command, and verify the listener returns. If startup fails, report the error and leave the server stopped rather than retrying silently.
- A discovered server without a launch profile remains stoppable. It cannot be started or restarted until the user configures it.
- Scanning never executes a saved command. A command runs only after the user selects Start or confirms Restart.

## Persistence and identity

- Store launch profiles and pin state locally in the shared per-user local-server data used by the Python dashboard API and the native macOS app.
- Use a stable server-profile identity rather than the listener PID so pin and launch settings survive process replacement.
- Associate a discovered listener with its profile using its configured project directory and expected port. Preserve existing recent-server history during any storage migration.
- Do not sync launch commands or pin state to other machines.

## Command handling and errors

- Save only the launch command entered by the user; do not capture or replay process arguments or environment variables.
- Execute the saved command in the configured directory through the user's login shell so common development commands and shell setup work.
- Keep server control APIs loopback-only and CSRF-protected, following the existing stop endpoint boundary.
- Surface actionable errors for missing commands, unavailable working directories, occupied ports, process identity changes, permission failures, and startup timeouts in both interfaces.

## Out of scope

- Discovering environment variables or secrets from an existing process.
- Automatically starting every discovered server at app launch.
- Remote server management or cloud synchronization of launch profiles.
- Capturing application logs or supervising servers after successful startup.

## Acceptance criteria

- A user can configure a running server once, stop it, start it again, and restart it from both the dashboard and menu bar popover.
- An unconfigured server can still be stopped but never launches an inferred command.
- Pinning a server in either interface keeps it in the popover after its PID changes and while it is stopped.
- Start and Restart report success only after the expected local port is listening; failures remain visible without silently retrying.
- Existing recent-server history, popover settings disclosure, and dashboard navigation continue to work.
