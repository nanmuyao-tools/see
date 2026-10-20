"""容器入口：等 ollama 就绪 → 拉模型（首次）→ 运行需求雷达的 run / eval / digest / backfill / schedule / serve / cluster 命令。

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
OLLAMA_MODEL = os.getenv("OLLAMA_MODEL", "qwen3:4b-instruct-2507-q4_K_M")
# 相似需求合并用的嵌入模型（和 demand_radar/config.py 的默认值一致）。只有嵌入服务就是容器里的
# ollama 时（没单独设 DR_EMBED_BASE_URL）才由这里代拉，否则用户自己负责那个服务上有模型
EMBED_MODEL = os.getenv("DR_EMBED_MODEL", "qwen3-embedding:0.6b")
EMBED_ELSEWHERE = bool(os.getenv("DR_EMBED_BASE_URL"))
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


def pull_model(name: str = OLLAMA_MODEL) -> None:
    print(f"拉取模型 {name}（首次较慢，之后走缓存）…", flush=True)
    req = urllib.request.Request(
        f"{OLLAMA_API}/api/pull",
        data=json.dumps({"name": name, "stream": False}).encode(),
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


COMMANDS = ("run", "eval", "digest", "backfill", "schedule", "serve", "cluster", "recommend")
NEEDS_MODEL = ("run", "eval", "backfill", "schedule")  # digest / serve 只读数据库，不等模型；cluster 只要嵌入模型


def wants_cluster(args: list[str]) -> bool:
    """cluster 命令本身，或 run / schedule 带 --cluster（命令行或 DR_CLUSTER=1）。"""
    return args[0] == "cluster" or "--cluster" in args or os.getenv("DR_CLUSTER", "0").lower() in ("1", "true", "yes")


TOP_LEVEL_FLAGS = ("--version", "-h", "--help")
WRITES_OUTPUT = ("run", "digest", "backfill", "schedule", "cluster")
OUT_DIR = "/app/out"


def check_output_dir(command: str) -> None:
    """容器以普通用户（uid 1000）运行。Linux 上 ./out 若属于别的用户（例如被 root 自动创建），
    这里先说清楚怎么修，而不是跑完一轮才在写文件时报 PermissionError。"""
    if command not in WRITES_OUTPUT or not os.path.isdir(OUT_DIR):
        return
    # 目录可写还不够：v0.3.0 及以前容器以 root 运行，Linux 上 ./out 里已有的 demands.md、digest-*.md
    # 都属于 root，覆盖时才会报错——那时一整轮抽取已经白跑了
    stuck = [n for n in sorted(os.listdir(OUT_DIR))
             if os.path.isfile(os.path.join(OUT_DIR, n)) and not os.access(os.path.join(OUT_DIR, n), os.W_OK)]
    if os.access(OUT_DIR, os.W_OK) and not stuck:
        return
    what = "没有写权限" if not stuck else f"里有 {len(stuck)} 个文件不能覆盖（如 {stuck[0]}，多半是旧版本以 root 写下的）"
    sys.exit(f"输出目录 ./out {what}（容器内用户 uid {os.getuid()}）。在宿主机 demand-radar 目录执行：\n"
             f"  sudo chown -R {os.getuid()}:{os.getgid()} out\n"
             "或在 docker-compose.yml 的 app 服务里把 user 改成你自己的 uid:gid（id -u / id -g 查看）。")


def main() -> None:
    args = sys.argv[1:]
    # 第一个参数是命令名就照用；--version / --help 原样交给程序；否则默认 run，其余参数原样传给它
    # 例：docker compose run --rm app digest --days 14 / docker compose run --rm app --limit 20
    if args and args[0] in TOP_LEVEL_FLAGS:
        os.execvp(sys.executable, [sys.executable, "-m", "demand_radar", *args])
    if not args or args[0] not in COMMANDS:
        args = ["run", *args]
    check_output_dir(args[0])
    if OLLAMA_API and (args[0] in NEEDS_MODEL or wants_cluster(args) and not EMBED_ELSEWHERE):
        wait_for_ollama()
    if args[0] in NEEDS_MODEL and OLLAMA_API:  # digest / serve 只读数据库，不用等模型
        pull_model(OLLAMA_MODEL)
    if wants_cluster(args) and OLLAMA_API and not EMBED_ELSEWHERE:
        pull_model(EMBED_MODEL)
    print("开始运行 …", flush=True)
    os.execvp(sys.executable, [sys.executable, "-m", "demand_radar", *args])


if __name__ == "__main__":
    main()
