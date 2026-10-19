"""容器入口：等 ollama 就绪 → 拉模型（首次）→ 运行需求雷达的 run / eval / digest / backfill / schedule / serve 命令。

只用标准库，镜像无需额外安装 curl。
"""

from __future__ import annotations

import json
import os
import sys
import time
import urllib.error
import urllib.request

# OLLAMA_API 留空表示模型不由容器里的 ollama 提供（例如 Mac 上原生跑的 mlx-lm），
# 这时不等待也不拉模型，直接运行；模型地址由 OLLAMA_BASE_URL / DR_PROVIDER 决定
OLLAMA_API = os.getenv("OLLAMA_API", "http://ollama:11434").rstrip("/")
OLLAMA_MODEL = os.getenv("OLLAMA_MODEL", "qwen2.5:3b")
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


COMMANDS = ("run", "eval", "digest", "backfill", "schedule", "serve")
NEEDS_MODEL = ("run", "eval", "backfill", "schedule")  # digest / serve 只读数据库，不等模型


def main() -> None:
    args = sys.argv[1:]
    # 第一个参数是命令名就照用；否则默认 run，其余参数原样传给它
    # 例：docker compose run --rm app digest --days 14 / docker compose run --rm app --limit 20
    if not args or args[0] not in COMMANDS:
        args = ["run", *args]
    if args[0] in NEEDS_MODEL and OLLAMA_API:  # digest 只读数据库，不用等模型
        wait_for_ollama()
        pull_model()
    print("开始运行 …", flush=True)
    os.execvp(sys.executable, [sys.executable, "-m", "demand_radar", *args])


if __name__ == "__main__":
    main()
