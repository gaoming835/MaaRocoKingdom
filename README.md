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
远程控制 API 移动鼠标并点击。首次使用前：

1. 将 `agent/autoflower.local.example.json` 复制为
   `agent/autoflower.local.json`。
2. 填入 AutoFlower 手机端显示的 `base_url` 和六位 `pin`。
3. 确认 `window_title` 与游戏窗口标题一致；点击坐标会按游戏客户区自动换算。
   识别分辨率与 `interface.json` 的短边 720 配置保持一致。

本地配置已加入 `.gitignore`，不会提交 PIN。也可通过环境变量
`AUTOFLOWER_BASE_URL` 和 `AUTOFLOWER_PIN` 临时覆盖地址与 PIN。

## 一键选择当前筛选精灵

1. 在游戏精灵盒子中设置筛选条件，并停留在显示
   `筛选中 当前页/总页数` 的结果页。
2. 在 Maa 中运行“一键选择”任务。
3. 任务会清空已有勾选、进入放生多选状态，从第一页开始逐格选择，
   并通过页码 OCR 自动翻到末页。

Maa 仅使用 FramePool 截图和 OCR。进入多选、选择精灵和翻页等所有点击
都固定通过 AutoFlower HID 执行，不会回退到 Win32 输入。任务只负责选择，
不会点击“放生”确认按钮。

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
