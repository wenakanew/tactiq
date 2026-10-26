from __future__ import annotations

import os
import signal
import subprocess
import threading
import time


def _windows_job(process: subprocess.Popen):
    import ctypes
    from ctypes import wintypes

    class BasicLimits(ctypes.Structure):
        _fields_ = [("process_time", ctypes.c_longlong), ("job_time", ctypes.c_longlong),
                    ("flags", wintypes.DWORD), ("min_working", ctypes.c_size_t), ("max_working", ctypes.c_size_t),
                    ("active", wintypes.DWORD), ("affinity", ctypes.c_size_t),
                    ("priority", wintypes.DWORD), ("scheduling", wintypes.DWORD)]

    class IoCounters(ctypes.Structure):
        _fields_ = [(name, ctypes.c_ulonglong) for name in ("read_ops", "write_ops", "other_ops", "read_bytes", "write_bytes", "other_bytes")]

    class Limits(ctypes.Structure):
        _fields_ = [("basic", BasicLimits), ("io", IoCounters), ("process_memory", ctypes.c_size_t),
                    ("job_memory", ctypes.c_size_t), ("peak_process", ctypes.c_size_t), ("peak_job", ctypes.c_size_t)]

    kernel = ctypes.WinDLL("kernel32", use_last_error=True)
    kernel.CreateJobObjectW.argtypes = [ctypes.c_void_p, wintypes.LPCWSTR]
    kernel.CreateJobObjectW.restype = wintypes.HANDLE
    kernel.SetInformationJobObject.argtypes = [wintypes.HANDLE, ctypes.c_int, ctypes.c_void_p, wintypes.DWORD]
    kernel.AssignProcessToJobObject.argtypes = [wintypes.HANDLE, wintypes.HANDLE]
    kernel.OpenProcess.argtypes = [wintypes.DWORD, wintypes.BOOL, wintypes.DWORD]
    kernel.OpenProcess.restype = wintypes.HANDLE
    kernel.CloseHandle.argtypes = [wintypes.HANDLE]
    job = kernel.CreateJobObjectW(None, None)
    handle = kernel.OpenProcess(0x0101, False, process.pid)
    limits = Limits()
    limits.basic.flags = 0x2000  # Kill only this job's descendants when its handle closes.
    try:
        if not job or not handle or not kernel.SetInformationJobObject(job, 9, ctypes.byref(limits), ctypes.sizeof(limits)):
            raise OSError("Unable to create a bounded provider process job")
        if not kernel.AssignProcessToJobObject(job, handle):
            raise OSError("Unable to contain the provider process tree")
    except OSError:
        if job:
            kernel.CloseHandle(job)
        raise
    finally:
        if handle:
            kernel.CloseHandle(handle)
    return lambda: kernel.CloseHandle(job)


def run_resolver_process(command: list[str], deadline: float) -> str:
    environment = {key: value for key, value in os.environ.items()
                   if key.upper() in {"PATH", "SYSTEMROOT", "WINDIR", "TEMP", "TMP", "LANG", "LC_ALL"}}
    environment.update({"PYTHONIOENCODING": "utf-8", "PYTHONDONTWRITEBYTECODE": "1"})
    process = subprocess.Popen(
        command, stdin=subprocess.DEVNULL, stdout=subprocess.PIPE, stderr=subprocess.PIPE,
        env=environment, start_new_session=os.name != "nt",
    )
    close_job = None
    buffers = [bytearray(), bytearray()]
    overflow = threading.Event()

    def consume(stream, target: bytearray, limit: int):
        try:
            while chunk := stream.read(65536):
                if len(target) + len(chunk) > limit:
                    overflow.set()
                    break
                target.extend(chunk)
        finally:
            stream.close()

    def terminate():
        if close_job is not None:
            close_job()
        elif os.name != "nt":
            try:
                os.killpg(process.pid, signal.SIGKILL)
            except ProcessLookupError:
                pass
        elif process.poll() is None:
            process.kill()

    readers = []
    try:
        if os.name == "nt":
            close_job = _windows_job(process)
        for stream, target, limit in ((process.stdout, buffers[0], 2 * 1024 * 1024), (process.stderr, buffers[1], 65536)):
            reader = threading.Thread(target=consume, args=(stream, target, limit), daemon=True)
            reader.start()
            readers.append(reader)
        while process.poll() is None:
            if overflow.is_set():
                raise RuntimeError("Provider resolver exceeded its output limit.")
            if time.monotonic() >= deadline:
                raise RuntimeError("Provider resolution exceeded its time budget.")
            time.sleep(0.03)
        for reader in readers:
            reader.join(timeout=2)
        if overflow.is_set() or any(reader.is_alive() for reader in readers):
            raise RuntimeError("Provider resolver output did not complete within its bounds.")
        output = bytes(buffers[0]).decode("utf-8")
        if process.returncode and not output:
            raise RuntimeError("The bounded provider resolver could not start.")
        return output
    finally:
        terminate()
        process.wait(timeout=5)
        for reader in readers:
            reader.join(timeout=2)
