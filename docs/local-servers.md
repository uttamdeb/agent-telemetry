# Local server controls

The **Local servers** dashboard view supports macOS and Windows: discover TCP
listeners owned by your account, configure a launch command, pin a server, and
start, stop or restart it. The macOS menu includes pinned server controls. The
Windows tray’s **Local servers** button opens these controls in the browser, starting
monitoring first if needed. A ready, updated AgentTelemetry backend is required.
Linux displays an unsupported state; its telemetry dashboard continues to work.

## What is observed

macOS discovery uses the local `lsof` and `ps` utilities. A row identifies a process by its
PID, recorded start time and listening port. The working directory supplies the
project name. Listeners with an unavailable working directory, system directories,
and AgentTelemetry's own backend are excluded. This is a filtered listener inventory,
not a complete inventory of applications or websites. It does not inspect process
arguments, credentials or other processes' environment variables.

Windows uses Win32 TCP tables, process token ownership and exact process creation
times. It does not inspect foreign process memory. Windows does not expose a
supported working-directory query for other processes, so newly discovered rows
show the process and port; **Configure** asks for an absolute working folder before
saving or pinning. That folder is associated only with the exact observed process
and port. An unrelated replacement on the same port never inherits the saved recipe.

TCP does not identify HTTP versus HTTPS or another protocol. A detected port is not
proof that a web page is ready. Discovery failures remain errors, with the previous
successful list marked stale, rather than being interpreted as every server stopping.

## Saved commands and process actions

Configure stores the command typed by the user with the discovered (macOS) or
user-selected (Windows) working directory and expected port. Saving and pinning never run a command. Start and Restart require
explicit confirmation. macOS executes the saved command through `/bin/zsh -lc`;
its shell startup files may run. Windows uses the system `cmd.exe /d /s /c`, without
Command Processor AutoRun commands. Both run in the saved directory. The command inherits
the backend's normal environment, excluding AgentTelemetry's backend control token;
it does not recover the original server's environment or terminal setup.
The inventory scan makes no network requests. A command you explicitly run can access
files and the network with your account's permissions.

On macOS, Stop sends SIGTERM after checking process identity again. On Windows,
Stop calls `TerminateProcess` on a single held handle after verifying its owner and
creation time. **Windows stops immediately; unsaved work can be lost.** The dashboard
explains this before confirmation. Other ports owned by that process can stop too. Restart validates the saved
profile, stops the confirmed process, waits, then launches the saved command. A
changed process is refused. Commands that daemonize or deliberately leave the launch
process group (macOS) or launch job (Windows) cannot always be attributed safely
and may remain unverified. Windows assigns the suspended command to a private job
before resuming it, then verifies that the expected listener belongs to that job.
Jobs do not have kill-on-close enabled.

Startup must be observed at the expected port before it is reported successful. A
timeout or launch error is shown without silently retrying. AgentTelemetry is not a
process supervisor: it does not capture server output, force-kill on timeout, restore
terminal sessions, or automatically restart a server after it exits. Inspect the
server before retrying a timed-out command, since it may still be starting.

## Storage and privacy

Recent observations, pins and commands are private to this installation, in
`.local_servers.json` beside the backend's selected data directory. They are not part
of the telemetry ledger, health, summary or device-sharing payloads. Commands can
contain secrets; use environment references rather than saving credentials in them.
The file is excluded from Git and installation payloads. Unreadable or corrupt
inventory files are preserved and reported, not silently replaced with empty data.

The API requires a loopback client. Writes additionally require the dashboard's
same-origin JSON guard. Start, Stop and Restart require `confirmed:true`; Restart
also identifies the exact server row that was confirmed. Inventory updates use both
an in-process lock and a file lock so separate requests and backend processes do not
overwrite each other's changes. No telemetry cache migration or rebuild is required.

Implementation references: [Apple's file-lock semantics](https://developer.apple.com/library/archive/documentation/System/Conceptual/ManPages_iPhoneOS/man2/fcntl.2.html)
and Python's [subprocess environment and session controls](https://docs.python.org/3/library/subprocess.html#popen-constructor).

Windows API references: [TCP tables](https://learn.microsoft.com/en-us/windows/win32/api/iphlpapi/nf-iphlpapi-getextendedtcptable), [job objects](https://learn.microsoft.com/en-us/windows/win32/procthread/job-objects), and [process termination](https://learn.microsoft.com/en-us/windows/win32/api/processthreadsapi/nf-processthreadsapi-terminateprocess).
