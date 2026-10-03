"""Make everything this backend starts die with it, however it dies.

The desktop app stops the backend with `taskkill /T`, which walks the process
tree. On a machine short of memory taskkill itself fails to start (Windows
logged it as 0xc0000142 on the machine that later crashed), and then FFmpeg
and Ollama's model runners survive the app, holding RAM, VRAM and commit
that nothing will ever give back until a reboot.

A Windows Job Object with KILL_ON_JOB_CLOSE closes that gap in the kernel:
this process joins the job, every child it starts after that joins too, and
when this process exits for any reason (quit, crash, killed) the handle
closes and Windows ends the rest. No helper process has to start for it.

Windows only; a no-op elsewhere and whenever the call is refused.
"""

import os

_job = None  # the handle must stay open for the life of the process


def bind_children_to_this_process() -> bool:
    global _job
    if os.name != "nt" or _job is not None:
        return _job is not None
    try:
        import ctypes
        import ctypes.wintypes as w

        kernel32 = ctypes.WinDLL("kernel32", use_last_error=True)

        class IO_COUNTERS(ctypes.Structure):
            _fields_ = [(n, ctypes.c_ulonglong) for n in (
                "ReadOperationCount", "WriteOperationCount", "OtherOperationCount",
                "ReadTransferCount", "WriteTransferCount", "OtherTransferCount")]

        class BASIC_LIMIT(ctypes.Structure):
            _fields_ = [
                ("PerProcessUserTimeLimit", ctypes.c_int64),
                ("PerJobUserTimeLimit", ctypes.c_int64),
                ("LimitFlags", w.DWORD),
                ("MinimumWorkingSetSize", ctypes.c_size_t),
                ("MaximumWorkingSetSize", ctypes.c_size_t),
                ("ActiveProcessLimit", w.DWORD),
                ("Affinity", ctypes.c_size_t),
                ("PriorityClass", w.DWORD),
                ("SchedulingClass", w.DWORD),
            ]

        class EXTENDED_LIMIT(ctypes.Structure):
            _fields_ = [
                ("BasicLimitInformation", BASIC_LIMIT),
                ("IoInfo", IO_COUNTERS),
                ("ProcessMemoryLimit", ctypes.c_size_t),
                ("JobMemoryLimit", ctypes.c_size_t),
                ("PeakProcessMemoryUsed", ctypes.c_size_t),
                ("PeakJobMemoryUsed", ctypes.c_size_t),
            ]

        JOB_OBJECT_LIMIT_KILL_ON_JOB_CLOSE = 0x2000
        JobObjectExtendedLimitInformation = 9

        kernel32.CreateJobObjectW.restype = w.HANDLE
        job = kernel32.CreateJobObjectW(None, None)
        if not job:
            return False
        info = EXTENDED_LIMIT()
        info.BasicLimitInformation.LimitFlags = JOB_OBJECT_LIMIT_KILL_ON_JOB_CLOSE
        if not kernel32.SetInformationJobObject(
            w.HANDLE(job), JobObjectExtendedLimitInformation,
            ctypes.byref(info), ctypes.sizeof(info),
        ):
            kernel32.CloseHandle(w.HANDLE(job))
            return False
        kernel32.GetCurrentProcess.restype = w.HANDLE
        me = w.HANDLE(kernel32.GetCurrentProcess())  # the pseudo-handle -1
        if not kernel32.AssignProcessToJobObject(w.HANDLE(job), me):
            # Already in a job that forbids nesting. Nothing lost: the
            # desktop app's taskkill still applies.
            kernel32.CloseHandle(w.HANDLE(job))
            return False
        _job = job
        return True
    except Exception:
        return False
