"""容器入口：等 ollama 就绪 → 拉模型（首次）→ 运行需求雷达。

只用标准库，镜像无需额外安装 curl。
"""

from __future__ import annotations

import json
import os
import sys
import time
import urllib.error
import urllib.request

OLLAMA_API = os.getenv("OLLAMA_API", "http://ollama:11434").rstrip("/")
OLLAMA_MODEL = os.getenv("OLLAMA_MODEL", "qwen2.5:7b")
WAIT_SECONDS = int(os.getenv("OLLAMA_WAIT_SECONDS", "120"))


def wait_for_ollama() -> None:
    print(f"等待 ollama ({OLLAMA_API}) 就绪 …", flush=True)
    deadline = time.time() + WAIT_SECONDS
    while time.time() < deadline:
        try:
            urllib.request.urlopen(f"{OLLAMA_API}/api/tags", timeout=5)
            return
        except (urllib.error.URLError, OSError):
            time.sleep(2)
    sys.exit(f"ollama 在 {WAIT_SECONDS}s 内未就绪，请检查 ollama 服务")


def pull_model() -> None:
    print(f"拉取模型 {OLLAMA_MODEL}（首次较慢，之后走缓存）…", flush=True)
    req = urllib.request.Request(
        f"{OLLAMA_API}/api/pull",
        data=json.dumps({"name": OLLAMA_MODEL, "stream": False}).encode(),
        headers={"Content-Type": "application/json"},
    )
    try:
        with urllib.request.urlopen(req, timeout=None) as resp:
            status = json.load(resp).get("status")
    except urllib.error.HTTPError as e:
        sys.exit(f"拉取模型失败: HTTP {e.code} {e.read().decode(errors='replace')}")
    except (urllib.error.URLError, OSError) as e:
        sys.exit(f"拉取模型失败: {e}")
    if status != "success":
        sys.exit(f"拉取模型失败: {status}")


def main() -> None:
    wait_for_ollama()
    pull_model()
    print("开始运行 …", flush=True)
    os.execvp(sys.executable, [sys.executable, "-m", "demand_radar", "run", *sys.argv[1:]])


if __name__ == "__main__":
    main()
