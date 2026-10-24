"""Conservative process liveness checks: uncertainty never permits reclamation."""
import os


def process_alive(pid):
    if not isinstance(pid,int) or isinstance(pid,bool) or pid<=0:
        return None
    if os.name!='nt':
        try:
            os.kill(pid,0);return True
        except ProcessLookupError:
            return False
        except PermissionError:
            return None
    import ctypes
    from ctypes import wintypes
    kernel=ctypes.WinDLL('kernel32',use_last_error=True)
    kernel.OpenProcess.argtypes=[wintypes.DWORD,wintypes.BOOL,wintypes.DWORD]
    kernel.OpenProcess.restype=wintypes.HANDLE
    kernel.CloseHandle.argtypes=[wintypes.HANDLE]
    kernel.GetExitCodeProcess.argtypes=[wintypes.HANDLE,ctypes.POINTER(wintypes.DWORD)]
    handle=kernel.OpenProcess(0x1000,False,pid)
    if not handle:
        return False if ctypes.get_last_error()==87 else None
    try:
        code=wintypes.DWORD()
        if not kernel.GetExitCodeProcess(handle,ctypes.byref(code)):
            return None
        return code.value==259
    finally:
        kernel.CloseHandle(handle)
