# XEMBRA Cluster Dashboard

**Container image:** `ghcr.io/probablyapigeon/xembra-cluster-dashboard:latest`.
Pull with `docker pull ghcr.io/probablyapigeon/xembra-cluster-dashboard:latest`,
then run `docker compose -f compose.pull.yaml up -d` from this repository.
See [DOCKER.md](DOCKER.md) for package visibility and operating instructions.

**Docker:** `docker compose up -d --build`, then open
<http://127.0.0.1:8766>. See [DOCKER.md](DOCKER.md) for persistent storage,
controls, and container tests.

**Networked nodes:** see [NETWORK.md](NETWORK.md) for authenticated node agents,
shared compression functions, persistent evolution state, majority-voted QT
synchronization, a network mailbox, and replicated events. This new mode has a
separate coordinator/dashboard at port 8770. It retains a single coordinator;
automatic leader election and failover are not implemented.

A local Windows dashboard for scheduled XEMBRA evolution runs, worker health,
crash history, and daemon controls. The controller and dashboard use Python's
standard library; the bundled evolution workload needs `requirements.txt`.

## Quick start

Requires Windows and Python 3.14 (the version used for verification).

```powershell
git clone https://github.com/probablyapigeon/XEMBRA-Cluster-Dashboard.git
cd XEMBRA-Cluster-Dashboard
py -3.14 -m venv .venv
.\.venv\Scripts\python.exe -m pip install -r requirements.txt
.\.venv\Scripts\python.exe scripts/cluster_control.py start
Start-Process http://127.0.0.1:8765
```

The dashboard binds to localhost only. Keep the same Python interpreter for
subsequent commands; detached workers inherit it. The default schedule starts
two independent evolution workers, with a batch check every 60 seconds.

## Included

- Guardian, controller, and worker heartbeats.
- Persistent crash records with exit codes and output tails.
- Cluster-wide evolution batch scheduling.
- A durable SQLite message bus for local daemon commands and completion notices.
- Queue-based autoscaling, with a configurable ceiling of one to four workers.
- Guardian recovery for controller crashes and stale heartbeats.
- A persistent kill switch that also overrides automatic recovery.
- Responsive browser dashboard with schedule and run controls.

Each worker maintains its own SQLite data and generated Python functions.
The original local mode does not exchange functions or communicate across
machines. Use the separate network mode above for those capabilities.

## Control commands

```powershell
.\.venv\Scripts\python.exe scripts/cluster_control.py status
.\.venv\Scripts\python.exe scripts/cluster_control.py pause
.\.venv\Scripts\python.exe scripts/cluster_control.py run
.\.venv\Scripts\python.exe scripts/cluster_control.py kill
.\.venv\Scripts\python.exe scripts/cluster_control.py resume
.\.venv\Scripts\python.exe scripts/cluster_control.py schedule --interval 120 --max-workers 2
.\.venv\Scripts\python.exe scripts/cluster_control.py stop
```

`kill` leaves the dashboard running for inspection. `stop` shuts down the control
plane and latches the kill switch. After starting again, explicitly `resume` to
allow work. Neither command deletes history. No Windows service or automatic
startup after reboot is installed; the guardian itself is not self-resurrecting.

Runtime data stays under `logs/cluster/` and is excluded from Git. Logs rotate;
SQLite history, generated code, and worker databases are not automatically pruned.
See [DAEMON.md](DAEMON.md) for recovery limits and operational details.

## Verification

```powershell
py -3.14 -m unittest discover -s tests -p test_cluster_control.py -v
```

The six integration tests use temporary folders and small stand-in workloads to
check isolation, messaging, crash handling, kill-switch behavior, watchdog
resurrection, worker cleanup after controller loss, and dashboard authorization.
They need no model dependencies. Real XEMBRA workers have also completed runs on
the development machine. A fresh dependency installation is not covered by these
tests. Browser rendering has not been visually verified.

## License

The existing XEMBRA license is preserved in [LICENSE](LICENSE). It is a restricted
license, not an open-source license.
