"""Spawn one parser process per document, with an enforced memory ceiling."""
from __future__ import annotations

import ctypes
from datetime import datetime, timezone
import json
import multiprocessing
import os
from pathlib import Path
import queue
import time


def _memory_limit(megabytes):
    if os.name != "nt":
        import resource
        ceiling = megabytes * 1024 * 1024
        resource.setrlimit(resource.RLIMIT_AS, (ceiling, ceiling))
        return None
    from ctypes import wintypes
    size = ctypes.c_size_t

    class Basic(ctypes.Structure):
        _fields_ = [("ProcessTime", ctypes.c_longlong), ("JobTime", ctypes.c_longlong),
                    ("Flags", wintypes.DWORD), ("MinWorkingSet", size),
                    ("MaxWorkingSet", size), ("ActiveProcesses", wintypes.DWORD),
                    ("Affinity", size), ("PriorityClass", wintypes.DWORD),
                    ("SchedulingClass", wintypes.DWORD)]

    class Counters(ctypes.Structure):
        _fields_ = [(name, ctypes.c_ulonglong) for name in
                    ("ReadOps", "WriteOps", "OtherOps", "ReadBytes", "WriteBytes", "OtherBytes")]

    class Extended(ctypes.Structure):
        _fields_ = [("Basic", Basic), ("Io", Counters), ("ProcessMemory", size),
                    ("JobMemory", size), ("PeakProcessMemory", size), ("PeakJobMemory", size)]

    kernel = ctypes.WinDLL("kernel32", use_last_error=True)
    kernel.CreateJobObjectW.argtypes = [ctypes.c_void_p, wintypes.LPCWSTR]
    kernel.CreateJobObjectW.restype = wintypes.HANDLE
    kernel.SetInformationJobObject.argtypes = [wintypes.HANDLE, ctypes.c_int,
                                               ctypes.c_void_p, wintypes.DWORD]
    kernel.SetInformationJobObject.restype = wintypes.BOOL
    kernel.AssignProcessToJobObject.argtypes = [wintypes.HANDLE, wintypes.HANDLE]
    kernel.AssignProcessToJobObject.restype = wintypes.BOOL
    kernel.GetCurrentProcess.restype = wintypes.HANDLE
    kernel.CloseHandle.argtypes = [wintypes.HANDLE]
    handle = kernel.CreateJobObjectW(None, None)
    if not handle:
        raise ctypes.WinError(ctypes.get_last_error())
    limits = Extended()
    limits.Basic.Flags = 0x100  # JOB_OBJECT_LIMIT_PROCESS_MEMORY
    limits.ProcessMemory = megabytes * 1024 * 1024
    if not kernel.SetInformationJobObject(handle, 9, ctypes.byref(limits), ctypes.sizeof(limits)):
        error = ctypes.get_last_error()
        kernel.CloseHandle(handle)
        raise ctypes.WinError(error)
    if not kernel.AssignProcessToJobObject(handle, kernel.GetCurrentProcess()):
        error = ctypes.get_last_error()
        kernel.CloseHandle(handle)
        raise ctypes.WinError(error)
    return (kernel, handle)


def _peak_memory_bytes():
    if os.name != "nt":
        import resource
        import sys
        peak = resource.getrusage(resource.RUSAGE_SELF).ru_maxrss
        return peak if sys.platform == "darwin" else peak * 1024
    from ctypes import wintypes

    class Memory(ctypes.Structure):
        _fields_ = [("cb", wintypes.DWORD), ("PageFaultCount", wintypes.DWORD)] + [
            (name, ctypes.c_size_t) for name in ("PeakWorkingSetSize", "WorkingSetSize",
            "QuotaPeakPagedPoolUsage", "QuotaPagedPoolUsage", "QuotaPeakNonPagedPoolUsage",
            "QuotaNonPagedPoolUsage", "PagefileUsage", "PeakPagefileUsage")]

    counters = Memory()
    counters.cb = ctypes.sizeof(counters)
    kernel = ctypes.WinDLL("kernel32", use_last_error=True)
    kernel.GetCurrentProcess.restype = wintypes.HANDLE
    psapi = ctypes.WinDLL("psapi", use_last_error=True)
    psapi.GetProcessMemoryInfo.argtypes = [wintypes.HANDLE, ctypes.c_void_p, wintypes.DWORD]
    if not psapi.GetProcessMemoryInfo(kernel.GetCurrentProcess(), ctypes.byref(counters), counters.cb):
        return None
    return counters.PeakWorkingSetSize


def _worker(payload, results, memory_mb):
    start = time.monotonic()
    handle = None
    try:
        try:
            handle = _memory_limit(memory_mb)
        except Exception as exc:
            results.put({"status": "failed", "error": "memory_limit_unavailable",
                         "detail": str(exc)})
            return
        from .models import ManifestEntry, ParseConfig
        from .pipeline import parse_document
        result = parse_document(ManifestEntry.from_dict(payload["entry"]),
                                config=ParseConfig(**payload["config"]),
                                workspace_root=payload["workspace_root"],
                                output=payload["output"], force=payload.get("force", False))
        results.put({"status": result["status"], "parse_result": result,
                     "elapsed_seconds": round(time.monotonic() - start, 3),
                     "peak_working_set_bytes": _peak_memory_bytes()})
    except Exception as exc:
        results.put({"status": "failed", "error": type(exc).__name__, "detail": str(exc),
                     "elapsed_seconds": round(time.monotonic() - start, 3)})
    finally:
        if handle is not None:
            handle[0].CloseHandle(handle[1])


def _clean_worker_locks(payload, pid, started_at):
    root = Path(payload["workspace_root"]).resolve()
    output = Path(payload["output"])
    output = (output if output.is_absolute() else root / output).resolve()
    document = (output / payload["entry"]["document_version_id"]).resolve()
    if not document.is_relative_to(root):
        raise ValueError("Lock cleanup path is outside the workspace")
    removed = []
    for lock in document.glob("*/active.lock"):
        resolved = lock.resolve()
        if not resolved.is_relative_to(document):
            continue
        data = json.loads(resolved.read_text(encoding="utf-8"))
        when = datetime.fromisoformat(data["started_at"].replace("Z", "+00:00"))
        if data.get("pid") == pid and when.tzinfo is not None and when >= started_at:
            resolved.unlink()
            removed.append(str(resolved.relative_to(root)))
    return removed


def run_pending(ledger, timeout_seconds=300, memory_mb=2048):
    if timeout_seconds <= 0 or memory_mb < 128:
        raise ValueError("Invalid timeout or memory limit")
    context = multiprocessing.get_context("spawn")
    summaries = []
    while (job := ledger.claim()) is not None:
        runtime = job["payload"].get("runtime", {})
        job_timeout = runtime.get("timeout_seconds", timeout_seconds)
        job_memory = runtime.get("memory_mb", memory_mb)
        if job_timeout <= 0 or job_memory < 128:
            ledger.finish(job, "failed", {"status": "failed", "error": "invalid_runtime_limits"})
            continue
        results = context.Queue(maxsize=1)
        child = context.Process(target=_worker, args=(job["payload"], results, job_memory))
        started = time.monotonic()
        started_at = datetime.now(timezone.utc)
        result = None
        try:
            child.start()
            ledger.heartbeat(job, child.pid)
            while child.is_alive():
                remaining = job_timeout - (time.monotonic() - started)
                if remaining <= 0:
                    child.terminate()
                    child.join(5)
                    if child.is_alive():
                        child.kill()
                        child.join(5)
                    result = {"status": "failed", "error": "document_timeout",
                              "elapsed_seconds": round(time.monotonic() - started, 3),
                              "worker_stopped": not child.is_alive()}
                    break
                child.join(min(1, remaining))
                ledger.heartbeat(job, child.pid)
            if result is None:
                try:
                    result = results.get(timeout=1)
                except queue.Empty:
                    result = {"status": "failed", "error": "worker_exited_without_result",
                              "exit_code": child.exitcode}
            if child.exitcode not in (0, None) and result["status"] != "failed":
                result = {"status": "failed", "error": "worker_exit_code",
                          "exit_code": child.exitcode, "worker_result": result}
        except Exception as exc:
            result = {"status": "failed", "error": type(exc).__name__, "detail": str(exc)}
        finally:
            if child.pid is not None and child.is_alive():
                child.terminate()
                child.join(5)
                if child.is_alive():
                    child.kill()
                    child.join(5)
            results.close()
            if child.pid is not None and not child.is_alive():
                if result is not None and result["status"] == "failed":
                    try:
                        result["removed_worker_locks"] = _clean_worker_locks(job["payload"], child.pid, started_at)
                    except Exception as exc:
                        result["lock_cleanup_error"] = str(exc)
                child.close()
        status = result["status"]
        state = ("failed" if status == "failed" else "unsupported_format"
                 if status == "unsupported_format" else "needs_review")
        result["publication_status"] = "quarantined"
        ledger.finish(job, state, result)
        summaries.append({"job_id": job["job_id"], "state": state, **result})
    return summaries
