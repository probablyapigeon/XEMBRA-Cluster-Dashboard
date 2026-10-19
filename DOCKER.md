# Run XEMBRA in Docker

Local builds default to the measured neural codec-selection workload. See
[LEARNING.md](LEARNING.md). A previously published GHCR image will not include
new local changes until a new publishing workflow has completed.

## Published GHCR image

The publishing workflow tests the Linux image before uploading `latest` and an
immutable `sha-COMMIT` tag to GitHub Container Registry:

```powershell
docker pull ghcr.io/probablyapigeon/xembra-cluster-dashboard:latest
docker compose -f compose.pull.yaml up -d
```

Open <http://127.0.0.1:8766>. This Compose file uses the same persistent volume
and project name as the local build; use one Compose file at a time. To stop:

```powershell
docker compose -f compose.pull.yaml stop
```

The first published package may be private even though this repository is
public. For anonymous pulls, the package owner must change its visibility to
Public in GitHub Packages settings. Publication does not change the XEMBRA
license. The image currently targets Linux amd64.

## Build locally

From this repository folder with Docker Desktop's Linux engine running:

```powershell
docker compose up -d --build
```

Open <http://127.0.0.1:8766>. Port 8766 keeps the existing Windows dashboard
at 8765 available during migration. The container runs a separate cluster with
fresh state; existing Windows history is not automatically imported.

```powershell
docker compose ps
docker compose logs --tail 50 cluster
docker compose exec cluster python scripts/cluster_control.py status
docker compose exec cluster python scripts/cluster_control.py kill
docker compose exec cluster python scripts/cluster_control.py resume
docker compose stop
docker compose start
```

The container runs as an unprivileged user. The dashboard is published only on
host loopback, and the container has CPU, memory, PID, and Docker-log limits.
Docker restarts it after unexpected exit while the Docker engine is available.
Health checks report dashboard readiness; unhealthy status by itself does not
cause Docker to restart a running container.

State, SQLite history, rotating worker logs, generated functions, and persistent
QT/compression state live in the named volume `xembra-dashboard_cluster-data`.
`docker compose down` removes containers but retains the volume. Avoid
`docker compose down -v` unless you intend to delete that saved state.
Image rebuilds exclude host logs, databases, tokens, virtual environments, and
Git metadata. Keep Docker Desktop running to keep the cluster running.

Normal Docker stop/restart preserves the prior schedule and kill-switch setting.
A kill issued through the dashboard or CLI stays latched across container
restarts; explicitly resume it to allow work again. The CLI `stop` command also
latches the kill switch, so prefer Docker's stop/start commands for normal
container maintenance.

To use another host port, set it before launching:

```powershell
$env:XEMBRA_HOST_PORT = '8767'
docker compose up -d
```

To run the integration tests inside the image:

```powershell
docker compose run --rm --no-deps cluster python -m unittest discover -s tests -p test_cluster_control.py -v
docker compose run --rm --no-deps cluster python -m unittest discover -s tests -p test_evolution_state.py -v
```

These tests use temporary state directories. The first checks controller
recovery, isolation, messaging, request protection, and kill behavior; the second
runs the real evolution workload twice to verify persistence.

This image packages the local dashboard and worker supervisor. It does not
implement the pending multi-coordinator consensus/failover or configure remote
networking. Network scripts remain in the source checkout for separate use.

## Run your XC program in the local container

```powershell
docker compose -f compose.yaml -f compose.xc.yaml up -d --build
```

Open http://127.0.0.1:8766 and find **XC program & persistent memory**.
The override selects `XEMBRA_WORKLOAD=xc`. Use both compose files when recreating
the XC container; ordinary `docker compose up` otherwise selects learning.
Docker Desktop restarts preserve the current container configuration.

Each scheduled worker executes eight scripted events from `xc/mara_memory.xc`,
then saves its own `xc_checkpoint.json` and `xc_metrics.json` under
`/data/worker-N/`. The next run resumes state, action, event counter and memory.
The existing named volume preserves these files across container replacement.
Source/hash mismatches fail instead of silently erasing an old checkpoint.
Keep a backup and choose a new state volume before changing the program's
source if its existing checkpoints must remain available.

The XC runtime is the locally tested 0.3.0 foundation, copied unchanged as
`xc_runtime.py`; provenance is recorded in `xc/PROVENANCE.json`. Its default
Mara program retains up to 64 memories. The dashboard also compares a hypothetical
next PlayerTalk response with only the last 16 memories; this comparison does
not change the saved full checkpoint. Events and policy are handwritten demo
rules, not neural training, real Minecraft observations or shared machine memory.

Switch back to the compression selector:

```powershell
$env:XEMBRA_WORKLOAD = 'learning'
docker compose up -d
```

The publishing workflow includes the XC runtime and runs its persistence tests.
Kill/pause controls still apply to XC.

## Share XC without cloning the repository

With Docker running, pull and start a private local XC cluster:

```powershell
docker pull ghcr.io/probablyapigeon/xembra-cluster-dashboard:latest
docker run -d --name xembra-xc --init --restart unless-stopped -p 127.0.0.1:8766:8765 -e XEMBRA_WORKLOAD=xc -v xembra-xc-data:/data --memory 2g --cpus 2 --pids-limit 256 --security-opt no-new-privileges --cap-drop ALL ghcr.io/probablyapigeon/xembra-cluster-dashboard:latest
```

Open http://127.0.0.1:8766. The named volume keeps checkpoints after container
replacement. This starts an independent cluster; it does not join another
person's cluster or exchange their memories. For later starts of this same
container use `docker start xembra-xc` instead of repeating `docker run`.
