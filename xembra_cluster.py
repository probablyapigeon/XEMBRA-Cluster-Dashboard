import time
import logging
import numpy as np
from typing import TYPE_CHECKING

from evolution_engine import EvolutionEngine   # ← FIXED IMPORT

if TYPE_CHECKING:
    from cognitive_singularity import PsiData


class TranscendentNode:
    """
    A node in the Transcendent Cluster.
    Each node contains its own CognitiveSingularityEngine,
    its own evolutionary engine, and communicates Ψ with peers.
    """

    def __init__(self, node_id, engine_cls):
        self.node_id = node_id
        self.engine = engine_cls()

        # THIS WAS THE BUG — FIXED
        self.evolution = EvolutionEngine(self.engine)

        self.peers = []
        self.received_log = []



    # ---------------------------------------------------------
    # NETWORK FORMATION
    # ---------------------------------------------------------

    def add_peer(self, peer_node):
        if peer_node not in self.peers:
            self.peers.append(peer_node)
            peer_node.peers.append(self)

    # ---------------------------------------------------------
    # Ψ BROADCASTING
    # ---------------------------------------------------------

    def broadcast_psi(self, psi):
        logging.info(f"Node {self.node_id} broadcasting Ψ: {psi.raw_data}")
        for peer in self.peers:
            peer.receive_psi(psi, sender=self.node_id)

    # ---------------------------------------------------------
    # SAFE RECEIVE (NO INFINITE LOOP)
    # ---------------------------------------------------------

    def receive_psi(self, psi, sender):
        from cognitive_singularity import PsiData   # LOCAL IMPORT

        logging.info(f"Node {self.node_id} received Ψ from {sender}: {psi.raw_data}")
        self.received_log.append((sender, psi))

        # Process Ψ
        result = self.engine.process_psi(psi)

        logging.info(
            f"Node {self.node_id} processed Ψ: {psi.raw_data} | "
            f"Compressed: {result['compressed']} | "
            f"QT State: {result['qt_state']}"
        )

        # Prevent infinite rebroadcast loops
        hops = psi.metadata.get("hops", 0)

        if hops < 1:
            compressed_psi = PsiData(
                timestamp=time.time(),
                source=f"compressed_by_{self.node_id}",
                raw_data=result["compressed"],
                metadata={
                    "original_sender": psi.metadata.get("original_sender", self.node_id),
                    "hops": hops + 1
                }
            )

            for peer in self.peers:
                peer.receive_psi(compressed_psi, sender=self.node_id)

    # ---------------------------------------------------------
    # CONSENSUS COMPRESSION
    # ---------------------------------------------------------

    def consensus_compress(self, psi):
        results = []
        for peer in [self] + self.peers:
            try:
                results.append(peer.engine.kindergarten.compress(psi))
            except Exception:
                pass

        if not results:
            return {"error": "no consensus"}

        return results[0]

    # ---------------------------------------------------------
    # QT SYNCHRONIZATION
    # ---------------------------------------------------------

    def synchronize_qt(self, psi):
        trajectories = []
        for peer in [self] + self.peers:
            qt_state = peer.engine.qt_loop.adaptive_update_qt(np.array([0.5, 0.5]))
            trajectories.append(qt_state)

        avg_state = np.mean(trajectories, axis=0)

        return {
            "avg_state": avg_state,
            "trajectories": trajectories
        }

    # ---------------------------------------------------------
    # DISTRIBUTED EVOLUTION
    # ---------------------------------------------------------

    def distributed_evolution(self, psi_list, population_size=10):
        self.evolution.evolve_with_selection(psi_list, population_size)

        elite_funcs = list(self.engine.generated_functions.values())[:3]

        for peer in self.peers:
            for func in elite_funcs:
                peer.engine.kindergarten.compression_func = func

    # ---------------------------------------------------------
    # NETWORK STATUS
    # ---------------------------------------------------------

    def status(self):
        return {
            "node_id": self.node_id,
            "peers": [p.node_id for p in self.peers],
            "received_psi_count": len(self.received_log),
            "generation": self.evolution.tracker.generation
        }

def visualize_cluster(nodes):
    print("\n🌐 Cluster Topology:")
    for node in nodes:
        peers = ", ".join([p.node_id for p in node.peers])
        print(f"Node {node.node_id} → {peers}")
    print()

def cluster_health(nodes):
    from cognitive_singularity import detect_godelian_cascade, check_consistency

    print("\n🩺 Cluster Health Report:")
    for node in nodes:
        qt_state = node.engine.qt_loop.adaptive_update_qt(np.array([0.5, 0.5]))
        cascade = detect_godelian_cascade([qt_state])
        consistent = check_consistency(node.engine.qt_loop)

        print(f"Node {node.node_id}:")
        print(f"  QT State: {qt_state}")
        print(f"  Cascade Risk: {cascade}")
        print(f"  Consistent: {consistent}")
    print()
