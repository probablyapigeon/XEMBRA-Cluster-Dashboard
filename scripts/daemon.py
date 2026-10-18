"""Windows cluster supervisor: python scripts/daemon.py start|status|stop|run."""
import argparse
import json
import logging
from logging.handlers import RotatingFileHandler
import msvcrt
import os
from pathlib import Path
import subprocess
import sys
import threading
import time

ROOT = Path(__file__).resolve().parents[1]
STATE = ROOT / "logs" / "daemon"
STATE.mkdir(parents=True, exist_ok=True)
STOP = STATE / "stop"
STATUS = STATE / "status.json"


def acquire():
    handle = (STATE / "lock").open("a+b")
    if os.fstat(handle.fileno()).st_size == 0:
        handle.write(b"0")
        handle.flush()
    handle.seek(0)
    try:
        msvcrt.locking(handle.fileno(), msvcrt.LK_NBLCK, 1)
    except OSError:
        handle.close()
        return None
    return handle


def running():
    handle = acquire()
    if handle is None:
        return True
    handle.close()
    return False


def preflight():
    result = subprocess.run([sys.executable, "-c",
        "import importlib.util; names=['numpy','pandas','matplotlib','yfinance']; "
        "missing=[n for n in names if importlib.util.find_spec(n) is None]; "
        "assert not missing, 'Missing dependencies: '+', '.join(missing)"],
        cwd=ROOT, capture_output=True, text=True, timeout=15)
    if result.returncode:
        raise SystemExit("Cluster dependencies are unavailable in " + sys.executable
                         + "\n" + result.stderr)


def supervise(interval, timeout):
    lock = acquire()
    if lock is None:
        raise SystemExit("XEMBRA supervisor is already running.")
    logger = logging.getLogger("xembra-daemon")
    logger.setLevel(logging.INFO)
    handler = RotatingFileHandler(STATE / "daemon.log", maxBytes=2_000_000,
                                 backupCount=5, encoding="utf-8")
    handler.setFormatter(logging.Formatter("%(asctime)s %(levelname)s %(message)s"))
    logger.addHandler(handler)
    child = None
    generation = 0
    failures = 0

    def heartbeat(phase):
        temp = STATUS.with_suffix(".tmp")
        temp.write_text(json.dumps(dict(pid=os.getpid(), child_pid=child.pid if child else None,
            heartbeat=time.time(), phase=phase, cycle=generation, failures=failures)), encoding="utf-8")
        temp.replace(STATUS)

    try:
        STOP.unlink(missing_ok=True)
        logger.info("XEMBRA supervisor started")
        while not STOP.exists():
            generation += 1
            env = dict(os.environ, XEMBRA_DAEMON="1", PYTHONIOENCODING="utf-8")
            child = subprocess.Popen([sys.executable, "-u", str(ROOT / "cluster_test.py")],
                cwd=ROOT, env=env, stdin=subprocess.DEVNULL, stdout=subprocess.PIPE,
                stderr=subprocess.STDOUT, text=True, encoding="utf-8", errors="replace",
                creationflags=subprocess.CREATE_NO_WINDOW)
            def capture(process):
                for line in process.stdout:
                    logger.info("cluster: %s", line.rstrip())
            reader = threading.Thread(target=capture, args=(child,), daemon=True)
            reader.start()
            started = time.monotonic()
            while child.poll() is None and not STOP.exists():
                heartbeat("running")
                if time.monotonic() - started > timeout:
                    logger.error("Cycle exceeded timeout (%ss)", timeout)
                    child.kill()
                    break
                time.sleep(1)
            if child.poll() is None:
                child.terminate()
            child.wait(timeout=10)
            reader.join(timeout=5)
            code = child.returncode
            child = None
            if STOP.exists():
                break
            failures = failures + 1 if code else 0
            logger.info("Cycle %s exited with %s", generation, code)
            if failures >= 10:
                logger.error("Stopping after 10 consecutive failures; inspect daemon.log")
                break
            delay = min(60, 2 ** min(failures, 6)) if failures else interval
            until = time.monotonic() + delay
            while time.monotonic() < until and not STOP.exists():
                heartbeat("cooldown")
                time.sleep(1)
    finally:
        if child and child.poll() is None:
            child.kill()
            child.wait()
        heartbeat("stopped")
        logger.info("XEMBRA supervisor stopped")
        lock.close()


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("command", choices=["start", "run", "status", "stop"])
    parser.add_argument("--interval", type=int, default=30)
    parser.add_argument("--timeout", type=int, default=300)
    args = parser.parse_args()
    if args.interval < 1 or args.timeout < 1:
        parser.error("interval and timeout must be positive")
    if args.command == "status":
        print("Supervisor running:" , running())
        print(STATUS.read_text() if STATUS.exists() else "No recorded run.")
    elif args.command == "stop":
        if not running():
            print("Supervisor is already stopped.")
            return
        STOP.touch()
        for _ in range(15):
            if not running():
                print("Supervisor stopped.")
                return
            time.sleep(1)
        raise SystemExit("Stop requested; shutdown has not completed. Check status.")
    else:
        if running():
            raise SystemExit("Supervisor is already running.")
        preflight()
        if args.command == "run":
            supervise(args.interval, args.timeout)
        else:
            with (STATE / "launcher.log").open("a", encoding="utf-8") as output:
                process = subprocess.Popen([sys.executable, str(Path(__file__).resolve()),
                    "run", "--interval", str(args.interval), "--timeout", str(args.timeout)],
                    cwd=ROOT, stdin=subprocess.DEVNULL, stdout=output, stderr=output,
                    creationflags=subprocess.DETACHED_PROCESS | subprocess.CREATE_NEW_PROCESS_GROUP)
            for _ in range(20):
                if running():
                    print("Supervisor started; PID", process.pid)
                    return
                if process.poll() is not None:
                    break
                time.sleep(.25)
            raise SystemExit("Startup not confirmed; check logs/daemon/launcher.log")


if __name__ == "__main__":
    main()
