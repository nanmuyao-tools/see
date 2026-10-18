# OpenAppFilter（OAF）学习笔记

版本 6.0.1（commit 7fcf9e8），见 `oaf/src/fwx.h:8`。下文路径都相对仓库根目录。

## 1. 架构与数据流

| 包 | 职责 |
|---|---|
| `kmod-oaf`（`oaf/src`） | 内核模块：DPI 识别、拦截、按设备统计流量。只依赖 `kmod-nf-conntrack` 和 `kmod-nf-reject`（`oaf/Makefile:15`） |
| `oafd`（`open-app-filter/src`） | 守护进程，基于 uloop/ubus/sqlite/curl：下发特征库、统计时长、落库，并提供 ubus 对象 `fwx` |
| `luci-app-oaf` | Lua 界面，只调用 `ubus fwx common`（`luci-app-oaf/luasrc/controller/oaf_app_record.lua:42`） |

内核与用户态之间的通道：

| 通道 | 方向 | 内容 | 位置 |
|---|---|---|---|
| 自定义 netlink（协议号 29） | 双向 | 用户态下发特征（`FWX_NL_MSG_ADD_FEATURE`）；内核每 60 秒上报访问记录 JSON | `fwx.h:15,93`；`fwx_main.c:2704-2809`；`open-app-filter/src/fwx_netlink.c:326` |
| `/proc/sys/fwx/*` | 用户态→内核 | `lan_ifname`、`work_mode`、`record_enable` 等开关 | `oaf/src/fwx_log.c:53-188` |
| `/dev/fwx` 字符设备 | 用户态→内核 | 用 JSON 写入规则（应用过滤、MAC 过滤、白名单） | `fwx_config.c:163` |
| `/proc/net/af_client`、`af_visit`、`af_active_app`、`af_conn` | 内核→用户态 | 设备表、当前访问记录 | `fwx_client_fs.c:187,274`；`fwx_main.c:3338` |

## 2. 识别原理

### netfilter 钩子

不用 iptables/nft 规则，直接调用 `nf_register_net_hooks`：

- DPI 挂在 `NF_INET_PRE_ROUTING`，优先级 `NF_IP_PRI_CONNTRACK+1`，pf 为 `NFPROTO_INET`（`fwx_main.c:2599-2613`）。
- 按设备统计流量挂在 `NF_INET_FORWARD`，分别在 FIRST+1 和 LAST-1（`fwx_client.c:778-790`）。
- 两种工作模式：网关模式依赖 conntrack（`fwx_main.c:2364`）；旁路模式自己维护一张连接表（`fwx_main.c:2193`、`fwx_conntrack.c`）。

所以它 **fw3 和 fw4（nftables）都能用**。

### 解析器（入口 `dpi_main`，`fwx_main.c:2055`）

- **HTTP**：按 `\r\n` 切行，取 `GET`/`POST` 后面的 URL 和 `Host:` 头（`fwx_main.c:1413-1469`）。
- **TLS SNI**：不做规范解析，是启发式扫描（`fwx_main.c:1352-1411`）：
  - 先检查 `p[0]==0x16 && p[1]==0x03 && p[5]==0x01`；
  - 然后在整个包里找 `00 00 00 xx`，长度取 +7 偏移，主机名取 +9 偏移；
  - ClientHello 被切成多个包时，在 ct 上置 `client_hello` 标志，下一个包继续扫（`fwx_main.c:1381,2464,2490`）；
  - 限制：SNI 超过 `MAX_HOST_LEN 40` 字节就直接丢弃（`fwx.h:11`）。
- **DNS**：解析查询的域名，最多 16 条（`fwx_main.c:1793,1848`）。DNS 命中时置 `match_by_dns`。
- **QUIC**：只看长头和版本号，**不解析 SNI**。所有 QUIC 流一律记为 app_id=10 "QUIC"，并被标为 ignore（`fwx_main.c:1695-1748,2502`）。另有 `filter_quic` 选项，用来封掉 QUIC、逼客户端回落到 TCP（`fwx_app_filter.c:359`）。

### 匹配顺序（`fwx_main.c:1733`）

1. 用 Aho-Corasick 做 host 子串匹配，先匹配 SNI/Host，再匹配 DNS 域名（`593-672`）；
2. 正则 host，写法 `^…*…$`（`353`）；
3. 端口 + 按字节位置的 dict + 搜索串（`1561`）。

### 特征库语法（解析代码在 `fwx_main.c:931-1129`）

```
#id name:[proto;sport;dport;host url;request;dict;search str;ignore]
3101~YouTube:[tcp;;;youtube;;,tcp;;;googlevideo.com;;]
7103~WindowsUpdate:[tcp;;80;update.microsoft.com;;]
```

- 多条特征用逗号分隔。
- dport 支持 `|`、`-`、`!` 写法（`862-929`）。
- dict 写成 `pos:hex|pos:hex`，pos 为负数表示从包尾倒数（`774,1574`）。
- `appid / 1000` 就是分类（`fwx.h:68`）。

**国内短视频规则：仓库里没有。** grep `douyin|kuaishou|bilibili|xhs|channels.weixin` 没有任何结果。仓库自带的 `files/feature.bin` 是加密的特征库，只有约 50 条海外应用，比如 Facebook、YouTube、Netflix。国内规则要凭 token 从官方服务器在线拉取（`fwx_feature_online.c:26,277`）。

### 每条流检测多少包、怎么标记

- 网关模式下，conntrack 双向包数之和超过 `MAX_DPI_PKT_NUM 256` 就停止检测（`fwx_main.c:2473-2476`）。
- 识别成功后把 appid 写入 `ct->mark`，之后的包直接放行（`2459-2461`）。
- mark 的位布局（`fwx_main.c:72-114`）：

```c
#define FWX_CT_APPID_MASK       0x0000FFFFU   // 低 16 位 appid
#define FWX_CT_DNS_MATCH_BIT    0x10000000U
#define FWX_CT_IGNORE_BIT       0x20000000U
#define FWX_CT_CLIENT_HELLO_BIT 0x40000000U
#define FWX_CT_DROP_BIT         0x80000000U
```

注意它占用了整个 mark 字段，会和 mwan3、SQM 这类同样用 mark 的组件冲突。

## 3. 设备识别

- **内核侧**：在 FORWARD 钩子里，对从 `lan_ifname` 进来的包取 `eth_hdr(skb)->h_source` 建设备表，同时更新 IPv4/IPv6 地址和上行字节数（`fwx_client.c:622-692`）。下行字节按目的 IP 反查设备来记（`753-770`）。设备 180 秒没有流量就过期（`fwx_client.h:23`）。
- **用户态**：
  - 读 `/proc/net/af_client`（`fwx_user.c:2302`）；
  - 主机名取自 `/tmp/dhcp.leases`（`1874`）；
  - 无线状态用 `ubus call iwinfo assoclist`（`2178`）；
  - 设备离线 3 天后清除（`2450`）。

## 4. 访问记录与时长统计（有，但很粗）

**内核侧**：

- 网关模式下，已识别流的每个包都会调用 `af_update_client_app_info`，执行 `total_num++` 并刷新 `latest_time`（`fwx_main.c:2440,1966-2000`）。
- 每台设备有一个 2 秒定时器，每 30 次（即 60 秒）上报一次（`fwx_client.c:835-843`）。
- 上报时过滤掉 `is_http && conn_count<=3` 的应用（`510`），剩下的按 total_num 排序，**只报前 3 个**（`g_max_app_report_count`，`fwx_client.c:43,521`）。
- 上报格式：

  ```
  {"mac","ip","app_num","up_flow","down_flow","active","visit_info":[{"appid","latest_action"}]}
  ```

**用户态**（`open-app-filter/src/fwx_netlink.c:210-285`）：

- 上报里出现的 appid 一律加 `REPORT_INTERVAL_SECS=60` 秒，同时累加到按小时、按天的统计。
- 维护一个 `online_visit` 列表。每 10 秒检查一次，间隔超过 `ONLINE_VISIT_TIMEOUT_SEC 300` 的会话关闭，时长取 `latest - first`。小于 `app_valid_time` 的会话丢弃，其余写入 sqlite（`fwx_user.c:2514-2544`）。
- 表结构：`app_visit_record(mac, record_date, appid, start_time, end_time, duration, action)`，存在默认路径 `/tmp/oaf/client.db`（`fwx_user.c:851-905`）。

**精度问题**：

- 一分钟内只要有命中就记 60 秒，哪怕一次 DNS 查询也算（DNS 命中时 `is_http=0`，绕过了上面的过滤）；
- 每分钟只上报前 3 个 App；
- 后台保活流量会被误计入；
- 只出现一次上报的会话时长为 0，会被丢弃。

## 5. 性能与限制

- 每条流只看前 256 个包，识别结果缓存在 ct mark 里；host 用 AC 自动机匹配；非线性 skb 小于 3000 字节时才拷贝（`fwx_main.c:124,2478`）。
- **和 flow offload 冲突**：开启加速后，后续包不再经过钩子，统计会失真。项目提供 `files/hnat.sh`，用来关掉 mt798x hnat、QCA ECM、turboacc 和 firewall 的 `flow_offloading(_hw)`。
- 设备查找是在 64 个哈希桶里线性遍历。
- `add_app_feature` 里的 `host_url[32]` 用 `strncpy` 拷贝，没有限制长度（`fwx_main.c:937,980`）。
- 许可：GPL-2.0，企业使用需要作者授权。

## 6. 可借鉴 / 要改进

**可借鉴**：

- PRE_ROUTING 加 conntrack 的流识别框架；
- ct mark 存 appid，后续包快速放行；
- 前 N 个包之后停止 DPI；
- 跨包 ClientHello 的续扫标志；
- 用 AC 自动机匹配 host；
- 用 FORWARD 钩子按 MAC 统计流量；
- netlink 定时上报 JSON、sqlite 落库的分层设计；
- `hnat.sh` 关闭加速的写法；
- 简洁的特征语法。

**要改进**：

1. 自己维护国内短视频规则。
2. 解析 QUIC Initial 拿 SNI，参考 nDPI。
3. 时长改成按下行字节或包速率判定"在看"（它的 `pkt_down_rate>20` 活跃判定可以参考，`fwx_client.c:594`），并用 30–60 秒的空闲超时切分会话。
4. 放宽 SNI 和规则 host 的长度限制。
5. 改用 connmark 掩码或 ct label，避免占满 mark。
6. 数据要持久化，不能只放在 `/tmp`。
