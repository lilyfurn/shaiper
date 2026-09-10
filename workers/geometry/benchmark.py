"""Observed timings and memory only. Physical accuracy and visual review stay unset."""

import importlib.metadata
import platform
import sys


def peak_process_memory() -> dict:
    try:
        if sys.platform == "win32":
            import ctypes
            from ctypes import wintypes

            class Counters(ctypes.Structure):
                _fields_ = [("cb", wintypes.DWORD), ("PageFaultCount", wintypes.DWORD)] + [
                    (name, ctypes.c_size_t) for name in (
                        "PeakWorkingSetSize", "WorkingSetSize", "QuotaPeakPagedPoolUsage", "QuotaPagedPoolUsage",
                        "QuotaPeakNonPagedPoolUsage", "QuotaNonPagedPoolUsage", "PagefileUsage", "PeakPagefileUsage",
                    )]
            counters = Counters()
            counters.cb = ctypes.sizeof(counters)
            kernel = ctypes.WinDLL("kernel32", use_last_error=True)
            psapi = ctypes.WinDLL("psapi", use_last_error=True)
            kernel.GetCurrentProcess.restype = wintypes.HANDLE
            psapi.GetProcessMemoryInfo.argtypes = [wintypes.HANDLE, ctypes.POINTER(Counters), wintypes.DWORD]
            if not psapi.GetProcessMemoryInfo(kernel.GetCurrentProcess(), ctypes.byref(counters), counters.cb):
                raise OSError("GetProcessMemoryInfo failed")
            return {"peakProcessRssBytes": int(counters.PeakWorkingSetSize), "peakProcessRssSource": "windows_peak_working_set"}
        import resource
        peak = resource.getrusage(resource.RUSAGE_SELF).ru_maxrss
        return {"peakProcessRssBytes": int(peak if sys.platform == "darwin" else peak * 1024),
                "peakProcessRssSource": "getrusage_process_high_water_mark"}
    except (ImportError, OSError, AttributeError):
        return {"peakProcessRssBytes": None, "peakProcessRssSource": "unavailable"}


def environment() -> dict:
    packages = {}
    for name in ("numpy", "Pillow", "torch", "torchvision", "depth-anything-3", "xformers", "jsonschema"):
        try:
            packages[name] = importlib.metadata.version(name)
        except importlib.metadata.PackageNotFoundError:
            packages[name] = None
    return {"pythonVersion": platform.python_version(), "system": platform.system(),
            "machine": platform.machine(), "packages": packages}


def markdown_report(report: dict) -> str:
    metrics = report["metrics"]
    rows = "\n".join(f"| {key} | {'Not measured' if value is None else value} |" for key, value in metrics.items())
    return (
        "# Single-image benchmark run\n\n"
        f"Run: `{report['jobId']}`. Export validation: passed. Geometry usefulness: **not yet reviewed**.\n\n"
        "This is an estimated visible surface in arbitrary units. It is not a measured object scan.\n\n"
        "| Metric | Observed value |\n| --- | --- |\n" + rows + "\n\n"
        "Model-load time is a fresh model instance, not a measured machine/container cold start. "
        "CUDA memory is PyTorch allocator memory, not whole-device VRAM usage. "
        "Process RAM is its high-water mark through export validation, before bundle packaging.\n\n"
        "## Independent inspection (pending)\n\n"
        "- [ ] Open pointcloud.ply and surface.obj in an independent viewer.\n"
        "- [ ] Check color alignment, orientation, bounds, point/face counts and missing surfaces.\n"
        "- [ ] Record viewer/version, visible errors, object category and photograph permission.\n"
        "- [ ] Record independent physical dimensions and a measurement protocol.\n\n"
        "Physical accuracy, held-out dimension error, peak total VRAM and machine cold-start time "
        "are not measured by this command. Successful export does not pass the Block 0 usefulness gate.\n"
    )
