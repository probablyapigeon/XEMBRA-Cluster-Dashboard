"""Stop a network workload if its agent dies or stops renewing its local lease."""
import ctypes
import os
from pathlib import Path
import subprocess
import sys
import time


def main():
    folder=Path(sys.argv[1]);parent=int(sys.argv[2]);handle=None
    if os.name=='nt':
        kernel=ctypes.WinDLL('kernel32',use_last_error=True)
        kernel.OpenProcess.restype=ctypes.c_void_p
        kernel.OpenProcess.argtypes=[ctypes.c_uint32,ctypes.c_int,ctypes.c_uint32]
        kernel.WaitForSingleObject.argtypes=[ctypes.c_void_p,ctypes.c_uint32]
        kernel.CloseHandle.argtypes=[ctypes.c_void_p]
        handle=kernel.OpenProcess(0x00100000,0,parent)
        if not handle:return 1
        def alive():return kernel.WaitForSingleObject(handle,0)==258
    else:
        def alive():return os.getppid()==parent
    child=subprocess.Popen([sys.executable,'-u',str(folder/'cluster_test.py')],cwd=folder,
        creationflags=subprocess.CREATE_NO_WINDOW if os.name=='nt' else 0)
    try:
        while child.poll() is None:
            lease=folder/'contact.lease'
            if not alive() or not lease.exists() or time.time()-lease.stat().st_mtime>10:
                child.terminate();child.wait(timeout=5);return 1
            time.sleep(.25)
        return child.returncode
    finally:
        if child.poll() is None:child.kill();child.wait()
        if handle:kernel.CloseHandle(handle)


if __name__=='__main__':sys.exit(main())
