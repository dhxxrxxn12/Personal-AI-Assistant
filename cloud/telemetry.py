"""cloud/telemetry.py — real host telemetry for the JARVIS command center.

Every value returned here is measured on the machine running the Cloud server.
Nothing is synthesised: when a sensor is unavailable (for example CPU
temperature on Apple Silicon, or GPU load without NVML) the field is reported
as ``None`` together with an ``available: false`` flag so the HUD can display
"n/a" instead of a fabricated number.
"""

from __future__ import annotations

import ctypes
import platform
import socket
import threading
import time
from datetime import datetime, timezone
from typing import Any, Dict, Optional

try:  # psutil is a hard requirement of the project (actions/system_monitor.py)
    import psutil
except Exception:  # pragma: no cover - only when dependencies are incomplete
    psutil = None  # type: ignore


_GB = 1024 ** 3


def _round(value: Optional[float], digits: int = 1) -> Optional[float]:
    if value is None:
        return None
    try:
        return round(float(value), digits)
    except (TypeError, ValueError):
        return None


def _human_uptime(seconds: float) -> str:
    seconds = max(0, int(seconds))
    days, rem = divmod(seconds, 86400)
    hours, rem = divmod(rem, 3600)
    minutes = rem // 60
    if days:
        return f"{days}d {hours}h {minutes}m"
    if hours:
        return f"{hours}h {minutes}m"
    return f"{minutes}m"


# ----------------------------------------------------------------------
# Hardware probes that need no subprocess
# ----------------------------------------------------------------------

_nvml_state: Dict[str, Any] = {"tested": False, "lib": None}


def _gpu_percent() -> Optional[float]:
    """NVIDIA utilisation via NVML (ctypes). None when NVML is unavailable."""
    if _nvml_state["tested"] and _nvml_state["lib"] is None:
        return None
    try:
        class _Util(ctypes.Structure):
            _fields_ = [("gpu", ctypes.c_uint), ("memory", ctypes.c_uint)]

        lib = _nvml_state["lib"]
        if lib is None:
            names = (
                "nvml",
                "libnvidia-ml.so.1",
                "libnvidia-ml.so",
                "libnvidia-ml.dylib",
            )
            loader = ctypes.WinDLL if platform.system() == "Windows" else ctypes.CDLL
            for name in names:
                try:
                    candidate = loader(name)
                    candidate.nvmlInit_v2()
                    lib = candidate
                    break
                except Exception:
                    continue
            _nvml_state["tested"] = True
            _nvml_state["lib"] = lib
        if lib is None:
            return None

        handle = ctypes.c_void_p()
        lib.nvmlDeviceGetHandleByIndex_v2(0, ctypes.byref(handle))
        util = _Util()
        lib.nvmlDeviceGetUtilizationRates(handle, ctypes.byref(util))
        return float(util.gpu)
    except Exception:
        _nvml_state["tested"] = True
        _nvml_state["lib"] = None
        return None


def _cpu_temp_celsius() -> Optional[float]:
    """CPU package temperature via psutil. None where the OS does not expose it."""
    if psutil is None:
        return None
    try:
        sensors = psutil.sensors_temperatures()
    except Exception:
        return None
    if not sensors:
        return None
    preferred = ("coretemp", "k10temp", "cpu_thermal", "cpu-thermal", "acpitz")
    for name in preferred:
        entries = sensors.get(name)
        if entries:
            return float(entries[0].current)
    for entries in sensors.values():
        if entries:
            return float(entries[0].current)
    return None


def _host_ip() -> Optional[str]:
    try:
        return socket.gethostbyname(socket.gethostname())
    except Exception:
        return None


# ----------------------------------------------------------------------
# Telemetry sampler
# ----------------------------------------------------------------------

class Telemetry:
    """Sampled host metrics with a short cache so polling stays cheap."""

    def __init__(self, cache_seconds: float = 1.0) -> None:
        self._cache_seconds = cache_seconds
        self._lock = threading.RLock()
        self._cached: Optional[Dict[str, Any]] = None
        self._cached_at = 0.0
        self._net_counters: Optional[Any] = None
        self._net_at = 0.0

    def sample(self) -> Dict[str, Any]:
        with self._lock:
            now = time.monotonic()
            if self._cached is not None and (now - self._cached_at) < self._cache_seconds:
                return self._cached
            collected = self._collect()
            self._cached = collected
            self._cached_at = now
            return collected

    # -- internals -----------------------------------------------------

    def _empty(self, reason: str = "") -> Dict[str, Any]:
        return {
            "available": False,
            "reason": reason,
            "cpu": {"available": False, "percent": None, "physical_cores": None,
                    "logical_cores": None, "frequency_mhz": None, "load_avg": None},
            "memory": {"available": False, "percent": None, "used_gb": None, "total_gb": None},
            "network": self._network(),
            "gpu": {"available": False, "percent": None},
            "temperature": {"available": False, "celsius": None},
            "disk": {"available": False, "percent": None, "used_gb": None, "total_gb": None},
            "uptime": {"seconds": None, "human": None},
            "process_count": None,
            "os": self._os_block(),
            "collected_at": datetime.now(timezone.utc).isoformat(),
        }

    def _network(self) -> Dict[str, Any]:
        base: Dict[str, Any] = {
            "available": False,
            "rx_kbps": None,
            "tx_kbps": None,
            "rx_total_gb": None,
            "tx_total_gb": None,
            "hostname": socket.gethostname(),
            "ip": _host_ip(),
        }
        if psutil is None:
            return base
        try:
            counters = psutil.net_io_counters()
        except Exception:
            return base

        now = time.monotonic()
        rx_kbps = tx_kbps = None
        if self._net_counters is not None:
            elapsed = max(now - self._net_at, 1e-3)
            rx_kbps = max(0.0, (counters.bytes_recv - self._net_counters.bytes_recv) / elapsed / 1024)
            tx_kbps = max(0.0, (counters.bytes_sent - self._net_counters.bytes_sent) / elapsed / 1024)
        self._net_counters = counters
        self._net_at = now

        base.update({
            "available": True,
            "rx_kbps": _round(rx_kbps),
            "tx_kbps": _round(tx_kbps),
            "rx_total_gb": _round(counters.bytes_recv / _GB, 2),
            "tx_total_gb": _round(counters.bytes_sent / _GB, 2),
        })
        return base

    def _os_block(self) -> Dict[str, Any]:
        return {
            "system": platform.system() or None,
            "release": platform.release() or None,
            "machine": platform.machine() or None,
            "architecture": platform.machine() or None,
            "processor": platform.processor() or platform.machine() or None,
            "python": platform.python_version(),
            "hostname": socket.gethostname(),
        }

    def _collect(self) -> Dict[str, Any]:
        if psutil is None:
            return self._empty("psutil is not installed")

        try:
            cpu_percent = psutil.cpu_percent(interval=None)
        except Exception:
            cpu_percent = None

        try:
            memory = psutil.virtual_memory()
            memory_block = {
                "available": True,
                "percent": _round(memory.percent),
                "used_gb": _round(memory.used / _GB, 1),
                "total_gb": _round(memory.total / _GB, 1),
            }
        except Exception:
            memory_block = {"available": False, "percent": None, "used_gb": None, "total_gb": None}

        try:
            frequency = psutil.cpu_freq()
            frequency_mhz = _round(frequency.current, 0) if frequency else None
        except Exception:
            frequency_mhz = None

        try:
            load_avg = [round(value, 2) for value in psutil.getloadavg()]
        except Exception:
            load_avg = None

        try:
            uptime_seconds = max(0.0, time.time() - psutil.boot_time())
            uptime_block = {"seconds": int(uptime_seconds), "human": _human_uptime(uptime_seconds)}
        except Exception:
            uptime_block = {"seconds": None, "human": None}

        try:
            process_count = len(psutil.pids())
        except Exception:
            process_count = None

        try:
            disk = psutil.disk_usage("/")
            disk_block = {
                "available": True,
                "percent": _round(disk.percent),
                "used_gb": _round(disk.used / _GB, 1),
                "total_gb": _round(disk.total / _GB, 1),
            }
        except Exception:
            disk_block = {"available": False, "percent": None, "used_gb": None, "total_gb": None}

        temperature = _cpu_temp_celsius()
        gpu = _gpu_percent()

        return {
            "available": True,
            "reason": "",
            "collected_at": datetime.now(timezone.utc).isoformat(),
            "cpu": {
                "available": cpu_percent is not None,
                "percent": _round(cpu_percent),
                "physical_cores": psutil.cpu_count(logical=False),
                "logical_cores": psutil.cpu_count(logical=True),
                "frequency_mhz": frequency_mhz,
                "load_avg": load_avg,
            },
            "memory": memory_block,
            "network": self._network(),
            "gpu": {"available": gpu is not None, "percent": _round(gpu)},
            "temperature": {"available": temperature is not None, "celsius": _round(temperature)},
            "disk": disk_block,
            "uptime": uptime_block,
            "process_count": process_count,
            "os": self._os_block(),
        }


# ----------------------------------------------------------------------
# Subsystem health (used by /system/status)
# ----------------------------------------------------------------------

class SubsystemHealth:
    """Last observed outcome per subsystem, so the HUD shows real state."""

    def __init__(self) -> None:
        self._lock = threading.RLock()
        self._entries: Dict[str, Dict[str, Any]] = {}

    def mark(self, key: str, ok: bool, detail: str = "") -> None:
        with self._lock:
            self._entries[key] = {
                "ok": bool(ok),
                "detail": str(detail),
                "at": datetime.now(timezone.utc).isoformat(),
            }

    def get(self, key: str) -> Optional[Dict[str, Any]]:
        with self._lock:
            entry = self._entries.get(key)
            return dict(entry) if entry else None

    def snapshot(self) -> Dict[str, Dict[str, Any]]:
        with self._lock:
            return {key: dict(value) for key, value in self._entries.items()}


telemetry = Telemetry()
health = SubsystemHealth()