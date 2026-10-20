"""recommend 命令：检测本机配置，按 models.toml 的档位表推荐抽取模型和嵌入模型，可写进 .env。

档位从上往下匹配，取第一个条件全部满足的。检测不准时（例如在 Docker 里看不到 Mac 的 GPU），
用 --apple / --ram-gb / --vram-gb 手动指定宿主机配置。
"""

from __future__ import annotations

import json
import os
import platform
import tomllib

from .hardware import Hardware, detect

RUNTIMES = ("ollama", "mlx")
# 每种运行方式要叠加的 compose 文件（写进 .env 的 COMPOSE_FILE，之后 docker compose up 自动带上）
_COMPOSE = {
    "ollama": "docker-compose.yml",
    "ollama-nvidia": "docker-compose.yml:docker-compose.nvidia.yml",
    "mlx": "docker-compose.yml:docker-compose.mac-gpu.yml",
}


def load_tiers(path: str) -> list[dict]:
    try:
        with open(path, "rb") as f:
            tiers = tomllib.load(f).get("tier") or []
    except FileNotFoundError:
        raise SystemExit(f"找不到模型档位表 {path}。用 --models-file 指定，或去掉这个参数用默认的") from None
    except tomllib.TOMLDecodeError as e:
        raise SystemExit(f"模型档位表 {path} 格式有误：{e}") from None
    for t in tiers:
        missing = [k for k in ("id", "runtime", "extract", "embed") if not t.get(k)]
        if missing:
            raise SystemExit(f"模型档位表 {path} 里的档位 {t.get('id', '?')} 缺少 {', '.join(missing)}")
        if t["runtime"] not in RUNTIMES:
            raise SystemExit(f"档位 {t['id']} 的 runtime 只能是 {' / '.join(RUNTIMES)}，现在是 {t['runtime']}")
    return tiers


def matches(tier: dict, hw: Hardware) -> bool:
    w = tier.get("when") or {}
    if "apple_silicon" in w and bool(w["apple_silicon"]) != hw.apple_silicon:
        return False
    if hw.ram_gb < float(w.get("min_ram_gb", 0)):
        return False
    return not hw.nvidia_vram_gb < float(w.get("min_vram_gb", 0))


def choose(tiers: list[dict], hw: Hardware, tier_id: str | None = None) -> dict | None:
    if tier_id:
        for t in tiers:
            if t["id"] == tier_id:
                return t
        raise SystemExit(f"档位表里没有 {tier_id}；可选：{', '.join(t['id'] for t in tiers)}")
    return next((t for t in tiers if matches(t, hw)), None)


def _compose_key(tier: dict) -> str:
    if tier["runtime"] == "ollama" and float((tier.get("when") or {}).get("min_vram_gb", 0)) > 0:
        return "ollama-nvidia"
    return tier["runtime"]


def env_for(tier: dict) -> dict[str, str]:
    """要写进 .env 的变量。docker compose 会自动读同目录的 .env。"""
    env = {"COMPOSE_FILE": _COMPOSE[_compose_key(tier)], "DR_EMBED_MODEL": tier["embed"]}
    if tier["runtime"] == "mlx":
        env["MLX_MODEL"] = tier["extract"]
        env["DR_EMBED_BASE_URL"] = "http://host.docker.internal:11434/v1"  # mlx-lm 没有嵌入接口
    else:
        env["DR_MODEL"] = tier["extract"]
    return env


def write_env(path: str, values: dict[str, str]) -> None:
    """改 .env 里这几个变量，其他行原样保留；没有的就追加。"""
    lines: list[str] = []
    if os.path.exists(path):
        with open(path, encoding="utf-8") as f:
            lines = f.read().splitlines()
    done = set()
    for i, line in enumerate(lines):
        key = line.split("=", 1)[0].strip()
        if key in values and not line.lstrip().startswith("#"):
            lines[i] = f"{key}={values[key]}"
            done.add(key)
    rest = [k for k in values if k not in done]
    if rest:
        lines.append("# demand-radar recommend 写入")
        lines += [f"{k}={values[k]}" for k in rest]
    with open(path, "w", encoding="utf-8") as f:
        f.write("\n".join(lines) + "\n")


def _linuxkit() -> bool:
    """Docker Desktop（Mac / Windows）的虚拟机内核名里带 linuxkit。"""
    return "linuxkit" in platform.release().lower()


def describe(hw: Hardware) -> str:
    parts = [hw.chip or f"{hw.os} {hw.arch}", f"{hw.ram_gb:g} GB 内存", f"{hw.cpu_cores} 核"]
    parts.append(f"NVIDIA {hw.gpu_name} {hw.nvidia_vram_gb:g} GB 显存" if hw.nvidia_vram_gb else "没有 NVIDIA 显卡")
    return "，".join(parts)


def how_to(tier: dict) -> list[str]:
    if tier["runtime"] == "mlx":
        return [
            "1. 在 Mac 上装 mlx-lm 并起服务（需要 Python 3.11+；端口别用 8080，Docker Desktop 占着）：",
            "     python3.12 -m pip install mlx-lm",
            f"     mlx_lm.server --model {tier['extract']} --port 8765",
            "2. 运行（.env 已写好 COMPOSE_FILE 的话，直接 docker compose up 即可）：",
            "     docker compose -f docker-compose.yml -f docker-compose.mac-gpu.yml up",
            "3. 要合并相似需求（cluster）时：mlx-lm 没有嵌入接口，嵌入走 Mac 上原生的 Ollama：",
            f"     brew install ollama && ollama pull {tier['embed']}",
            "   .env 里的 DR_EMBED_BASE_URL 已指向它",
        ]
    lines = []
    if _compose_key(tier) == "ollama-nvidia":
        lines.append("1. 宿主机要装好 NVIDIA 驱动和 NVIDIA Container Toolkit，叠加 GPU 覆盖文件运行：")
        lines.append("     docker compose -f docker-compose.yml -f docker-compose.nvidia.yml up")
    else:
        lines.append("1. 直接运行：docker compose up")
    lines.append(f"   入口脚本首次运行会自动拉取 {tier['extract']} 和 {tier['embed']}")
    return lines


def report(hw: Hardware, tier: dict | None, tiers: list[dict], forced: bool) -> str:
    out = [f"本机配置：{describe(hw)}"]
    if hw.in_docker:
        out.append("  ⚠ 在容器里运行：看到的是容器的内存和 CPU，看不到宿主机的 GPU。")
        if _linuxkit():
            out.append("    这是 Docker Desktop 的虚拟机。宿主机如果是 Apple 芯片的 Mac，请加 --apple --ram-gb <Mac 内存>；"
                       "有 NVIDIA 显卡请加 --vram-gb <显存>。")
    if not tier:
        out.append("档位表里没有适合这台机器的档位，请在 models.toml 末尾加一个 when = {} 的兜底档位。")
        return "\n".join(out)
    out += [
        "",
        f"推荐档位：{tier['id']} — {tier.get('name', '')}" + ("（手动指定）" if forced else ""),
        f"  抽取模型：{tier['extract']}（{'mlx-lm，Mac GPU' if tier['runtime'] == 'mlx' else 'Ollama'}）",
        f"  嵌入模型：{tier['embed']}",
    ]
    if tier.get("sec_per_item"):
        out.append(f"  预计每条：约 {tier['sec_per_item']:g} 秒")
    if tier.get("quality"):
        out.append(f"  质量：{tier['quality']}" + ("" if tier.get("measured") else "（估算，未在本项目实测）"))
    if tier.get("note"):
        out.append(f"  说明：{tier['note']}")
    out += ["", "怎么用：", *[f"  {s}" for s in how_to(tier)]]
    out += ["", "写进 .env（docker compose 自动读取）：",
            *[f"  {k}={v}" for k, v in env_for(tier).items()],
            "  加 --write-env 由本命令写入；想换档位用 --tier <id>，看全部档位用 --list。"]
    return "\n".join(out)


def list_tiers(tiers: list[dict], hw: Hardware) -> str:
    rows = []
    for t in tiers:
        ok = "✓" if matches(t, hw) else " "
        rows.append(f"  {ok} {t['id']:<14} {t.get('name', ''):<24} {t['runtime']:<6} {t['extract']}"
                    + ("" if t.get("measured") else "  （估算）"))
    return "档位（✓ 表示本机满足条件，从上往下取第一个）：\n" + "\n".join(rows)


def override(hw: Hardware, apple: bool | None, ram_gb: float, vram_gb: float | None) -> Hardware:
    """用户手动给的宿主机配置优先于检测结果（在 Docker 里看不到 Mac 的 GPU 和真实内存）。"""
    if apple is not None and apple != hw.apple_silicon:
        hw.apple_silicon = apple
        hw.chip = "Apple 芯片（手动指定）" if apple else ""
    if ram_gb:
        hw.ram_gb = ram_gb
    if vram_gb is not None:
        hw.nvidia_vram_gb = vram_gb
        hw.gpu_name = hw.gpu_name or "（手动指定）"
    return hw


def run(cfg) -> int:
    hw = override(detect(), cfg.hw_apple, cfg.hw_ram_gb, cfg.hw_vram_gb)
    tiers = load_tiers(cfg.models_file)
    if cfg.list_tiers:
        print(list_tiers(tiers, hw))
        return 0
    tier = choose(tiers, hw, cfg.tier)
    if cfg.as_json:
        print(json.dumps({"hardware": hw.to_dict(), "tier": tier, "env": env_for(tier) if tier else {}},
                         ensure_ascii=False, indent=2))
    else:
        print(report(hw, tier, tiers, bool(cfg.tier)))
    if cfg.write_env and tier:
        if hw.in_docker:
            print("\n在容器里写不到宿主机的 .env：请把上面几行复制到 demand-radar 目录下的 .env。")
        else:
            write_env(cfg.env_file, env_for(tier))
            print(f"\n已写入 {cfg.env_file}。")
    return 0 if tier else 1
