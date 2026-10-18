# 想法：开源的 Mac 截图工具（对标 Shottr / QQ 截图）

> 状态：构思阶段。这是一个独立于「短视频时长统计」的新想法，记录在此备查。

## 缘起

Mac 上的截图工具普遍不够顺手：系统自带的太基础；[Shottr](https://shottr.cc) 很好用，但**闭源**，是专有商业软件（大部分功能免费，部分进阶功能付费 / 自愿赞助，拿不到源码、无法二次开发）；Snipaste 最接近 QQ 那种「即截即贴」但也不开源，Mac 版功能偏少；Flameshot 开源跨平台，但 Qt 实现，Mac 上体验不佳。

**目标**：做一个开源、原生、把「QQ 式快速截图 + 标注」这一个点做到顺手的 Mac 工具。不追求大而全（录屏、OCR、美化先不做），专注核心反而是差异化。

## 现有方案

| 工具 | 开源 | 平台 | 特点 / 短板 |
|---|---|---|---|
| Shottr | ❌ 闭源 | Mac | 好用（滚动截图、标注、取色、测像素），但不可定制 |
| Snipaste | ❌ 闭源 | 跨平台 | 最接近 QQ 即截即贴；Mac 版功能少 |
| Flameshot | ✅ GPL | 跨平台 | Qt 实现，Mac 体验差，偏传统标注 |
| [macshot](https://github.com/sw33tLie/macshot) | ✅ GPL-3.0 | Mac | 原生 Swift，功能全（追求录屏/OCR/美化） |
| [Snippr](https://github.com/fantasy9876/snippr) | ✅ | Mac | Swift，含标注、OCR、滚动截图 |
| [BetterShot](https://github.com/KartikLabhshetwar/better-shot) | ✅ | Mac | 定位为 CleanShot X 的开源替代 |

**空白**：真正「开源 + 原生 + 专注把 QQ 式截图标注做到极致」的还没有。现有开源项目多追求大而全，核心手感反而不够打磨。它们的源码可作为学习参照。

## MVP：QQ 截图的核心体验

1. 按全局快捷键 → 全屏变暗 + 十字准星；
2. 拖拽选区，**自动吸附到窗口 / 控件边界**（Shottr 和 QQ 好用的关键）；
3. 选区旁弹出工具条：矩形、箭头、画笔、文字、马赛克、序号；
4. 一键**复制到剪贴板** / 保存 / **贴图置顶**；
5. 滚轮微调、回车确认、Esc 取消。

把这五步做顺，就超过多数 Mac 免费工具。

## 技术路线（原生 Swift）

| 部分 | 方案 |
|---|---|
| 截屏 | ScreenCaptureKit（macOS 14+ 的 `SCScreenshotManager`，苹果现行官方方式） |
| 选区浮层 | 覆盖所有显示器的透明置顶窗口 + 变暗 + 十字准星 |
| 窗口吸附 | 读取屏上窗口边界，拖拽时吸附 |
| 标注 | CoreGraphics / SwiftUI Canvas，**存矢量绘制指令**而非直接改位图，便于撤销重绘 |
| 贴图 | 无边框置顶小窗口显示截图 |
| 权限 | 需申请「屏幕录制」权限 |

参考：Swift 论坛上 [Mio 的架构分享](https://forums.swift.org/t/mio-a-tiny-open-source-macos-screenshot-utility-built-with-swiftui-screencapturekit/87099)。

## 评估

| 维度 | 评价 |
|---|---|
| 范围可控 | ✅ MVP 一两周可出可用版本 |
| 独立完成 | ✅ 不依赖后端、不依赖硬件 |
| 真实痛点 | ✅ 自己就是用户 |
| 作品集 / star | ✅ 「开源的 QQ 式 Mac 截图」定位清晰 |
| 风险 | ⚠️ Swift / macOS 开发需新学；窗口吸附、多屏、权限有坑 |

## 里程碑

- [ ] v0：全局快捷键 → 区域截图 → 复制到剪贴板 + 保存文件
- [ ] v1：选区浮层优化 + 基础标注（矩形、箭头、文字、马赛克）
- [ ] v2：窗口 / 控件边界自动吸附
- [ ] v3：贴图置顶
- [ ] v4：取色、像素测量等 Shottr 常用增强（按需）

## 待确认

- 常用的核心功能优先级（以自己在 Shottr 里离不开的几个为准）。
- 目标 macOS 版本（决定能否用 ScreenCaptureKit 新接口）。
- 是否单独开仓库，还是作为本仓库的子目录。
