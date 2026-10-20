"""检测本机配置，给 recommend 命令选模型档位用。只用标准库，检测不到的项留空，不报错。

在 Docker 里运行时看到的是容器（Mac / Windows 上是 Docker Desktop 虚拟机）的内存和 CPU，
看不到 Mac 的 GPU。这种情况会标出来，由用户用 --apple / --ram-gb 告诉我们宿主机的真实配置。
"""

from __future__ import annotations

import os
import platform
import shutil
import subprocess
from dataclasses import asdict, dataclass

GB = 1024 ** 3


@dataclass
class Hardware:
    os: str                    # Darwin / Linux / Windows
    arch: str                  # arm64 / x86_64
    ram_gb: float              # 可用的总内存；容器里是容器的上限
    cpu_cores: int
    apple_silicon: bool
    chip: str = ""             # 例如 Apple M5
    nvidia_vram_gb: float = 0  # 最大一块 NVIDIA 显卡的显存，没有为 0
    gpu_name: str = ""
    in_docker: bool = False

    def to_dict(self) -> dict:
        return asdict(self)


def _run(cmd: list[str]) -> str:
    if not shutil.which(cmd[0]):
        return ""
    try:
        return subprocess.run(cmd, capture_output=True, text=True, timeout=10, check=False).stdout.strip()
    except (OSError, subprocess.SubprocessError):
        return ""


def _ram_bytes(system: str) -> int:
    if system == "Darwin":
        out = _run(["sysctl", "-n", "hw.memsize"])
        if out.isdigit():
            return int(out)
    try:
        return os.sysconf("SC_PAGE_SIZE") * os.sysconf("SC_PHYS_PAGES")
    except (ValueError, OSError, AttributeError):
        return 0


def _cgroup_limit() -> int:
    """容器的内存上限；没设上限或不在容器里返回 0。"""
    for path in ("/sys/fs/cgroup/memory.max", "/sys/fs/cgroup/memory/memory.limit_in_bytes"):
        try:
            with open(path) as f:
                v = f.read().strip()
        except OSError:
            continue
        if v.isdigit() and int(v) < 1 << 60:  # cgroup v1 没上限时是一个巨大的数
            return int(v)
    return 0


def _in_docker() -> bool:
    if os.path.exists("/.dockerenv"):
        return True
    try:
        with open("/proc/1/cgroup") as f:
            return "docker" in f.read()
    except OSError:
        return False


def _nvidia() -> tuple[float, str]:
    out = _run(["nvidia-smi", "--query-gpu=memory.total,name", "--format=csv,noheader,nounits"])
    best = (0.0, "")
    for line in out.splitlines():
        mib, _, name = line.partition(",")
        try:
            gb = float(mib) / 1024
        except ValueError:
            continue
        best = max(best, (gb, name.strip()))
    return best


def detect() -> Hardware:
    system = platform.system()
    arch = platform.machine().lower()
    ram = _ram_bytes(system)
    limit = _cgroup_limit()
    if limit:
        ram = min(ram, limit) if ram else limit
    apple = system == "Darwin" and arch in ("arm64", "aarch64")
    chip = _run(["sysctl", "-n", "machdep.cpu.brand_string"]) if system == "Darwin" else ""
    vram, gpu = _nvidia()
    return Hardware(
        os=system,
        arch=arch,
        ram_gb=round(ram / GB, 1),
        cpu_cores=os.cpu_count() or 0,
        apple_silicon=apple,
        chip=chip,
        nvidia_vram_gb=round(vram, 1),
        gpu_name=gpu,
        in_docker=_in_docker(),
    )
