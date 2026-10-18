# AdGuard Home 学习笔记

版本 commit fb7774f。下文路径都相对仓库的 `internal/` 目录。

## 1. querylog（查询日志）

- **日志条目**（`querylog/entry.go:16-45`）：
  - `T`：时间
  - `QH`、`QT`、`QC`：查询的域名、类型、类别
  - `CID`：ClientID
  - `CP`：客户端协议，取值为空 / doh / dot / doq / dnscrypt
  - `IP`：客户端 IP
  - `Answer`、`OrigAnswer`：打包的 DNS 报文
  - `Result`：过滤结果，含 Reason、Rules、ServiceName
  - `Upstream`、`Elapsed`、`Cached`
- **存储**：JSON Lines 文件 `querylog.json`（`querylog/qlog.go:23`），用 O_APPEND 追加写（`querylog/querylogfile.go:80-100`）。
- **缓冲**：先写内存，条目数满 `MemSize` 后异步刷盘（`querylog/qlog.go:219-266`）。
- **轮转**：每小时检查一次，看文件首行的时间是否超过 `RotationIvl`，超过就改名为 `querylog.json.1`，只保留一个旧文件（`querylog/querylogfile.go:103-167`）。
- **HTTP API**：`GET /control/querylog`（`querylog/http.go:65`）
  - 参数（`querylog/http.go:443-488`）：
    - `older_than`：RFC3339Nano 游标
    - `limit`：默认 500
    - `offset`
    - `search`：按域名或客户端搜索
    - `response_status`
  - 分页：把返回的 `oldest` 作为下一页的 `older_than`。

返回示例（字段来自 `querylog/json.go:39-191`）：

```json
{"oldest":"2026-10-18T10:00:00.1Z","data":[{"time":"…","client":"192.168.1.5",
 "client_proto":"doh","client_id":"phone1","client_info":{"name":"小明手机"},
 "question":{"name":"v3-dy.douyinvod.com","type":"A","class":"IN"},
 "answer":[{"type":"A","value":"1.2.3.4","ttl":60}],
 "status":"NOERROR","reason":"NotFilteredNotFound","service_name":"tiktok",
 "cached":false,"elapsed_ms":"1.2"}]}
```

## 2. stats（统计）

- **时间粒度**：按小时分桶，桶 id 是 Unix 时间换算成的小时数（`stats/unit.go:185-187`）。
- **桶内容**：domains、blockedDomains、clients 三个 `map[string]uint64` 计数器（`stats/unit.go:95-129`）。
- **存储**：用 GOB 编码写进 bbolt，定期刷盘（`stats/unit.go:155-180`）。每类只保留 Top 100（`stats/unit.go:20-27`）。
- **局限**：**没有"客户端 × 域名"的交叉统计**，没法直接拿来算某台设备在某个 App 上的时长。

## 3. 客户端识别

- **来源优先级**：WHOIS < ARP < rDNS < DHCP < hosts < 持久客户端（`client/client.go:47-55`）。ARP 每 10 分钟刷新一次（`home/clients.go:309`）。
- **持久客户端**：可以用 IP、子网、MAC、ClientID 来标识（`client/persistent.go:66-82`）。
- **查找顺序**：ClientID → IP → CIDR → MAC。用 IP 查 MAC 时借助 DHCP 租约（`client/storage.go:533-580`）。
- **DoT/DoQ 的 ClientID**：取 TLS SNI 里 `<id>.dns.example.com` 的最左一级（`dnsforward/clientid.go:20-50`）。
- **DoH 的 ClientID**：取路径 `/dns-query/{ClientID}`（`dnsforward/clientid.go:63-83`）。

所以每台手机配一个独立的 DoH 地址，识别起来比按 IP 稳定得多。

## 4. 按客户端拦截与时间表

- **按客户端覆盖**：客户端打开 `UseOwnBlockedServices` 后，用它自己的 BlockedServices 替换全局设置（`client/storage.go:774-810`）。
- **时间表**：`BlockedServices{Schedule *schedule.Weekly; IDs}`（`filtering/blocked.go:65-72`）。Weekly 是每天一个开始–结束区间，带时区（`schedule/schedule.go:17-23,185-202`）。
- **注意语义**：`Schedule.Contains(now)` 为真时**不拦截**，也就是说区间表示的是"放行"时段（`filtering/blocked.go:134`）。

**服务定义**在 `filtering/servicelist.go`。

- **tiktok**（`:2914-2951`），原文：

```
||amemv.com^ ||bdurl.com^ ||bytecdn.cn^ ||bytedance.map.fastly.net^ ||bytedapm.com^
||bytegoofy.com^ ||byteimg.com^ ||byteoversea.com^ ||bytescm.com^ ||douyin.com^
||douyincdn.com^ ||douyinliving.com^ ||douyinpic.com^ ||douyinstatic.com^ ||douyinvod.com^
||huoshan.com^ ||huoshanstatic.com^ ||huoshanzhibo.com^ ||muscdn.com^ ||musical.ly^
||p16-tiktok-*.ibyteimg.com^ ||p16-tiktokcdn-com.akamaized.net^ ||pstatp.com^ ||snssdk.com^
||tiktok.com^ ||tiktokcdn-us.com^ ||tiktokcdn.com^ ||tiktokrow-cdn.com^ ||tiktokv.com^
||ttlivecdn.com^ ||v*.tiktokcdn-eu.com^ ||zijieapi.com^
```

  抖音的域名就在这里面。`bdurl.com` 可能和字节的 HTTPDNS 有关，需要实测确认。

- **bilibili**（`:422-472`），摘录：

```
||acg.tv^ ||acgvideo.com^ ||b23.tv^ ||biliapi.com^ ||biliapi.net^ ||bilibili.com^ ||bilibili.tv^
||bilicdn1.com^ … ||bilicdn5.com^ ||biliimg.com^ ||bilivideo.cn^ ||bilivideo.com^ ||bilivideo.net^
||hdslb.com^ ||hdslb.org^ ||upos-hz-mirrorakam.akamaized.net^ …
```

- **其他**：有 xiaohongshu（`:3205`）、iqiyi（`:1886`）、youtube（`:3217`）。**没有快手**，需要自己补，比如 `kuaishou.com`、`gifshow.com`、`kwai.net`、`yximgs.com`、`kwaicdn.com`。

## 5. TTL 控制

- `cache_ttl_min` / `cache_ttl_max` 会覆盖上游返回的 TTL（`dnsforward/config.go:113-119`），要开启缓存才生效。
- `blocked_response_ttl` 只作用于被拦截的应答（`filtering/filtering.go:181-183`）；dnsrewrite 规则也可以自定 TTL。
- 把 TTL 压到 30–60 秒，就能用"查询次数 × TTL"估算使用时长。会造成低估的因素：系统或 App 自带缓存、HTTP/2 长连接复用、HTTPDNS 或 DoH 直连。

## 6. 可借鉴

- **客户端识别链路**：ClientID（DoH 路径 / DoT SNI）→ IP → CIDR → MAC。
- **数据源**：拉取 `/control/querylog`，用 `older_than` 做增量游标；也可以离线读 `querylog.json*`。直接用 `service_name` 或域名后缀归类。
- **统计方式**：stats 缺"客户端 × App"这个维度，要自己建表。可以沿用它的小时桶和 bbolt 思路，时长则用会话化来算。
- **管控**：复用按客户端 BlockedServices 加 Weekly 时间表来实现"超时就拦截"。
- **规则表**：`servicelist.go` 里的抖音和 B站域名表可以直接作为初始规则。
