# XEMBRA cluster dashboard (Windows)

The new cluster control plane replaces the single-worker supervisor for normal use.
From the repository folder, using Python with the cluster dependencies installed:

```powershell
py -3.14 scripts/cluster_control.py start
Start-Process http://127.0.0.1:8765
py -3.14 scripts/cluster_control.py status
py -3.14 scripts/cluster_control.py kill
py -3.14 scripts/cluster_control.py resume
py -3.14 scripts/cluster_control.py stop
```

The dashboard stays open after **Kill switch**, so you can inspect crashes and
resume. **Stop** shuts down the entire control plane and dashboard, and also
latches the kill switch. A subsequent `start` preserves that latch; use `resume`
explicitly to allow evolution work again. No startup-at-login service is installed.

## Controls and behavior

- Heartbeat monitoring: guardian, controller, and active worker wrappers publish
  heartbeats. The guardian restarts a controller whose heartbeat is stale for
  20 seconds. Workers stop their cluster child after losing the controller
  heartbeat for 12 seconds. A heartbeat is responsiveness, not model progress.
- Crash analytics: persistent exit codes, times, output tails, failure totals,
  and recent crash details appear in the dashboard.
- Evolution scheduling: default 60-second batch checks. Each batch queues one
  independent three-node evolution run per configured worker. The scheduler
  skips a batch if previous work is still queued/running; it does not accumulate
  missed runs. Pause stops future batches while queued and running work drains.
- Messaging: a durable local SQLite mailbox delivers commands and worker
  completion notices between local daemons. This is single-PC IPC, not remote
  networking or cross-machine model synchronization.
- Autoscaling: queued work starts worker daemons on demand; completed workers
  exit. Default ceiling is two concurrent workers, configurable from one to
  four. Lowering the ceiling lets already-running work finish. The scale decision
  uses queue depth, not CPU or memory demand.
- Resurrection: one guardian owns and restarts the controller after a crash,
  with backoff and a maximum of five rapid failures. It waits at least 13 seconds
  before resurrection so old workers can expire their controller lease. The
  guardian itself is not automatically resurrected if killed or after reboot.
- Kill switch: durable latch blocks scheduling and all worker launches; active
  workers terminate their cluster child. Five worker crashes in five minutes
  automatically latch this switch. Resume does not erase crash history; if the
  failure window still exceeds the limit the circuit breaker latches again.

The dashboard binds only to `127.0.0.1:8765`; browser control requests require
a per-launch token and a matching Host header. It is not exposed to the LAN.
Worker source snapshots, private SQLite databases, generated functions, and
rotating logs live in `logs/cluster/worker-N/`. Workers never concurrently modify
the original project database or generated Python file. Independent evolution
results are reported centrally; evolved functions are not merged across workers.

The mailbox, run history, and crash records are in `logs/cluster/control.db`.
Worker logs rotate at 2 MB with three backups. History, generated Python, and
worker databases remain persistent and are not automatically pruned.

```powershell
py -3.14 scripts/cluster_control.py schedule --interval 120 --max-workers 2
py -3.14 scripts/cluster_control.py pause
py -3.14 scripts/cluster_control.py run
Get-Content logs/cluster/worker-1/worker.log -Tail 30 -Wait
py -3.14 -m unittest discover -s tests -p test_cluster_control.py -v
```

Tests use temporary folders and a tiny stand-in workload, including watchdog
resurrection, actual worker shutdown, crash handling, isolation, mailbox delivery,
and dashboard request protection. They do not alter the real cluster's data.

## Legacy single-worker supervisor

The commands below apply only to the original supervisor. Stop it before using
the new controller; `scripts/daemon.py status` does not report the new cluster.

From this project folder, using a Python installation with the cluster dependencies:

```powershell
py -3.14 scripts/daemon.py start
py -3.14 scripts/daemon.py status
py -3.14 scripts/daemon.py stop
Get-Content logs/daemon/daemon.log -Tail 50
```

`start` detaches a supervisor from the terminal. It runs one cluster worker at a
time, preserving the existing three in-process nodes. Successful runs repeat
after 30 seconds. Failures use exponential backoff up to 60 seconds; ten
consecutive failures stop the supervisor. Each worker has a 300-second limit.
Override these with `--interval 30 --timeout 300` on `start` or `run`.

Logs rotate at 2 MB with five backups. `status.json` records the supervisor PID,
worker PID, cycle, failures, and a heartbeat updated every second. A heartbeat
indicates supervisor activity, not proof that the cluster is making progress.
The Windows file lock prevents duplicate supervisors. The PID is informational;
the stop command uses a stop request rather than killing an unverified PID.

Daemon workers set `XEMBRA_DAEMON=1`, disable interactive plots, and leave
restart decisions to the supervisor. Normal interactive script behavior remains.
Workers retain the project's existing SQLite and generated-code writes.

This does not install a Windows service or start at login/reboot. An OS restart
requires running `start` again. The cluster's generated Python and SQLite data
are not rotated by the supervisor. `stop` terminates an active worker, which may
interrupt its current work. There is no added distributed process cluster,
self-upgrading logic, or automatic restart of the supervisor itself.

For startup problems, inspect `logs/daemon/launcher.log`. Both old project
virtual environments currently refer to missing Python installations; use the
working Python 3.14 installation until those environments are rebuilt.
