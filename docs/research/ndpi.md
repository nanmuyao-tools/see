# nDPI 学习笔记

版本 commit f1570f3。下文路径都相对仓库根目录。

## 1. 识别流程

入口是 `ndpi_detection_process_packet`（`src/lib/ndpi_main.c:11130`），实际工作在 `ndpi_internal_detection_process_packet`（`:10888`）中完成：

1. `ndpi_init_packet` 解析 L3/L4，`ndpi_connection_tracking` 跟踪连接（`:10909-10913`）。
2. 一条流处理到 `packets_limit_per_flow` 个包（默认 32，`ndpi_config.c:208`）就强制放弃（`:10917`）。
3. 设置了 `extra_packets_func` 时，只跑额外解析（`:10931`）。
4. 第一个包可以先过 nBPF 自定义规则（`:10947`）。
5. `do_guess`（`:10582`）：先按端口猜，再按 IP 段猜（Patricia 树，`ndpi_guess_host_protocol_id`，`:9331`）。只有猜中的是**用户自定义协议**才立即定案，否则只当作提示（`:10612-10635`）。
6. 调度各协议解析器（`check_ndpi_detection_func`，`:9222`）：先跑端口对应的那个，再线性扫描其他适用的解析器，第一个识别成功就停（`:9248-9266`）。
7. 放弃时兜底（`:10057-10092`）：先查缓存，再看端口和 IP。

**优先级**：自定义规则 > DPI 解析（含 SNI/Host 匹配）> 缓存 > 端口 / IP 猜测。

域名匹配不是单独的一步：TLS、QUIC、HTTP、DNS 的解析器拿到主机名后，调用 `ndpi_match_hostname_protocol` → `ndpi_match_host_subprotocol`（`:12617`），得到 `TLS.TikTok` 这样的子协议。

## 2. TLS SNI 与 QUIC

- **TLS**：在 `processClientServerHello`（`src/lib/protocols/tls.c:2680`）中，遇到扩展 id 0（server_name）时，用 `ndpi_hostname_sni_set` 规范化，再做域名匹配（`:3266-3316`）。证书 SAN 也会参与匹配（`:1030`）。
- **QUIC**（`quic.c`，入口 `ndpi_search_quic`，`:1580`）：
  - **派生初始密钥**（`decrypt_initial_packet`，`:637`）：`initial_secret = HKDF-Extract(salt, 客户端 DCID)`，然后 `HKDF-Expand-Label("client in")`（`:559-628`）。
  - **派生包密钥**：再用 `"quic key"`、`"quic iv"`、`"quic hp"` 派生；QUIC v2 用 `"quicv2 *"`（`:368-399`）。
  - **算法**：哈希用 SHA256，头部保护用 AES-128-ECB，载荷用 AES-128-GCM。
  - **初始盐**：v1 是 `38762cf7f55934b34d179ae6a4c80cadccbb7f0a`（`:609-611`）。另外支持 v2、draft-29 和 gQUIC Q050/T05x。
  - **CRYPTO 帧重组**：帧跨多个 UDP 包时，用位图重组（`:722-850`）。
  - **复用 TLS 解析**：重组后交给 `process_tls` 复用 ClientHello 解析（`:1044`）；gQUIC 走 `process_chlo` 读 SNI 标签（`:1078`）。
  - **加密库**：可以用 libgcrypt，也有内置的轻量实现。

## 3. 域名匹配

- **规则表**：`host_match[]`（`src/lib/ndpi_content_match.c.inc:397`），每条是 `{pattern, 协议名, 协议ID, category, breed, level}`，由 `init_string_based_protocols` 载入 Aho-Corasick 自动机（`ndpi_main.c:1066`），匹配时不区分大小写（`:4320`）。
- **匹配回调**：`ac_domain_match_handler`（`:3071`）只接受两种命中：
  - 完全匹配；
  - 命中位置前一个字符是 `.`，也就是在标签边界上的后缀匹配。所以 `ws.amazon.com` 不会误中 `aws.amazon.com`。

  多条规则同时命中时，取更长、更精确的那条。以 `.` 结尾的模式（比如 `weixin.`）表示前缀匹配。

**相关条目原文**：

- TikTok（`:1010-1029`）：`musical.ly`、`byteoversea.com`、`ibyteimg.com`、`tiktokcdn.com`、`tiktokv.com/.us/.eu`、`bytecdn.cn`、`muscdn.com`、`tiktok.com`、`byted.org`、`byteicdn.com`、`tiktokcdn-us.com`、`tiktokcdn-eu.com` 等，协议 ID 49，分类 SOCIAL_NETWORK。
- WeChat（`:940-946`）：`wechat.com`、`wechatapp.com`、`we.chat`、`wx.`、`weixin.`、`mmsns.qpic.cn`，归类为 CHAT，**不区分视频号**。
- QQ（`:838-839`）：`qq.com`、`gtimg.com`；腾讯视频：`v.qq.com`（`:1332`）。
- **找不到**：douyin、kuaishou、bilibili、xiaohongshu，以及 `amemv`、`snssdk`、`hdslb`、`xhscdn`、`gifshow`、`kwai`、`pstatp`。

## 4. IP 列表匹配

- 手写表 `host_protocol_list[]`（`content_match.c.inc:24`）加上自动生成的 `src/lib/inc_generated/ndpi_asn_*.c.inc`，每条是 `{网络地址, 前缀长度, 协议}`。
- 生成脚本 `utils/asn_update.sh:83` 按 ASN 拉取路由。例如腾讯 AS45090，生成了 741 行。
- 载入 Patricia 树，查询时做最长前缀匹配（`ndpi_main.c:3314-3460`）。
- 字节和快手大量使用公共 CDN 与云厂商 IP，按 IP 识别只能当兜底。

## 5. 自定义规则 protos.txt

语法见 `example/protos.txt`，加载代码在 `ndpi_load_protocols_file`（`ndpi_main.c:6678`）和 `ndpi_handle_rule`（`:5646`）：

```
tcp:81,udp:5062@Name=ID                 # 端口
host:"a.com",host:"b.com"@Name=ID       # 域名（进入同一个 AC 自动机，后缀匹配）
ip:1.2.3.4/32:443@Name=ID               # IP，可以追加 ,cat=,breed=
nbpf:"host 1.2.3.4"@Name                # nBPF
```

国内短视频规则示例（域名要实测验证）：

```
host:"douyin.com",host:"amemv.com",host:"douyinvod.com",host:"douyinpic.com",host:"snssdk.com"@Douyin=60010
host:"kuaishou.com",host:"gifshow.com",host:"kwaicdn.com",host:"yximgs.com"@Kuaishou=60011
host:"bilibili.com",host:"bilivideo.com",host:"hdslb.com"@Bilibili=60012
host:"xiaohongshu.com",host:"xhscdn.com"@Xiaohongshu=60013
host:"finder.video.qq.com",host:"channels.weixin.qq.com"@WeixinChannels=60014
```

因为多条命中时取最长匹配，更具体的自定义域名会胜过内置的 `qq.com`、`weixin.`。

## 6. 何时停止检测、内存占用、在路由器上能否跑

- **停止检测**：
  - 没有额外解析的流，一识别出来就停（`:11111`）。
  - TLS 识别后还会继续看 ServerHello 和证书，最多 UDP 20 个包、TCP 12 个包（`tls.c:1974`）。
  - 实际上带 SNI 的流通常 1–6 个包就出结果。
- **内存**：`struct ndpi_flow_struct` 有静态断言保证不超过 1392 字节（`src/include/ndpi_typedefs.h:2093`）。1 万条流约 14MB，再加上自动机和 Patricia 树的几 MB。
- **可行性**：OpenWrt 上已经有 ndpi-netfilter 等先例。建议裁剪：关掉熵计算、JA4、多余的 ASN 表，只保留 TLS、QUIC、HTTP、DNS 四个解析器。

## 7. 可借鉴 / 可复用

1. **整体思路**：解析出 SNI、Host、DNS 里的域名，再用后缀匹配映射到 App，不需要为每个 App 写专门的解析器。
2. **代码移植**：可以只移植 `tls.c` 的 SNI 解析和 `quic.c` 的 Initial 解密。
3. **匹配规则**：沿用标签边界后缀匹配和取最长匹配。
4. **DNS→IP 缓存**：参考 FPC（`lru.fpc_dns.*`，`ndpi_config.c:269-271`；`fpc_check_eval`，`ndpi_main.c:10658`）。对没有 SNI 或用了 ECH 的流，用目的 IP 反查最近的 DNS 应答。
5. **分层与置信度**：自定义 > DPI > 缓存 > IP/端口 的分层，并给结果带上置信度（`NDPI_CONFIDENCE_*`）。
6. **IP 段兜底**：用 `asn_update.sh` 的方法按 ASN 生成 IP 段，作为低置信度兜底。
7. **时长要自己算**：nDPI 只负责分类，"时长"需要我们基于流的活跃时段和码率阈值，按设备自己算。
