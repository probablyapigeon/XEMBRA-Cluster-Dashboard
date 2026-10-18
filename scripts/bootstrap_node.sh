#!/usr/bin/env sh
# Usage: bootstrap_node.sh HTTPS_COORDINATOR NODE TOKEN_FILE [CA_FILE]
set -eu
coordinator=${1:?coordinator URL required}
node=${2:?node ID required}
token_file=${3:?token file required}
ca_file=${4:-}
bootstrap_source=$(CDPATH= cd -- "$(dirname -- "$0")/.." && pwd)
destination=${XEMBRA_NODE_DESTINATION:-"$bootstrap_source"}
if [ ! -f "$destination/scripts/network_cluster.py" ]; then
    if [ -e "$destination" ]; then echo 'Destination exists without Git metadata.' >&2; exit 1; fi
    git clone https://github.com/probablyapigeon/XEMBRA-Cluster-Dashboard.git "$destination"
fi
if [ ! -f "$destination/scripts/network_cluster.py" ]; then echo 'Transfer the updated network source before provisioning.' >&2; exit 1; fi
if [ ! -x "$destination/.venv/bin/python" ]; then python3 -m venv "$destination/.venv"; fi
"$destination/.venv/bin/python" -m pip install -r "$destination/requirements.txt"
set -- "$destination/.venv/bin/python" "$destination/scripts/network_cluster.py" watchdog --node "$node" --url "$coordinator" --token-file "$token_file" --state "$destination/logs/nodes/$node"
if [ -n "$ca_file" ]; then set -- "$@" --ca "$ca_file"; fi
exec "$@"
