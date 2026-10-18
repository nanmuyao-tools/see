# 现有方案调研：不重复造轮子

调研时间：2026-10。各产品的功能和价格以官方当前版本为准。

## 1. 系统和 App 自带

| | 功能 |
|---|---|
| iOS 屏幕使用时间 | 精确统计每个 App 的前台时长，可设每日限额 |
| Android 数字健康 / 华为"健康使用手机" / 小米"屏幕时间管理"等 | 同上 |
| 抖音、快手 App 内的时间管理 | 使用时长提醒、休息提醒 |
| 华为、小米路由器 App 的家长控制 | 按设备设置断网时段、上网时长（只能控制整台设备，统计不到 App） |

共同问题：**太容易突破**，点一下"再用 15 分钟"就过去了（[少数派](https://sspai.com/post/89727)）。

## 2. 第三方"摩擦"类 App

| App | 平台 | 特点 |
|---|---|---|
| one sec | iOS / Android | 打开前强制深呼吸、等待，让人反思（[少数派](https://sspai.com/post/94839)） |
| ScreenZen | iOS / Android | 免费，打开前先暂停；有耐心的话可以等过去（[评测](https://habitdoom.com/blog/opal-vs-screenzen-jomo-one-sec)） |
| Opal | iOS / Android | 定时屏蔽，延迟逐次递增；硬模式要付费 |

## 3. 开源 Android 项目

| 项目 | 特点 |
|---|---|
| [Mindful](https://github.com/akaMrNagar/Mindful) | 按 App 限时、屏蔽 Reels/Shorts、睡眠模式；"无敌模式"锁定设置，只在每天固定的窗口期内能改；用无障碍服务和本地 VPN 实现 |
| [DigiPaws / Curbox](https://f-droid.org/de/packages/nethical.digipaws) | GPL-3.0，使用统计和 App 屏蔽，能只屏蔽 App 里的短视频部分；alpha 阶段 |
| [Shorts Blocker](https://github.com/atick-faisal/Shorts-Blocker) | 检测到 Shorts/Reels 就自动返回 |
| [PCAPdroid](https://github.com/emanuele-f/PCAPdroid) | 不用 root，通过本地 VPN 抓包，每条连接都标明属于哪个 App 和对应的域名，能导出 pcap，适合做验证实验 |

## 4. 网络层产品

- [Firewalla](https://help.firewalla.com/hc/en-us/articles/26036957415699)：商业防火墙盒子。能按设备或按人统计 TikTok、YouTube 等 App 的使用时长，也能设每日额度。
  - 有用户反馈它统计的时长比手机屏幕使用时间多，而且按整分钟取整。这说明网络层统计本质上是估算。
  - 不支持国内 App。

**空白**：面向国内短视频 App、在网络层统计时长的开源方案。

## 5. 低成本方案阶梯

| 方案 | 成本 | 折腾程度 | 能做到 |
|---|---|---|---|
| 系统屏幕使用时间 + 路由器家长控制 + one sec / ScreenZen | 0 | 很低 | 统计时长、晚上断网、打开前停顿 |
| 手机私人 DNS + NextDNS 免费版 | 0 | 低 | 按域名记录和屏蔽，4G 也能管 |
| PCAPdroid（Android） | 0 | 中 | 按 App 看流量，做实验 |
| 电脑开热点抓包 | 0 | 中 | 网络层实验 |
| 旧 Android 手机 root 后做中转热点 | 0 | 高 | 需要支持边连 Wi-Fi 边开热点；长期插电有电池鼓包风险 |
| 二手玩客云 / 斐讯 N1 做旁路网关 | 30–100 元 | 中 | 长期在网络层统计自己的手机 |
| 能刷 OpenWrt 的路由器做主路由 | 约 150 元 | 中 | 管全家设备 |

注：树莓派并不便宜（几百元），这里不如二手刷机小盒子划算。
