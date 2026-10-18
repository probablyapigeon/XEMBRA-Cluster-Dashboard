# Networked XEMBRA nodes

This mode adds a coordinator and persistent worker agents. It is separate from
the original local daemon at port 8765; its dashboard defaults to port 8770.
Do not run both modes against the same worker state directories.

## Two nodes on one PC

Install `requirements.txt`, then use three PowerShell terminals from this checkout.
Use the Python interpreter where you installed the dependencies (`py -3.14`
below, or `.\.venv\Scripts\python.exe`).

Create the administrator secret once; the command does not print it:

```powershell
py -3.14 scripts/network_cluster.py init-token
```

Terminal 1:

```powershell
py -3.14 scripts/network_cluster.py coordinator --nodes node-a,node-b
```

Terminal 2:

```powershell
py -3.14 scripts/network_cluster.py issue-join --node node-a --output logs/network/node-a.ticket
py -3.14 scripts/network_cluster.py join --node node-a --ticket-file logs/network/node-a.ticket --output logs/nodes/node-a/token
py -3.14 scripts/network_cluster.py watchdog --node node-a --state logs/nodes/node-a --token-file logs/nodes/node-a/token
```

Terminal 3:

```powershell
py -3.14 scripts/network_cluster.py issue-join --node node-b --output logs/network/node-b.ticket
py -3.14 scripts/network_cluster.py join --node node-b --ticket-file logs/network/node-b.ticket --output logs/nodes/node-b/token
py -3.14 scripts/network_cluster.py watchdog --node node-b --state logs/nodes/node-b --token-file logs/nodes/node-b/token
```

Open <http://127.0.0.1:8770>. The dashboard asks for the access token in
`logs/network/token`; it keeps the token in tab memory and never writes it to
browser storage. Use the administrator token for dashboard controls, never a
node's credential. Node credentials authorize only that node's worker operations;
they cannot impersonate peers, enroll other nodes, or control the cluster.
Administrator token holders can still act as any node and must remain trusted.

Join tickets are valid for 15 minutes by default (`--ttl` permits 30–3600 seconds),
bound to one configured node, and redeemable only once. Coordinator storage
contains credential/ticket hashes, not the secret strings. Joining again revokes
the node's previous credential; credentials can also be revoked explicitly:

```powershell
py -3.14 scripts/network_cluster.py revoke --node node-b
```

Deliver a join ticket privately to its intended machine; run `join` there and
keep the returned credential file local. Normal nodes need no administrator
token. Agents/watchdogs accept `XEMBRA_NODE_TOKEN` if using environment-based
credentials; the administrator uses `XEMBRA_CLUSTER_TOKEN`. Secret output files
are created exclusively and with owner-only permissions on POSIX. On Windows,
keep them in a folder whose ACL restricts access to the intended account.

The coordinator schedules one job per configured node every 120 seconds, after
previous jobs finish. The node IDs in `--nodes` are a fixed voter list stored in
the coordinator database. Offline assigned nodes leave jobs queued, so this mode
waits for them instead of silently changing membership or shrinking quorum.

```powershell
py -3.14 scripts/network_cluster.py status
py -3.14 scripts/network_cluster.py schedule
py -3.14 scripts/network_cluster.py send --node node-a --recipient node-b --body "hello"
py -3.14 scripts/network_cluster.py kill
py -3.14 scripts/network_cluster.py resume
```

Kill is durable across coordinator restarts. Connected agents stop workloads on
their next poll. Agents disconnected from the coordinator stop their workload
after ten seconds without contact. Node workloads also have a watchdog that
reaps them if the agent dies or stops renewing its local lease. Coordinator jobs
use 20-second renewable leases and a maximum of three lease attempts. Completion
is idempotent for the current lease; results from expired/replaced leases are
rejected. Agents retain undelivered completion results in a durable outbox.

Each `watchdog` supervises one agent, restarts crashes with bounded backoff,
checks its local heartbeat, and stops after five rapid failures. Its heartbeat
and agent PID are in `NODE_STATE/watchdog/status.json`. A local `node.killed`
file overrides resurrection. A cluster-wide kill also latches this local file;
after administrator resume, a local operator must release it explicitly:

```powershell
py -3.14 scripts/network_cluster.py node-kill --state logs/nodes/node-a
py -3.14 scripts/network_cluster.py node-resume --state logs/nodes/node-a
```

Stop foreground processes with Ctrl+C. No network process is automatically
installed as a service or started after reboot. The watchdog itself requires an
OS service or scheduler to survive its own death/reboot. Restart with the same
commands/state directories to resume. The original local guardian does not
supervise these network processes. `agent` remains available for direct execution
without resurrection supervision; it still respects the local kill latch.

## What is shared and persisted

- **Functions:** versioned compression specifications contain a text limit and
  numeric parameters. Each worker reconstructs the function locally. Received
  Python source, closures, pickle payloads, and arbitrary executable code are
  not accepted. Sharing currently supports this compression function family.
- **Worker state:** `evolution_state.json` retains generation, QT vector, alpha
  parameters, compression spec, and last accepted synchronization rounds.
  Worker SQLite data also stays in its node directory across restarts.
- **QT synchronization:** after every configured node returns a successful
  result for a round, the coordinator proposes the arithmetic mean of their QT
  and alpha vectors. The approved value is applied between jobs, never while a
  workload is writing state. This is numerical synchronization of the existing
  experiment, not a quantum-computing operation or proof of model convergence.
- **Consensus:** a strict majority of the configured voter IDs must approve a
  proposal. Each agent validates spec compatibility and finite numeric bounds.
  At most one artifact and one QT value can commit per round. This vote does
  not measure improved intelligence or fitness.
- **Replication:** nodes mirror an ordered, hash-chained event log in their own
  `replica.db`, verify sequence and hashes, and persist accepted artifacts.
  Restart resumes from the durable cursor. Mailbox messages are stored locally
  before acknowledgement; retransmissions are deduplicated by message ID.

The coordinator is a single scheduling and commit authority. This is not Raft,
Byzantine consensus, leader election, or automatic coordinator failover. Event
replicas are not a complete replacement for a coordinator database backup.
Back up `logs/network/coordinator.db` with SQLite's backup API or while stopped.
Nodes fail closed during coordinator loss and reconnect when it returns.

## Remaining work for a redundant WAN deployment

The enrollment and node watchdog code does not change coordinator architecture.
The following are still required before claiming full multi-coordinator service:

- Choose the actual coordinator and worker machines, operating systems, and
  permitted network identities. A three-member quorum backend can tolerate one
  member failure; it should span separate machines for machine-failure tolerance.
- Integrate a tested consensus backend for authoritative jobs, credentials,
  mailboxes, global kill state, and committed logs. The existing SQLite database
  and worker voting are not a substitute for coordinator consensus.
- Test leader loss, network partitions, stale leaders, restart, quorum loss,
  and storage recovery with real replicas. No automatic coordinator failover is
  implemented yet. Current mailbox copies at the coordinator and recipient do
  not provide a redundant multi-coordinator mailbox.
- Configure private network access, certificate identities, and ACLs for the
  selected hosts. Tailscale can provide NAT traversal and relay fallback, but
  installing its executable alone does not authorize or provision remote peers.
- Install the per-node watchdog as an OS-managed service on each selected
  machine, and verify unattended restart/reboot behavior. Foreground watchdogs
  currently restart their agents, not themselves.

Reference architecture: [etcd quorum and failure tolerance](https://etcd.io/docs/v3.6/faq/)
and [Tailscale connection types](https://tailscale.com/docs/reference/connection-types).
No remote systems, firewall rules, Tailscale ACLs, or OS services have been changed.

## Other machines

Remote nodes need a trusted checkout, Python, Git, the dependencies, the cluster
node credential supplied through a local file or `XEMBRA_NODE_TOKEN`, and a reachable
HTTPS coordinator URL. Never put the token in a URL, command argument, or Git.

For a coordinator accessible beyond loopback, TLS is mandatory:

```powershell
py -3.14 scripts/network_cluster.py coordinator --bind 0.0.0.0 --port 8770 --nodes node-a,node-b --cert C:\certs\server.crt --key C:\certs\server.key
```

Use a certificate valid for the coordinator's hostname. Agents verify certificate
and hostname by default; use `--ca C:\certs\cluster-ca.crt` for a private CA.
There is no certificate-verification bypass. Limit inbound access to intended
LAN/VPN peers. No firewall or public network exposure has been configured here.

Example on an already prepared remote Windows machine:

```powershell
py -3.14 scripts/network_cluster.py watchdog --node node-b --url https://coordinator.example:8770 --token-file C:\private\node-b-token --ca C:\certs\cluster-ca.crt --state C:\XembraState\node-b
```

For initial provisioning, run the included bootstrap script **on the target**,
including through an existing authenticated SSH session. It creates a virtual
environment, installs dependencies, and starts the agent in the foreground:

```powershell
.\scripts\bootstrap_node.ps1 -Coordinator https://coordinator.example:8770 -Node node-b -TokenFile C:\private\node-b-token -CaFile C:\certs\cluster-ca.crt
```

Linux equivalent:

```sh
sh scripts/bootstrap_node.sh https://coordinator.example:8770 node-b /private/node-b-token /certs/cluster-ca.crt
```

These scripts default to their own source checkout. If an explicit destination
is missing they can clone the repository, but they do not update an existing
checkout. Use a checkout containing this network feature before invoking them;
unpublished local changes must first be transferred to the target.
The agents then bootstrap missing workload files from that trusted checkout into
their private node directories, preserving runtime state. No cloud instances are
created and no SSH credentials, certificates, or remote targets are assumed.
The bootstrap scripts do not install Python or Git, open firewall rules, or
install a persistent OS service. Linux runtime and remote provisioning still
need verification on actual target machines.

## Verification

```powershell
py -3.14 -m unittest discover -s tests -p test_network_cluster.py -v
py -3.14 -m unittest discover -s tests -p test_network_watchdog.py -v
py -3.14 -m unittest discover -s tests -p test_evolution_state.py -v
py -3.14 -m unittest discover -s tests -p test_cluster_control.py -v
```

Network tests exercise real HTTP connections and two independent agent
processes using a small deterministic workload. They cover majority voting,
conflicting artifacts, stale leases, completion idempotency, kill persistence,
mailbox delivery, restart, hash-chain replication, QT averaging, and rejected
unauthenticated requests. The evolution test runs the real workload twice and
checks resumed state. Live multi-machine HTTPS transport, bootstrap installation,
and visual browser rendering are not verified by these local tests.
