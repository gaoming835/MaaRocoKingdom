<!-- markdownlint-disable MD033 MD041 -->
<p align="center">
  <img alt="LOGO" src="https://cdn.jsdelivr.net/gh/MaaAssistantArknights/design@main/logo/maa-logo_512x512.png" width="256" height="256" />
</p>

<div align="center">

# MaaPracticeBoilerplate

</div>

本仓库为 [MaaFramework](https://github.com/MaaXYZ/MaaFramework) 所提供的项目模板，开发者可基于此模板直接创建自己的 MaaXXX 项目。

> **MaaFramework** 是基于图像识别技术、运用 [MAA](https://github.com/MaaAssistantArknights/MaaAssistantArknights) 开发经验去芜存菁、完全重写的新一代自动化黑盒测试框架。
> 低代码的同时仍拥有高扩展性，旨在打造一款丰富、领先、且实用的开源库，助力开发者轻松编写出更好的黑盒测试程序，并推广普及。

## 即刻开始

请阅读[如何开发](./docs/zh_cn/develop/how_to_develop.md)

## 登录功能配置

“登录”任务通过 Maa OCR 判断当前画面是否存在“进入世界”，命中后使用 AutoFlower
远程控制 API 移动鼠标并点击。发布包已包含局域网使用的
`agent/autoflower.local.json`。使用时：

1. 确认配置中的 `base_url` 和六位 `pin` 与 AutoFlower 手机端一致。
2. 确认 `window_title` 与游戏窗口标题一致；点击坐标会按游戏客户区自动换算。
   识别分辨率与 `interface.json` 的短边 720 配置保持一致。

也可通过环境变量 `AUTOFLOWER_BASE_URL` 和 `AUTOFLOWER_PIN`
临时覆盖发布包中的地址与 PIN。

## 一键选择当前筛选精灵

1. 在游戏精灵盒子中设置筛选条件，并停留在显示
   `筛选中 当前页/总页数` 的结果页。
2. 在 Maa 中运行“一键选择”任务。
3. 任务会清空已有勾选、进入放生多选状态，从第一页开始逐格选择，
   并通过页码 OCR 自动翻到末页。

Maa 仅使用 FramePool 截图和 OCR。进入多选、选择精灵和翻页等所有点击
都固定通过 AutoFlower HID 执行，不会回退到 Win32 输入。任务只负责选择，
不会点击“放生”确认按钮。

## 自动瞄准投球

运行“自动瞄准投球”任务后会立即开始，按 `Esc` 急停退出。任务只依据精灵本体
的 YOLO 检测结果选择目标和闭环修正，
必须先提供固定 640×640 输入的 `sprite.onnx`；缺少模型时任务会直接拒绝
启动，不发送任何 AutoFlower HID 输入。
该任务启动时会明确将 Maa 资源切换到 DirectML GPU；如果 DirectML 无法启用，
任务直接拒绝启动，不会回退到 CPU 推理，也不会连接 AutoFlower。

当前单类别 `sprite` 的语义固定为**月牙雪熊**，不是所有野外精灵；其他精灵
不会被可靠识别。2026-07-31 按用户决定安装了新旧数据联合训练的候选模型，
作为**月牙雪熊原型**使用。运行阈值为 `0.40`，不设置检测框高度门槛；任何
达到该置信度的直接 YOLO 检测都可进入目标选择和投球流程。

当前运行不限制投球数量，会持续检测并投球；60 秒内没有合格目标时，完成当前
无漂移扫描周期并回正后自动暂停。游戏窗口启动后超过 60 秒仍不在前台时，任务
自动退出并释放输入。运行期间按 `Esc` 也会立即释放输入并退出任务。

每次目标连续锁定确认、选球前，任务会把当前 FramePool 画面保存为
`debug/auto_aim_throw/*_target_selected.bmp`：最终选中的目标框为绿色，同帧其他
合格 YOLO 检测框为黄色。该诊断截图沿用 `debug_sample_limit`，默认最多保留 200 张，
保存失败不会阻止投球。

本次安装是对原验收门槛的明确覆盖，并不表示模型已经通过全部跨设备指标。
阈值 `0.40` 的冻结测试 precision 为 96.75%、recall 为 76.94%，82 张困难
负样本中有 2 个误检；目前也尚未执行实机 HID 投球验收。

配置、数据准备、模型训练和安全行为详见
[自动瞄准投球说明](./docs/zh_cn/develop/auto_aim_throw.md)。

## 生态共建

MAA 正计划建设为一类项目，而非舟的单一软件。

若您的项目依赖于 MaaFramework，我们欢迎您将它命名为 MaaXXX, MXA, MAX 等等。当然，这是许可而不是限制，您也可以自由选择其他与 MAA 无关的名字，完全取决于您自己的想法！

同时，我们也非常欢迎您提出 PR，在 [社区项目列表](https://github.com/MaaXYZ/MaaFramework#%E7%A4%BE%E5%8C%BA%E9%A1%B9%E7%9B%AE) 中添加上您的项目！

## 常见问题

请阅读[常见问题](./docs/zh_cn/develop/faq.md)

## 鸣谢

本项目由 **[MaaFramework](https://github.com/MaaXYZ/MaaFramework)** 强力驱动！

感谢以下开发者对本项目作出的贡献（下面链接改成你自己的项目地址）:

[![Contributors](https://contrib.rocks/image?repo=MaaXYZ/MaaFramework&max=1000)](https://github.com/MaaXYZ/MaaFramework/graphs/contributors)
