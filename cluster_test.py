import os
import sys
import json
import time
import sqlite3
from dataclasses import dataclass
from typing import Any, Dict, Optional, Callable
from cognitive_singularity import plot_qt_states

import numpy as np
import pandas as pd
import matplotlib
matplotlib.use("Agg" if os.environ.get("XEMBRA_DAEMON") == "1" else "TkAgg")
import matplotlib.pyplot as plt
import yfinance as yf
import inspect

from shared_evolution import compression as portable_compression, validate as validate_compression, write_json


def load_evolution_state():
    if os.environ.get("XEMBRA_DAEMON") == "1" and os.path.exists("evolution_state.json"):
        with open("evolution_state.json", encoding="utf-8") as stream:
            return json.load(stream)
    return {"generation": 0, "alpha": [1.0, 1.0], "qt": [0.5, 0.5]}

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
        self.conn = sqlite3.connect(db_path)
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
        cursor.execute(
            """
            INSERT INTO psi_tapes (timestamp, source, raw_data, compressed_data, metadata, mode)
            VALUES (?, ?, ?, ?, ?, ?)
            """,
            (
                psi.timestamp,
                psi.source,
                json.dumps(psi.raw_data),
                json.dumps(compressed),
                json.dumps(psi.metadata or {}),
                mode
            )
        )
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
        if text is None:
            raise ValueError("TextPsiRecorder.capture() requires a text argument.")
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
            metadata={"sensor": sensor_type, "duration": duration, "sample_rate": 100}
        )

class StockPsiRecorder:
    def capture(self, ticker: str = "AAPL", period: str = "1mo") -> Dict[str, PsiData]:
        stock = yf.Ticker(ticker)
        hist = stock.history(period=period)
        if hist.empty:
            raise ValueError(f"No stock data available for {ticker} with period {period}.")
        psi_data = PsiData(
            timestamp=time.time(),
            source="stock",
            raw_data=hist["Close"].tolist(),
            metadata={"ticker": ticker, "period": period, "points": len(hist)}
        )
        return {f"{ticker}_stock": psi_data}

# ============================================================
# QT LOOP
# ============================================================

class SelfModifyingQTLoop:
    def __init__(self, alpha=None):
        self.alpha = alpha if alpha is not None else np.array([1.0, 1.0])

    def adaptive_update_qt(self, state: np.ndarray) -> np.ndarray:
        noise = np.random.randn(2) * 0.05
        return state + self.alpha * 0.1 + noise

# ============================================================
# PARROT KINDERGARTEN
# ============================================================

class SelfModifyingParrotKindergarten:
    def __init__(self, initial_compression_func=None):
        self.compression_func = initial_compression_func or self._default_compression

    def _default_compression(self, psi: PsiData) -> Dict:
        if psi.source == "text":
            return {"summary": str(psi.raw_data)[:100], "sentiment": 0.0}
        if psi.source == "eeg":
            return {"mean": float(np.mean(psi.raw_data)), "std": float(np.std(psi.raw_data))}
        if psi.source == "stock":
            return {
                "summary": f"{len(psi.raw_data)} closing prices",
                "min": float(np.min(psi.raw_data)),
                "max": float(np.max(psi.raw_data))
            }
        return {"raw": psi.raw_data}

# ============================================================
# QT ANALYSIS
# ============================================================

def qt_analysis(state: np.ndarray) -> str:
    magnitude = np.linalg.norm(state)
    if magnitude < 0.8:
        return "Converging to a stable, confident state (Love Epidemic)."
    if magnitude < 1.4:
        return "Stable and balanced (Transcendent Cluster)."
    return "Diverging into chaos (Macro-Beast cascade)."

# ============================================================
# SELF-MODIFYING CODE ENGINE
# ============================================================

import importlib
import xembra_generated

def rewrite_self(func_name: str, func_source: str):
    filename = "xembra_generated.py"
    with open(filename, "a", encoding="utf-8") as f:
        f.write(f"\n\n# --- AUTO-GENERATED FUNCTION: {func_name} ---\n")
        f.write(func_source)
        f.write(f"\n# --- END AUTO-GENERATED FUNCTION: {func_name} ---\n")
    importlib.reload(xembra_generated)

def generate_dynamic_compression_source(mean_val: float, std_val: float) -> str:
    func_name = f"compression_v{time.time_ns()}"
    return f"""
def {func_name}(psi):
    if psi.source == "text":
        return {{"summary": str(psi.raw_data)[:120], "sentiment": {mean_val}}}
    elif psi.source == "eeg":
        return {{"mean": {mean_val}, "std": {std_val}}}
    else:
        return {{"raw": psi.raw_data}}

register("{func_name}", {func_name})
"""

# ============================================================
# COGNITIVE SINGULARITY ENGINE (Cluster‑Integrated)
# ============================================================

from xembra_cluster import TranscendentNode

class CognitiveSingularityEngine:
    def __init__(self):
        self.recorder = TextPsiRecorder()
        self.kindergarten = SelfModifyingParrotKindergarten()
        self.qt_loop = SelfModifyingQTLoop()
        previous = load_evolution_state()
        self.qt_loop.alpha = np.array(previous["alpha"])
        if "artifact" in previous:
            self.kindergarten.compression_func = portable_compression(previous["artifact"])
        self.library = PsiLibrary()
        self.parrot_mods = 0
        self.qt_mods = 0

    def process_psi(self, psi: PsiData):
        compressed = self.kindergarten.compression_func(psi)
        state = self.qt_loop.adaptive_update_qt(np.array([0.5, 0.5]))
        analysis = qt_analysis(state)
        tape_id = self.library.save(psi, compressed)
        return {
            "compressed": compressed,
            "qt_state": state,
            "qt_analysis": analysis,
            "tape_id": tape_id
        }

# ============================================================
# BUILD CLUSTER
# ============================================================

def build_cluster(engine_cls):
    nodeA = TranscendentNode("A", engine_cls)
    nodeB = TranscendentNode("B", engine_cls)
    nodeC = TranscendentNode("C", engine_cls)
    nodeA.add_peer(nodeB)
    nodeB.add_peer(nodeC)
    nodeC.add_peer(nodeA)
    return nodeA, nodeB, nodeC

# ============================================================
# MAIN SINGULARITY LOOP (Cluster‑Integrated)
# ============================================================

def run_cognitive_singularity():
    print("🚀 Starting Cognitive Singularity Engine...\n")

    recorder = TextPsiRecorder()
    kindergarten = SelfModifyingParrotKindergarten()
    qt_loop = SelfModifyingQTLoop()
    previous = load_evolution_state()
    qt_loop.alpha = np.array(previous["alpha"])
    state = np.array(previous["qt"])
    artifact = previous.get("artifact", {"version": 1, "text_limit": 100, "sentiment": 0.0, "eeg_mean": 0.0, "eeg_std": 0.0})
    kindergarten.compression_func = portable_compression(artifact)
    library = PsiLibrary()

    # Build cluster using the SAME evolving engine
    nodeA, nodeB, nodeC = build_cluster(CognitiveSingularityEngine)

    parrot_mods = 0
    qt_mods = 0

    for cycle in range(1, 3 + 1):
        print(f"--- Self-Evolution Cycle {cycle} ---")

        if cycle == 1:
            psi = recorder.capture("This is a test of the new compression function...")
        else:
            vec = np.round(np.random.randn(2), 3)
            psi = recorder.capture(f"Generated thought at {time.time():.2f}: {vec.tolist()}")

        print(f"Captured Ψ: {psi.raw_data}")

        # Broadcast Ψ into the cluster
        nodeA.broadcast_psi(psi)

        # Cluster distributed evolution
        nodeA.distributed_evolution([psi])
        nodeB.distributed_evolution([psi])
        nodeC.distributed_evolution([psi])

        # Compress Ψ
        compressed = kindergarten.compression_func(psi)
        print(f"Compressed Ψ: {compressed}")

        # QT update
        state = qt_loop.adaptive_update_qt(state)
        print(f"QT Analysis: {qt_analysis(state)}\n")

        # Modify compression engine
        if cycle == 2:
            artifact = {"version": 1, "text_limit": 120, "sentiment": float(state[0]),
                        "eeg_mean": float(state[0]), "eeg_std": abs(float(state[1]))}
            validate_compression(artifact)
            kindergarten.compression_func = portable_compression(artifact)
            parrot_mods += 1

        # Modify QT loop
        if cycle == 3:
            qt_loop.alpha += np.array([0.1, 0.1])
            qt_mods += 1

        tape_id = library.save(psi, compressed)

    print("Self-evolution complete.")
    print(f"Parrot modifications: {parrot_mods}")
    print(f"QT Loop modifications: {qt_mods}\n")

    test_output = kindergarten.compression_func(
        PsiData(time.time(), "text", "This is a test of the new compression function...")
    )
    print(f"Test output: {test_output}")

    print("\n📊 Visualizing QT loop trajectory...")
    if os.environ.get("XEMBRA_DAEMON") != "1":
        plot_qt_states(library, tape_id)
    else:
        previous.update({"generation": previous.get("generation", 0) + 1,
                         "alpha": qt_loop.alpha.tolist(), "qt": state.tolist(), "artifact": artifact})
        write_json("evolution_state.json", previous)

def auto_restart():
    print("\n🔁 Auto‑Restart Evolution Loop engaged...")
    time.sleep(0.2)
    python = sys.executable
    os.execl(python, python, *sys.argv)

# ============================================================
# MAIN
# ============================================================

if __name__ == "__main__":
    run_cognitive_singularity()
    if os.environ.get("XEMBRA_DAEMON") != "1":
        auto_restart()
