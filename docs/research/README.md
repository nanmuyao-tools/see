# 开源项目调研：识别 App 与统计流量

目标：在家用路由器（OpenWrt）上，按设备统计刷短视频（抖音、快手、B站、视频号、小红书）的时长。
本目录是对相关开源项目的源码学习笔记，用来指导我们自己的设计。

| 项目 | 版本（commit / 日期） | 定位 | 笔记 |
|---|---|---|---|
| [OpenAppFilter](https://github.com/destan19/OpenAppFilter) | 7fcf9e8 / 2026-09-24 | OpenWrt 内核模块：DPI 识别 App、拦截、按设备记录访问 | [openappfilter.md](openappfilter.md) |
| [nDPI](https://github.com/ntop/nDPI) | f1570f3 / 2026-10-16 | 主流开源 DPI 库：TLS/QUIC SNI、域名/IP 匹配 | [ndpi.md](ndpi.md) |
| [netifyd](https://gitlab.com/netify.ai/public/netify-agent) | c4907e3 / 2026-06-24 | 基于 nDPI 的流分析守护进程：流表、DNS 反查缓存、JSON 输出 | [netifyd-nlbwmon.md](netifyd-nlbwmon.md) |
| [nlbwmon](https://github.com/jow-/nlbwmon) | 29236be / 2025-06-02 | OpenWrt 官方流量统计：conntrack 计数、按 MAC 聚合、闪存保护 | [netifyd-nlbwmon.md](netifyd-nlbwmon.md) |
| [AdGuard Home](https://github.com/AdguardTeam/AdGuardHome) | fb7774f / 2026-10-16 | DNS 过滤：查询日志、客户端识别、按设备拦截与时间表 | [adguardhome.md](adguardhome.md) |

笔记中的 `文件:行号` 都对应上表中的版本。

## 核心结论

### 1. 没有现成项目能统计"短视频时长"

- 这些项目都只做到"识别 App"或"统计流量"。
- 唯一有时长功能的是 OpenAppFilter，但精度很粗：每分钟只要命中一次就记 60 秒，每台设备每分钟只上报前 3 个 App，一次 DNS 查询也算一分钟。

### 2. 国内短视频规则都要自己维护

| 项目 | 抖音 | 快手 | B站 | 小红书 | 视频号 |
|---|---|---|---|---|---|
| nDPI 内置 | ✗（只有 TikTok） | ✗ | ✗ | ✗ | ✗（微信不区分视频号） |
| netifyd 开源签名 | ✗（只有 TikTok） | ✗ | ✗ | ✗ | ✗ |
| OpenAppFilter 开源特征 | ✗（国内规则要在线付费拉取） | ✗ | ✗ | ✗ | ✗ |
| AdGuard Home 服务列表 | ✓（归在 `tiktok` 下） | ✗ | ✓ | ✓ | ✗ |

AdGuard Home 的 `filtering/servicelist.go` 是最好的起点，可以直接拿它的抖音和 B站域名表。

### 3. 识别手段对比

| 手段 | 谁在用 | 优点 | 缺点 |
|---|---|---|---|
| TLS SNI | 全部 DPI 项目 | 准，1–6 个包就能出结果 | 要求程序在数据路径上；ECH 普及后失效 |
| QUIC Initial 解密取 SNI | nDPI | 短视频大量走 QUIC，这一项必须有 | 实现稍复杂（HKDF + AES-GCM）；OAF 没做 |
| DNS 反查（IP→域名） | netifyd DHC、nDPI FPC | 不用解析每条流，开销很小 | 会被 HTTPDNS / DoH 绕过；CDN 共用 IP 时会误判 |
| IP 段 / ASN | nDPI、netifyd | 兜底 | 字节、快手大量使用公共 CDN，误判率高 |
| 屏蔽 QUIC，逼它回落 TCP | OAF `filter_quic` | 简单 | 改变了用户的网络行为 |

### 4. 流量统计手段

- **conntrack 计数**（nlbwmon）：不用抓包，零拷贝。订阅 NEW/DESTROY 事件，再定期用 `CTRZERO` dump 读出增量。前提是开启 `nf_conntrack_acct=1`。
- **netfilter hook 按包统计**（OAF）：最灵活，但要写内核模块。
- **两者都受 flow offload（软/硬件加速）影响**，要关掉加速，或者确认驱动会回填计数。

## 对我们设计的启发

```
           ┌──────────── 识别层（给每条连接打上 App 标签）────────────┐
DNS 应答 ──▶ DNS 缓存：(client_ip, server_ip) → qname，带 TTL       │
TLS/QUIC ──▶ SNI 提取（只看每条流的前几个包）                        ├─▶ 连接 → App
IP 段    ──▶ 兜底                                                   │
           └───────────────────────────────────────────────────────┘
                                   │
conntrack 计数（每 10–60s 增量）────┤
                                   ▼
             分钟桶：(MAC, App, minute) → 下行字节、包数
                                   ▼
     "活跃分钟"判定：下行 ≥ 阈值（如 200KB/min）才算在看，用来排除后台保活
                                   ▼
           会话合并：空闲 ≤ 2 分钟算同一次会话 → 时长、日报
```

具体来自哪个项目：

1. **域名 → App 匹配**：采用标签边界上的后缀匹配，多条命中时取最长的那条（nDPI 的 `ac_domain_match_handler`、netifyd 的逐级去掉左边一段）。规则少的话，用反转域名 trie 就够了。
2. **DNS 缓存**：参考 netifyd 的 DHC，改进两点：key 里加上客户端 IP，并且遵守 TTL。
3. **流量计数**：照搬 nlbwmon 的 conntrack 方案。如果路由器上还有别的程序在读计数，就改用 netifyd 的"读累计值、自己算差值"，避免互相清零。
4. **SNI**：如果要做，就移植 nDPI 的 `tls.c` 和 `quic.c`。OAF 的启发式扫描太粗：SNI 超过 40 字节直接丢弃，也不支持 QUIC。
5. **时长算法**：不要学 OAF 的"命中就算一分钟"。改成按下行字节判定活跃，再做会话化。同时可以借鉴专利里的"压低 DNS TTL，用查询次数 × TTL 估算时长"做交叉校验（AdGuard Home 的 `cache_ttl_min/max` 就能实现）。
6. **设备识别**：用 neigh 表查 MAC，用 DHCP 租约查主机名（nlbwmon、OAF）。DoH 场景下，给每台设备分配独立的 ClientID URL（AdGuard Home）。
7. **存储**：数据先写 `/tmp`，定期刷到闪存，用定长记录，限制保留代数（nlbwmon）。OAF 默认只写 `/tmp`，重启就丢。
8. **管控（可选）**：参考 AdGuard Home 的按客户端 BlockedServices + Weekly schedule。注意它的时间表区间表示"放行"时段。

## 实现路线建议

- **v0（只用 DNS，验证可行性）**：解析 dnsmasq 日志（或 AdGuard Home querylog）＋ conntrack 计数＋DNS 反查。全部在用户态完成，不写内核代码。
- **v1（加 SNI）**：用 NFQUEUE，或者用 pcap 只抓每条连接的前几个包，提取 TLS 和 QUIC 的 SNI，补上 HTTPDNS 造成的漏判。
- **v2（可选）**：如果性能不够，再考虑参考 OAF 写内核模块。

## 待实测验证

- 抖音、快手、B站、视频号、小红书实际使用的视频 CDN 域名（开"学习模式"抓包整理）。
- 这些 App 的 HTTPDNS 占比；屏蔽 HTTPDNS 服务后，App 是否会回落到系统 DNS。
- QUIC 流量占比。
- 下行阈值怎么定：拿手机的屏幕使用时间做真值来校准。
