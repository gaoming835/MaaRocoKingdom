# 自动瞄准投球

## 功能边界

“自动瞄准投球”只读取 Maa FramePool 截图，键盘和鼠标操作全部通过
AutoFlower Bluetooth HID 发送。它不会注入游戏、读取游戏内存或修改客户端。

精灵发现、目标选择、闭环修正和投球条件只使用精灵本体的 YOLO 检测框。
模型是必需运行资源；缺少 `sprite.onnx` 时任务会在读取 AutoFlower 配置之前
拒绝启动，不会聚焦窗口、扫描或发送 HID 输入。游戏中的其他交互 UI 不参与
识别、加权、稳定帧计算或调试判断。

任务还会在读取 AutoFlower 配置前强制调用 MaaFramework 的 DirectML GPU
推理后端。DirectML 设置失败时任务拒绝启动，不允许静默回退到 CPU。

这里的单类别 `sprite` 当前只表示**月牙雪熊**，而不是所有野外精灵。其他
精灵必须作为负样本，模型不会可靠识别它们；如果以后更换捕捉目标，必须重新
标注、训练并验收模型，不能沿用本模型的类别语义。

## 使用方法

1. 将训练并验收后的模型放到
   `assets/resource/model/detect/sprite.onnx`；发布包中对应路径为
   `resource/model/detect/sprite.onnx`。
2. 按照 README 配置 `agent/autoflower.local.json`，并连接 AutoFlower
   Bluetooth HID。
3. 游戏保持 1280×720 的识别比例，将游戏窗口置于前台后启动 Maa 的“自动瞄准
   投球”任务；任务启动即开始运行。
4. 任务不限制投球数量，会持续检测并投球。游戏窗口启动后超过 60 秒仍不在前台
   时任务自动退出；运行中按 `Esc` 立即释放输入并结束任务。

任务会先截图并运行 YOLO。没有检测结果时，在未按左键的状态下水平往返
扫描，完整一轮的净位移为零。检测到一个或多个精灵时，选择瞄准点离画面中心
最近者，必要时按一次 `E` 选球，再按住左键；每次小幅移动镜头后重新检测，
连续两帧进入死区才松开投球。由于投球轨迹是抛物线，当前默认把检测框距顶部
25% 的位置作为瞄准点（比几何中心更高）。目标在按住左键后连续多帧没有被 YOLO
检测到才释放并暂停，单个 FramePool/推理空帧会按配置短暂重试。
完成投球后会保存带 `throw_completed` 后缀的调试截图。默认 60 秒内没有
合格目标时，会先完成当前扫描周期使净位移回到零，再自动暂停；Maa 停止、
`Esc` 急停和异常清理不会等待扫描回正。

每次目标连续锁定确认、按 `E` 选球前，会把锁定确认帧保存为
`debug/auto_aim_throw/*_target_selected.bmp`。最终选中框绘制为绿色，同帧其他
合格 YOLO 框绘制为黄色；截图沿用 `debug_sample_limit`，默认最多保留 200 张。
这是尽力而为的诊断保存，失败不会改变选球、瞄准或投球流程。

当前配置不设置检测框高度门槛。只要直接 YOLO 检测达到 `detector_threshold`，
无论目标远近都会进入目标选择；瞄准框、交互提示等 UI 不参与判断。瞄准期间
目标丢失时立即释放左键并暂停。

游戏没有“取消瞄准但不投球”的操作，因此目标突然消失或 Esc 急停时，释放左键
可能额外消耗一球。释放输入优先于保留球，避免鼠标保持状态残留。

失败时的画面会保存在 `debug/auto_aim_throw`，默认最多保留 200 张 BMP。

## 参数校准

运行参数位于 `agent/auto_aim.json`：

| 参数 | 含义 |
| --- | --- |
| `aim_gain_x`, `aim_gain_y` | 目标像素误差换算成 HID 相对移动的比例 |
| `aim_deadzone_x`, `aim_deadzone_y` | 允许投球的准星误差 |
| `aim_anchor_y` | 检测框内的垂直瞄准位置；0.25 表示距顶部 25%，用于抛物线提前抬高瞄准点 |
| `aim_max_step` | 单次闭环修正的最大 HID 位移 |
| `scan_step` | 无目标时每次水平扫描的 HID 位移 |
| `aim_settle_seconds` | 镜头移动或按住左键后的等待时间 |
| `aim_target_miss_tolerance_frames` | 按住左键后允许连续空检的帧数；超过后安全释放并暂停 |
| `throw_cooldown_seconds` | 投球后的最短冷却时间 |
| `activation_timeout_seconds` | 无合格目标时自动暂停的秒数；`0` 关闭超时 |
| `foreground_grace_seconds` | 启动后允许游戏窗口不在前台的宽限秒数，默认 60 |
| `ball_roi` | 右下角当前球图标的 1280×720 坐标 |
| `ball_active_min_pixels` | 判断球已选中的紫色像素下限 |
| `detector_threshold` | YOLO 目标置信度阈值 |
| `detector_min_height_pixels` | 可进入瞄准的最小检测框高度；`0` 关闭过滤 |
| `debug_samples` | 是否保存诊断截图；关闭时也不会保存目标选择截图 |
| `debug_sample_limit` | 诊断 BMP 总保留上限，默认 200 张 |

若准星经常越过目标，应降低对应方向的 `aim_gain_*` 或 `aim_max_step`；若移动
过慢，则小幅提高增益。游戏内鼠标灵敏度改变后需要重新校准。

## 准备训练数据

抽帧工具的依赖与 Maa 运行时隔离：

```powershell
python -m pip install -r tools/sprite_dataset.requirements.txt
```

先列出本次模型要捕捉的精灵种类。每种目标至少准备 3 段独立录像、约 200 个
正样本框，其中至少 40 个远距离框，并覆盖两种以上地图及不同天气或昼夜。
录像需要包含正面、侧面、背面、跑动、遮挡、同屏多目标和投球特效前后画面。
按 2 FPS 抽帧，并以低分辨率图像差异删除连续重复画面：

```powershell
python tools/sprite_dataset.py extract D:\sprite-dataset `
  "D:\captures\beach.mp4" "D:\captures\forest.mp4"
```

输出结构如下：

```text
sprite-dataset/
├── images/<录屏标识>/*.jpg
├── labels/<录屏标识>/*.txt
└── recordings.json
```

为每张图片创建同名 YOLO 标签文件。精灵类别固定为 `0`，每行格式为：

```text
0 center_x center_y width height
```

坐标均为相对图片宽高的 0～1 数值。必须标注画面中所有属于目标集合的精灵
完整本体框，不只标注准星附近或实际被投球的实例。其他野外精灵、玩家、
其他玩家、NPC、坐骑、植物、特效、地图和 UI 都作为困难负样本，不标注为
`sprite`。没有目标的图片需要保留空标签文件。

标注完成后按整段录屏划分数据，避免相邻帧泄漏：

```powershell
python tools/sprite_dataset.py split D:\sprite-dataset
```

工具生成 `dataset.yaml`、三个主图片清单、`recording_splits.json`，
以及 `far_dataset.yaml` 和 `splits/far_test.txt`。默认随机种子固定，录屏数量
按约 70%/15%/15% 分给训练、验证和测试集；测试集中归一化高度不超过 0.05
的精灵框会额外进入远距离小目标清单。可以用
`--far-height-threshold` 调整这个分组阈值，它只影响评估清单，不改变主划分。

## 训练与导出

在独立训练环境安装 Ultralytics，并训练 YOLO11n 单类别模型。训练依赖和缓存
不得加入 Maa 运行发布包：

```powershell
python -m pip install ultralytics
yolo detect train data=D:\sprite-dataset\dataset.yaml model=yolo11n.pt `
  imgsz=640 epochs=120 patience=25 batch=4 device=0 workers=2 `
  amp=True cache=False flipud=0
```

单段素材训练可运行原型的时间隔离测试集门槛为 precision 不低于 90%、
recall 不低于 80%；正式模型门槛为 precision 不低于 95%、recall 不低于
90%。置信度阈值必须在验证集的 0.25～0.65 范围内以 precision 优先选择，
然后固定该阈值评估测试集。达到对应门槛后才导出并安装固定 640 输入的 ONNX：

```powershell
yolo export model=runs\detect\train\weights\best.pt format=onnx `
  imgsz=640 opset=17 simplify=True dynamic=False
```

主测试集和远距离小目标分组需要分别报告指标：

```powershell
yolo detect val model=runs\detect\train\weights\best.pt `
  data=D:\sprite-dataset\dataset.yaml imgsz=640 split=test
yolo detect val model=runs\detect\train\weights\best.pt `
  data=D:\sprite-dataset\far_dataset.yaml imgsz=640 split=test
```

若 640 模型在远距离分组明显漏检，应在验收结果中明确报告，不启用其他
启发式替代。

只有新录像时间隔离测试集和旧录像跨设备测试集都通过原型门槛，且 PyTorch
与 ONNX 在至少 20 张代表帧上的检测数量一致、匹配框 IoU 不低于 0.95，
才将导出的模型复制为：

```text
assets/resource/model/detect/sprite.onnx
```

重新启动 Maa 任务后，日志显示“YOLO 640”即表示模型路径校验通过。发布工具会随
resource 目录自动携带该 ONNX，用户端不需要安装 PyTorch 或 Ultralytics。

2026-07-30 将新电脑 6 分钟录像、原有 360 张旧数据和本次旧电脑 8 分
56.7 秒录像联合重训。数据按录像时间片段冻结为 train/val/test =
1391/300/317 帧，阈值扫描选中 `0.29`。合并测试 precision 为 93.22%、
recall 为 80.94%；新电脑测试为 91.00%/86.67%；旧电脑合并测试为
94.05%/79.06%。旧电脑 recall 比 80% 门槛低 0.94 个百分点，且 82 张
困难负样本中有 8 个阈值以上检测，因此没有安装模型，也没有修改运行阈值。

候选 ONNX 已导出到 `debug/auto_aim_training/moon_bear_joint_sprite.onnx`。
24 张跨设备代表帧上的 PyTorch/ONNX 检测数量一致，最低匹配 IoU 为
0.9714。测试集没有高度不超过 10% 的目标框；10%～20% 目标 recall 为
70.13%，大于 20% 目标 recall 为 87.08%，不能据此声称支持更远距离目标。
完整结果见 `debug/auto_aim_training/moon_bear_joint_training_report.md`。

2026-07-31 按用户决定覆盖上述原验收门槛，将候选 ONNX 安装为近距离月牙
雪熊原型。运行阈值改为 `0.40`，冻结测试 precision 为 96.75%、recall 为
76.94%，82 张困难负样本中有 2 个误检。当前不设置检测框高度门槛，远近目标
均按直接 YOLO 结果处理。该覆盖不改写原始训练结论，且尚未执行实机 HID
投球验收；当前状态为“近距离原型已安装，待实机验收”。
