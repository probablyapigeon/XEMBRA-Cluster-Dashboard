# Run XEMBRA in Docker

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
