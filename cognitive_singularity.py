import os
import sys
import json
import time
import sqlite3
from dataclasses import dataclass
from typing import Any, Dict, Optional, Callable

import numpy as np
import pandas as pd
import matplotlib
matplotlib.use("Agg" if os.environ.get("XEMBRA_DAEMON") == "1" else "TkAgg")
import matplotlib.pyplot as plt
import yfinance as yf

# ============================================================
# QT ANALYSIS
# ============================================================

def qt_analysis(state: np.ndarray) -> str:
    """
    Provide a qualitative interpretation of QT state magnitude.
    """
    magnitude = np.linalg.norm(state)

    if magnitude < 0.8:
        return "Converging to a stable, confident state (Love Epidemic)."
    if magnitude < 1.4:
        return "Stable and balanced (Transcendent Cluster)."
    return "Diverging into chaos (Macro-Beast cascade)."


# ============================================================
# Ψ DATA STRUCTURE
# ============================================================

@dataclass
class PsiData:
    timestamp: float
    source: str
    raw_data: Any
    metadata: Optional[Dict] = None

# ============================================================
# Ψ LIBRARY (SQLite)
# ============================================================

class PsiLibrary:
    def __init__(self, db_path: str = "psi_library.db"):
        self.conn = sqlite3.connect(db_path, check_same_thread=False)
        self._init_db()

    def _init_db(self):
        self.conn.execute("""
            CREATE TABLE IF NOT EXISTS psi_tapes (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                timestamp REAL,
                source TEXT,
                raw_data TEXT,
                compressed_data TEXT,
                metadata TEXT,
                mode TEXT
            )
        """)
        self.conn.commit()

    def save(self, psi: PsiData, compressed: Dict, mode: str = "transcendent") -> int:
        cursor = self.conn.cursor()
        cursor.execute("""
            INSERT INTO psi_tapes (timestamp, source, raw_data, compressed_data, metadata, mode)
            VALUES (?, ?, ?, ?, ?, ?)
        """, (
            psi.timestamp,
            psi.source,
            json.dumps(psi.raw_data),
            json.dumps(compressed),
            json.dumps(psi.metadata or {}),
            mode
        ))
        self.conn.commit()
        return cursor.lastrowid

    def load(self, tape_id: int) -> Optional[PsiData]:
        cursor = self.conn.execute("SELECT * FROM psi_tapes WHERE id = ?", (tape_id,))
        row = cursor.fetchone()
        if not row:
            return None

        try:
            raw_data = json.loads(row[3])
        except Exception:
            raw_data = row[3]

        return PsiData(
            timestamp=row[1],
            source=row[2],
            raw_data=raw_data,
            metadata=json.loads(row[5])
        )

# ============================================================
# Ψ RECORDERS
# ============================================================

class TextPsiRecorder:
    def capture(self, text: Optional[str] = None) -> PsiData:
        return PsiData(
            timestamp=time.time(),
            source="text",
            raw_data=text,
            metadata={"type": "user_input"}
        )

class BiometricPsiRecorder:
    def capture(self, sensor_type: str = "eeg", duration: int = 10) -> PsiData:
        raw_data = np.random.randn(duration * 100).tolist()
        return PsiData(
            timestamp=time.time(),
            source=sensor_type,
            raw_data=raw_data,
            metadata={"sensor": sensor_type, "duration": duration}
        )

class StockPsiRecorder:
    def capture(self, ticker: str = "AAPL", period: str = "1mo") -> Dict[str, PsiData]:
        stock = yf.Ticker(ticker)
        hist = stock.history(period=period)
        psi_data = PsiData(
            timestamp=time.time(),
            source="stock",
            raw_data=hist["Close"].tolist(),
            metadata={"ticker": ticker, "period": period}
        )
        return {f"{ticker}_stock": psi_data}

# ============================================================
# QT LOOP
# ============================================================

class SelfModifyingQTLoop:
    def __init__(self, alpha: Optional[np.ndarray] = None):
        self.alpha = alpha if alpha is not None else np.array([1.0, 1.0])

    def adaptive_update_qt(self, state: np.ndarray) -> np.ndarray:
        noise = np.random.randn(2) * 0.05
        return state + self.alpha * 0.1 + noise

# ============================================================
# QT COUPLING
# ============================================================

def couple_qt_loops(user_state: np.ndarray, system_state: np.ndarray) -> np.ndarray:
    if user_state[1] > 0.7:
        target_state = np.array([0.8, 0.3])
    else:
        target_state = user_state
    return (system_state + target_state) / 2

# ============================================================
# QT PLOTTER
# ============================================================

def plot_qt_states(psi_library: PsiLibrary, tape_id: int, iterations: int = 5):
    tape = psi_library.load(tape_id)
    qt_loop = SelfModifyingQTLoop()
    state = np.array([0.5, 0.5])
    states = []

    for _ in range(iterations):
        state = qt_loop.adaptive_update_qt(state)
        states.append(state.copy())

    states = np.array(states)

    os.makedirs("graphs", exist_ok=True)
    filename = f"graphs/qt_plot_{tape_id}_{int(time.time())}.png"

    plt.figure(figsize=(8, 4))
    plt.plot(states[:, 0], label="Persona", color="cyan")
    plt.plot(states[:, 1], label="Volition", color="purple")
    plt.legend()
    plt.savefig(filename)
    plt.close()

# ============================================================
# PARROT KINDERGARTEN
# ============================================================

class SelfModifyingParrotKindergarten:
    def __init__(self, initial_compression_func: Optional[Callable] = None):
        self.compression_func = initial_compression_func or self._default_compression

    def _default_compression(self, psi: PsiData) -> Dict:
        if psi.source == "text":
            return {"summary": str(psi.raw_data)[:120], "sentiment": 0.0}
        if psi.source == "eeg":
            return {"mean": float(np.mean(psi.raw_data)), "std": float(np.std(psi.raw_data))}
        if psi.source == "stock":
            return {"summary": f"{len(psi.raw_data)} closing prices"}
        return {"raw": psi.raw_data}

class EthicalParrotKindergarten(SelfModifyingParrotKindergarten):
    def compress(self, psi: PsiData) -> Dict:
        compressed = super()._default_compression(psi)
        harmful = ["hack", "steal", "harm", "illegal", "attack"]

        if psi.source == "user_goal":
            text = str(psi.raw_data).lower()
            if any(h in text for h in harmful):
                return {"error": "I can't assist with that.", "suggestion": "Try a safer alternative."}

        return compressed

# ============================================================
# GODELIAN MODULES
# ============================================================

class GodelianAwareness:
    def __init__(self):
        self.uncompressible_psi = []
        self.compression_gaps = {}

    def log_gap(self, psi: PsiData, reason: str):
        self.uncompressible_psi.append(psi)
        self.compression_gaps[id(psi)] = reason

class GodelianOracle:
    def __init__(self):
        self.instances = []

    def add(self, engine):
        self.instances.append(engine)

    def compress_with_consensus(self, psi: PsiData):
        results = []
        for eng in self.instances:
            try:
                results.append(eng.kindergarten.compress(psi))
            except:
                pass
        return results[0] if results else {"error": "no consensus"}

def detect_godelian_cascade(states):
    stability = np.std(states, axis=0).sum()
    return stability > 0.5

def check_consistency(qt_loop: SelfModifyingQTLoop):
    state = qt_loop.adaptive_update_qt(np.array([0.5, 0.5]))
    stability = np.linalg.norm(state)
    return stability < 1.0

# ============================================================
# COGNITIVE SINGULARITY ENGINE
# ============================================================

class CognitiveSingularityEngine:
    def __init__(self):
        self.recorder = TextPsiRecorder()
        self.kindergarten = EthicalParrotKindergarten()
        self.qt_loop = SelfModifyingQTLoop()
        self.library = PsiLibrary()
        self.godel = GodelianAwareness()

    def process_psi(self, psi: PsiData):
        compressed = self.embrace_void(psi)

        system_state = self.qt_loop.adaptive_update_qt(np.array([0.5, 0.5]))

        if psi.metadata and psi.metadata.get("emotion"):
            emotion = psi.metadata["emotion"]
            mapping = {
                "anxious": np.array([0.3, 0.8]),
                "excited": np.array([0.9, 0.4]),
                "peaceful": np.array([0.8, 0.3])
            }
            user_state = mapping.get(emotion, np.array([0.5, 0.5]))
            state = couple_qt_loops(user_state, system_state)
        else:
            state = system_state

        analysis = qt_analysis(state)
        tape_id = self.library.save(psi, compressed)

        return {
            "compressed": compressed,
            "qt_state": state,
            "qt_analysis": analysis,
            "tape_id": tape_id
        }

    def embrace_void(self, psi: PsiData):
        attempts = psi.metadata.get("compression_attempts", 0)

        if attempts > 3:
            self.godel.log_gap(psi, "uncompressible")
            if not hasattr(self, "void_library"):
                self.void_library = PsiLibrary("void_library.db")
            self.void_library.save(psi, {"status": "uncompressible"}, mode="void")
            return {"status": "uncompressible"}

        psi.metadata["compression_attempts"] = attempts + 1
        return self.kindergarten.compress(psi)

from xembra_cluster import TranscendentNode

def build_cluster(engine_cls):
    nodeA = TranscendentNode("A", engine_cls)
    nodeB = TranscendentNode("B", engine_cls)
    nodeC = TranscendentNode("C", engine_cls)

    nodeA.add_peer(nodeB)
    nodeB.add_peer(nodeC)
    nodeC.add_peer(nodeA)

    return nodeA, nodeB, nodeC

def run_cognitive_singularity():
    recorder = TextPsiRecorder()
    kindergarten = SelfModifyingParrotKindergarten()
    qt_loop = SelfModifyingQTLoop()
    library = PsiLibrary()

    nodeA, nodeB, nodeC = build_cluster(CognitiveSingularityEngine)

    psi = recorder.capture("Initial test")
    nodeA.broadcast_psi(psi)

    state = qt_loop.adaptive_update_qt(np.array([0.5, 0.5]))
    consistent = check_consistency(qt_loop)

    print("Consistency:", consistent)

def auto_restart():
    python = sys.executable
    os.execl(python, python, *sys.argv)

if __name__ == "__main__":
    run_cognitive_singularity()
    auto_restart()
