# netifyd 与 nlbwmon 学习笔记

- netifyd 5.2.8（commit c4907e3），路径相对 netify-agent 仓库根目录。
- nlbwmon commit 29236be，路径相对 nlbwmon 仓库根目录。

## 一、netifyd

### 1. 抓包方式（`capture_type`，`src/nd-config.cpp:1085-1112`）

- **pcap**：混杂模式，读超时 500ms，snaplen 65535，用 `pcap_dispatch` 批量取包（`nd-capture-pcap.cpp:107,214-220`）。
- **TPACKET_V3**：`AF_PACKET` 套接字加 mmap 环形缓冲区（4MB × 64 块），支持 `PACKET_FANOUT_HASH/LB/CPU` 把流量分给多个线程（`nd-capture-tpv3.cpp:184-300`）。
- **NFQUEUE**：基于 libmnl，参数 `COPY_PACKET`、`F_FAIL_OPEN`、`F_CONNTRACK`、`F_GSO`。每个包都要回 `NF_ACCEPT` 判决，回判决时可以顺便打 mark（`nd-capture-nfq.cpp:132-146,384-389`）。

### 2. 流表与超时

- **流表**：`ndFlowMap` 分 128 个桶，每个桶一把锁，桶内是 `unordered_map<digest, shared_ptr<ndFlow>>`，再配短、长两条 LRU 链表（`nd-flow-map.cpp:69-86`）。
  - TCP 流放长链，其他放短链；TCP 收到 FIN+ACK 后移到短链（`nd-capture.cpp:1315-1320`）。
- **流的 key**：`xxh64(接口, 协议, vlan, lower/upper 地址与端口)`（`nd-flow.cpp:111-151`）。lower/upper 是两端按地址大小排序后的结果，所以正反两个方向会落到同一条流上。
- **超时**：UDP 空闲 30 秒、TCP 空闲 300 秒（`netifyd.hpp:68-71`）。
- **过期处理**：从 LRU 链表头开始扫，遇到第一个没超时的就停（`nd-instance.cpp:3100-3179`）。过期分三步：标记、发出 `FLOW_EXPIRE` 事件、删除（`2882-2901`）。

### 3. 调用 nDPI

- 每个 DPI 线程一个 nDPI 实例，开启了 `dns.process_response`（`nd-ndpi.cpp:201-257`）。
- 每条流最多送 32 个包检测（`nd-capture.cpp:1359-1381`）；检测失败时调用 `ndpi_detection_giveup`（`nd-detection.cpp:1225-1246`）。
- 从 nDPI 取出 `host_server_name`（SNI 或 HTTP Host）去匹配 App（`nd-detection.cpp:388-391,680-690`）。
- **App 判定优先级**：SNI 域名 → DNS 缓存里的域名 → IP 段 → 表达式规则（`nd-detection.cpp:1253-1319`）。
- **流哈希缓存 FHC**：同一个五元组再次出现时，直接复用上次的结果（`nd-detection.cpp:311-357`）。

### 4. 签名文件格式（`deploy/netify-apps.conf`，解析在 `nd-apps.cpp:64-145`）

```
app:10302:netify.tiktok
dom:10302:p16-tiktok-sign-va-h2.ibyteimg.com
net:10119:2a12:e9c0::/29
xfm:<regex>:<replace>
nsd:<aid>:<pid>:<expr>
```

- **域名匹配**：先做 `xfm` 正则改写，再逐级去掉最左边一段做查找，`a.b.douyinvod.com` → `b.douyinvod.com` → `douyinvod.com`（`nd-apps.cpp:351-383`）。
- **IP 段**：用基数树匹配（`nd-apps.cpp:385` 起）。
- 随包附带的签名只有 TikTok，没有抖音、快手、B站。完整签名库需要商业授权（`nd-apps.cpp:52`）。

### 5. DNS 反查缓存 DHC（重点）

- **写入**：抓包线程解析 53 端口上的 DNS 应答，对每条 A/AAAA 记录执行 `dhc->Insert(ip, qname)`（`nd-capture.cpp:1331-1353,1394-1582`）。
  - 记录的是**原始查询名**，而不是 CNAME 链末端的名字，正好对付 CDN 的 CNAME。
  - 只接受 NOERROR 的应答。
- **实现**：`ndLRUCache<xxh64(ip), hostname>`，默认 1000 条，**忽略 TTL**（`nd-dhc.cpp:38-96`，`netifyd.hpp:77`）。
- **使用**：新流开始检测时，用对端 IP 查 DHC，结果写入 `dns_host_name`（`nd-detection.cpp:608-619`）。
- **持久化**：只在进程退出时写一次 CSV 文件 `dns-cache.csv`（`nd-dhc.cpp:180-221`）。

### 6. 输出

- **插件事件**：`FLOW_NEW`、`DPI_COMPLETE`、`FLOW_EXPIRE`、`FLOW_MAP` 等（`nd-plugin.hpp:139-156`）。proc 插件把数据序列化成 JSON 或 msgpack，sink 插件再发到 HTTP 或本地 socket。
- **本地 socket**：`/var/run/netifyd/netifyd.sock`，每行一个 JSON：

```json
{"type":"flow","flow":{"first_seen_at":1700000000000,"last_seen_at":1700000012000,
 "local_mac":"…","local_ip":"192.168.1.23","other_ip":"…","other_port":443,
 "detected_protocol_name":"TLS","detected_application_name":"netify.tiktok",
 "host_server_name":"v26.douyinvod.com","dns_host_name":"…",
 "local_bytes":…,"other_bytes":…,"total_bytes":…,"total_packets":…}}
```

### 7. 每条流的统计字段（`nd-flow.hpp:43-140`）

- **周期增量**：`lower/upper_bytes`、`lower/upper_packets`，每个 update_interval（默认 15 秒）结束时清零（`nd-instance.cpp:3051-3057`）。
- **累计值**：`total_*`。
- **时间戳**：`ts_first_seen`、`ts_last_seen`，单位毫秒。
- **用 conntrack 计数时**：读 conntrack 的累计值，减去上次读到的值得到增量（`nd-netlink-ct.cpp:540-632`），这样不会把别的程序要用的计数清零。

## 二、nlbwmon

- **获取计数**：
  - 订阅 conntrack 的 `NEW` 和 `DESTROY` 事件（`nfnetlink.c:386`）。DESTROY 消息带着连接的最终字节数，这样连接结束时的流量不会丢。
  - 每 30 秒发一次 `IPCTNL_MSG_CT_GET_CTRZERO`，**读完即清零**，读到的就是增量（`nfnetlink.c:404-516`）。
  - 前提：`nf_conntrack_acct=1`。netlink 接收缓冲区默认 512KB，不够时要调大 `rmem_max`（`nfnetlink.c:350-372`）。
- **方向判断**：用本地子网判断哪端是本机设备，统一成 in/out；本地到本地、远端到远端的连接直接丢弃（`nfnetlink.c:241-266`）。
- **聚合**：
  - key 是 `{family, proto, dst_port, src_mac, src_addr}`，值是连接数和 in/out 的包数、字节数（`database.h:56-74`）。
  - 用 AVL 树索引，用 memcmp 比较 key，同 key 的记录累加（`database.c:44-48,246-264`）。
  - 端口到协议名的映射来自 `protocols.txt`。
- **找 MAC**：
  - 先 dump `RTM_GETNEIGH` 邻居表；查不到再回落到"IP → 网卡 MAC"，结果缓存起来（`neigh.c:135-358`）。
  - 新连接查不到 MAC 时，延迟 500ms 再查一次（`nfnetlink.c:107-131`）。
- **持久化**：
  - 二进制格式：头部是 magic `nlbm`、记录数、时间戳、周期参数，后面跟定长记录，全部大端序；可以 gzip（`database.c:297-375`）。
  - 文件名 `<YYYYMMDD>.db[.gz]`，默认保留 10 代（`database.c:386,563-606`）。
  - 跨周期时先存档旧库，再立即 dump 一次，把还在进行的连接带到新周期（`database.c:608-638`）。
- **闪存保护**：
  - 平时只写 tmpfs 上的 `/tmp/0.db`；每 86400 秒才写一次闪存；收到 SIGTERM 时也写一次（`nlbwmon.c:69,109-135,165`）。
  - 启动时优先加载 `/tmp/0.db`；如果存储设备挂载得比进程晚，先合并再保存（`nlbwmon.c:84-107`）。
  - 可以限制记录数，满了就像环形缓冲区一样覆盖最旧的记录（`database.c:209-225`）。

## 三、对我们的借鉴

**推荐组合：conntrack 计数加 DNS 反查，完全不在数据路径上抓包。**

1. **DNS 侧**：解析 dnsmasq 的 `log-queries` 日志，或者只抓 53 端口的应答，按 DHC 的思路建 `(client_ip, server_ip) → qname` 映射。比 netifyd 多做两件事：key 加上客户端维度，记录 TTL 和插入时间，过期淘汰。
2. **流量侧**：照搬 nlbwmon 的 NEW/DESTROY 订阅加定期 CTRZERO。如果路由器上还有别的程序在读 conntrack 计数，就改用 netifyd 的"读累计值、自己算差值"。conntrack NEW 时查一次 DNS 缓存，把 App 绑定到 ct id 上，后续不再查。
3. **App 匹配**：用 netifyd 的 `dom:` 逐级后缀匹配，必要时配合 `xfm:` 正则改写。
4. **活跃分钟**：在每个时间桶里，`(MAC, App)` 的下行字节超过阈值才算这一分钟在看。分钟桶的聚合方式照搬 nlbwmon 的定长 record 加 AVL 树。
5. **MAC 与主机名**：用 neigh 表加缓存查 MAC，用 DHCP 租约查主机名。

**注意事项**：

- HTTPDNS 和 DoH 会让 DNS 反查失效，要用 IP 段兜底，必要时抓 TLS ClientHello 的 SNI。
- QUIC/UDP 的 conntrack 超时较短；IPv6 下一个 MAC 会对应多个地址。
- 开启 flow offload 后 conntrack 计数可能不更新，要关掉，或者确认驱动会回填计数。
- 必须开启 `nf_conntrack_acct=1`，并调大 `rmem_max`，防止 ENOBUFS 丢事件。
- 数据先放 `/tmp`，按小时或按天落盘。DNS 缓存只放内存就行。
