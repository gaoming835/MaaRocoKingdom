# AutoFlower 局域网远程控制 API

本文档面向 AI Agent、自动化程序和命令行客户端，描述如何通过电脑向 AutoFlower
Android App 发送键盘、鼠标和 AFS 脚本命令。

## 1. 工作方式

```text
电脑上的 AI/程序
    │  HTTP（局域网）
    ▼
AutoFlower Android App
    │  Bluetooth HID
    ▼
被控制的电脑
```

API 服务运行在手机上，默认端口为 `8765`。电脑不需要安装额外客户端，但必须满足：

1. 手机已通过 AutoFlower 与目标电脑建立 Bluetooth HID 连接。
2. 手机和发起请求的电脑位于同一可信局域网。
3. 用户已在 AutoFlower 首页手动启用“电脑远程控制”。
4. 调用方已获得手机显示的访问地址和六位 PIN。

示例基础地址：

```text
http://192.168.1.8:8765
```

基础地址由手机界面提供。AI 不应自行猜测手机 IP、PIN 或会话令牌。

## 2. AI 调用约束

AI 或自动化客户端调用本 API 时应遵守以下规则：

1. 首先调用 `/api/auth`，不要在日志或对话中泄露 PIN 和令牌。
2. 鉴权后先查询 `/api/status`，仅在 `connectionState` 为 `CONNECTED` 时发送任务。
3. `RUNNING`、`PAUSED`、`STOPPING` 表示已有任务，不能提交新任务。
4. 完整脚本应先调用 `/api/script/validate`，校验成功后再调用 `/api/task/start`。
5. `/api/command` 和 `/api/task/start` 不是幂等接口。请求结果不确定时不要盲目重发，
   否则可能重复产生键盘或鼠标操作；应先查询状态并请求用户确认。
6. 收到 `202 Accepted` 只表示任务已接收，不表示执行完成。应轮询 `/api/status`。
7. 建议轮询间隔为 `500 ms`，不要进行无间隔高频请求。
8. 错误 PIN 不应自动重试。一个来源地址在一分钟内第五次输错后会被锁定五分钟。
9. 使用 `{DOWN ...}` 或 `{MOUSE_DOWN ...}` 时，脚本应提供对应的 `UP`；
   中断任务时应调用 `/api/task/stop`。
10. 只根据 HTTP 状态码、`ok` 和 `code` 做程序判断，不要依赖中文 `message` 文本。

## 3. 通用 HTTP 规则

### 3.1 请求格式

- 协议：HTTP
- 字符编码：UTF-8
- JSON 请求头：

```http
Content-Type: application/json
```

- 除首页资源和 `/api/auth` 外，所有 API 都必须提供：

```http
Authorization: Bearer <token>
```

- 带 JSON 请求体的请求必须提供正确的 `Content-Length`。常见 HTTP 客户端会自动添加。
- 服务不开放 CORS。网页只能通过手机服务提供的同源页面调用 API。
- 只接受私有地址、链路本地地址和本机回环地址来源。

### 3.2 成功响应

一般成功响应：

```json
{
  "ok": true,
  "message": "操作成功",
  "status": {
    "taskState": "READY",
    "connectionState": "CONNECTED",
    "detail": null,
    "progressExecuted": 0,
    "progressTotal": 0,
    "progressPercent": 0,
    "currentCommandType": null,
    "remoteEnabled": true
  }
}
```

`message` 和部分字段会根据接口省略。脚本校验成功时还会返回 `commandCount`。

### 3.3 错误响应

```json
{
  "ok": false,
  "code": "TASK_BUSY",
  "message": "当前任务忙"
}
```

部分已鉴权业务错误还会携带当前 `status`。客户端必须允许响应中没有 `status`。

### 3.4 HTTP 状态码

| 状态码 | 含义 |
| --- | --- |
| `200` | 请求成功 |
| `202` | 任务已接收，正在或即将执行 |
| `400` | JSON、字段、命令或脚本不合法 |
| `401` | PIN 错误、令牌缺失、令牌失效或来源地址不匹配 |
| `403` | PIN 已锁定、来源网络不允许或跨站来源被拒绝 |
| `404` | 接口或网页资源不存在 |
| `405` | HTTP 方法不支持 |
| `409` | 已有认证客户端、任务忙或任务状态不允许当前操作 |
| `503` | 远程服务或控制线程不可用，或 Bluetooth HID 未连接 |

## 4. 认证

### `POST /api/auth`

提交手机显示的六位 PIN，获取随机 128 位会话令牌。

请求：

```json
{
  "pin": "123456"
}
```

成功响应：

```json
{
  "ok": true,
  "token": "0123456789abcdef0123456789abcdef",
  "status": {
    "taskState": "READY",
    "connectionState": "CONNECTED",
    "detail": null,
    "progressExecuted": 0,
    "progressTotal": 0,
    "progressPercent": 0,
    "currentCommandType": null,
    "remoteEnabled": true
  }
}
```

限制：

- 认证请求体最大 `2 KiB`。
- 同一时间只允许一个来源地址获得控制权限。
- 令牌与认证时的来源 IP 绑定。
- 同一来源重新认证会产生新令牌，旧令牌立即失效。
- 手机停止远程服务、重置会话或切换网络后，PIN 和令牌立即失效。

可能的错误码：

| HTTP | `code` | 说明 |
| --- | --- | --- |
| `400` | `INVALID_REQUEST` | 请求体或 PIN 字段不合法 |
| `401` | `INVALID_PIN` | PIN 不正确 |
| `403` | `AUTH_LOCKED` | 错误次数过多，来源被锁定 |
| `409` | `CLIENT_CONFLICT` | 已有另一来源地址通过认证 |
| `503` | `REMOTE_DISABLED` | 远程服务未启用或正在关闭 |

## 5. 查询状态

### `GET /api/status`

请求：

```http
GET /api/status HTTP/1.1
Host: 192.168.1.8:8765
Authorization: Bearer <token>
```

响应：

```json
{
  "ok": true,
  "status": {
    "taskState": "RUNNING",
    "connectionState": "CONNECTED",
    "detail": "正在执行",
    "progressExecuted": 12,
    "progressTotal": 40,
    "progressPercent": 30,
    "currentCommandType": "KEY",
    "remoteEnabled": true
  }
}
```

### 5.1 `taskState`

| 值 | 含义 | 能否提交新任务 |
| --- | --- | --- |
| `IDLE` | 空闲 | 可以 |
| `READY` | HID 已准备完成 | 可以 |
| `RUNNING` | 正在执行 | 不可以 |
| `PAUSED` | 已暂停 | 不可以 |
| `STOPPING` | 正在停止 | 不可以 |
| `FAILED` | 执行失败 | 可以 |
| `COMPLETED` | 执行完成 | 可以 |

### 5.2 `connectionState`

| 值 | 含义 |
| --- | --- |
| `UNAVAILABLE` | 当前平台或蓝牙能力不可用 |
| `DISCONNECTED` | HID 未连接 |
| `CONNECTING` | 正在连接 |
| `CONNECTED` | 可以发送 HID 命令 |

只有 `CONNECTED` 状态允许开始新的远程命令或脚本。

## 6. 执行单条命令

### `POST /api/command`

每次提交一条有效 AFS 命令。

请求：

```json
{
  "command": "{COMBO CTRL+S}"
}
```

成功：

```http
HTTP/1.1 202 Accepted
```

```json
{
  "ok": true,
  "message": "任务已接受",
  "status": {
    "taskState": "RUNNING",
    "connectionState": "CONNECTED",
    "detail": null,
    "progressExecuted": 0,
    "progressTotal": 1,
    "progressPercent": 0,
    "currentCommandType": null,
    "remoteEnabled": true
  }
}
```

`status` 是生成响应瞬间的状态快照；异步执行线程尚未启动时，`taskState` 也可能暂时仍为
`READY`。客户端应继续轮询 `/api/status`，不能依赖首次响应中的单个状态值判断任务完成。

限制：

- `command` 的 UTF-8 大小最大为 `4 KiB`。
- 解析后必须恰好包含一条命令。
- 不接受单独的 `{REPEAT ...}` 或 `{END_REPEAT}`。
- Bluetooth HID 未连接时返回 `503 HID_NOT_CONNECTED`。
- 有任务处于 `RUNNING`、`PAUSED` 或 `STOPPING` 时返回 `409 TASK_BUSY`。

常用示例：

```json
{"command":"{KEY A}"}
```

```json
{"command":"{COMBO CTRL+SHIFT+S}"}
```

```json
{"command":"{CLICK LEFT}"}
```

```json
{"command":"{SCROLL -3}"}
```

```json
{"command":"{MOVE 300 -40 500}"}
```

## 7. 校验完整脚本

### `POST /api/script/validate`

此接口只进行解析和校验，不执行 HID 操作，也不要求 HID 已连接。

请求：

```json
{
  "script": "{REPEAT 3}\n    {KEY A}\n    {DELAY 100}\n{END_REPEAT}"
}
```

成功响应：

```json
{
  "ok": true,
  "message": "脚本校验通过",
  "commandCount": 4,
  "status": {
    "taskState": "READY",
    "connectionState": "CONNECTED",
    "detail": null,
    "progressExecuted": 0,
    "progressTotal": 0,
    "progressPercent": 0,
    "currentCommandType": null,
    "remoteEnabled": true
  }
}
```

`commandCount` 是循环展开前解析出的命令数量。

限制：

- `script` 的 UTF-8 大小最大为 `256 KiB`。
- 空脚本返回 `400 EMPTY_SCRIPT`。
- 语法或参数错误返回 `400 INVALID_COMMAND`，`message` 中包含解析错误信息。

## 8. 开始完整脚本

### `POST /api/task/start`

请求：

```json
{
  "script": "{DELAY 500}\n{COMBO CTRL+S}\n{KEY ENTER}"
}
```

成功响应为 `202 Accepted`。客户端随后应每 `500 ms` 查询一次 `/api/status`，
直到进入 `COMPLETED`、`FAILED`、`IDLE` 或 `READY`。

限制：

- `script` 的 UTF-8 大小最大为 `256 KiB`。
- 脚本会在接收时再次校验。
- Bluetooth HID 必须处于 `CONNECTED`。
- 新任务不会替换或插入已有任务。

## 9. 暂停、继续和停止

以下接口都要求 Bearer 令牌，不需要 JSON 请求体。

### `POST /api/task/pause`

只在任务为 `RUNNING` 时成功。

状态不允许时：

```json
{
  "ok": false,
  "code": "PAUSE_REJECTED",
  "message": "当前状态无法暂停",
  "status": {
    "taskState": "READY",
    "connectionState": "CONNECTED",
    "detail": null,
    "progressExecuted": 0,
    "progressTotal": 0,
    "progressPercent": 0,
    "currentCommandType": null,
    "remoteEnabled": true
  }
}
```

### `POST /api/task/resume`

只在任务为 `PAUSED` 时成功。状态不允许时返回：

```text
409 RESUME_REJECTED
```

### `POST /api/task/stop`

停止当前任务，并发送空键盘和鼠标报告以释放保持状态。状态不允许时返回：

```text
409 STOP_REJECTED
```

停止请求成功后仍应轮询状态，直到任务离开 `RUNNING`、`PAUSED` 和 `STOPPING`。

## 10. AFS 指令速查

每行一条指令，空行忽略，缩进不影响执行。

### 10.1 键盘

| 指令 | 含义 |
| --- | --- |
| `{KEY A}` | 点击并释放按键 |
| `{COMBO CTRL+S}` | 点击并释放组合键 |
| `{DOWN W}` | 按下并保持 |
| `{UP W}` | 释放按键 |

常用按键名：

```text
A-Z
0-9
ENTER TAB SPACE ESC BACKSPACE DELETE
UP DOWN LEFT RIGHT HOME END PAGE_UP PAGE_DOWN
CTRL SHIFT ALT WIN META CMD COMMAND
F1-F12
NUMPAD_0-NUMPAD_9
NUMPAD_DIVIDE NUMPAD_MULTIPLY NUMPAD_SUBTRACT NUMPAD_ADD
NUMPAD_DOT NUMPAD_ENTER NUMPAD_EQUALS
```

组合键使用 `+` 连接，例如 `CTRL+S`、`ALT+TAB`、`CTRL+SHIFT+A`。

### 10.2 鼠标

| 指令 | 含义 |
| --- | --- |
| `{CLICK LEFT}` | 点击鼠标按键 |
| `{MOUSE_DOWN RIGHT}` | 按下并保持鼠标按键 |
| `{MOUSE_UP RIGHT}` | 释放鼠标按键 |
| `{SCROLL 1}` | 向上滚动 |
| `{SCROLL -3}` | 向下滚动三个单位 |
| `{MOVE 300 -40 500}` | 500 ms 内平滑移动相对距离 |
| `{MOVE_BIONIC 300 -40 500}` | 仿生轨迹移动相对距离 |

鼠标按键为 `LEFT`、`MIDDLE` 或 `RIGHT`。

### 10.3 等待与循环

```text
{DELAY 500}
{RANDOM_DELAY 80 120}
{REPEAT 3}
    {KEY A}
    {DELAY 100}
{END_REPEAT}
```

主要参数限制：

- `DELAY`、`RANDOM_DELAY` 和移动时长：`10`～`60000 ms`。
- `SCROLL`：`-127`～`127` 的非零整数，也支持 `UP` 和 `DOWN`。
- `MOVE`/`MOVE_BIONIC` 的 X、Y：`-32767`～`32767`，不能同时为 `0`。
- 循环次数必须是大于 `0` 的整数，循环允许嵌套。
- Boot Keyboard 最多同时保持六个非修饰键。

## 11. 命令行示例

以下示例假设：

```text
手机地址：http://192.168.1.8:8765
PIN：123456
```

### 11.1 PowerShell

```powershell
$baseUrl = "http://192.168.1.8:8765"

$auth = Invoke-RestMethod `
  -Method Post `
  -Uri "$baseUrl/api/auth" `
  -ContentType "application/json; charset=utf-8" `
  -Body (@{ pin = "123456" } | ConvertTo-Json)

$headers = @{ Authorization = "Bearer $($auth.token)" }

$status = Invoke-RestMethod `
  -Method Get `
  -Uri "$baseUrl/api/status" `
  -Headers $headers

if ($status.status.connectionState -ne "CONNECTED") {
    throw "Bluetooth HID is not connected"
}

Invoke-RestMethod `
  -Method Post `
  -Uri "$baseUrl/api/command" `
  -Headers $headers `
  -ContentType "application/json; charset=utf-8" `
  -Body (@{ command = "{COMBO CTRL+S}" } | ConvertTo-Json)
```

执行脚本：

```powershell
$script = @'
{DELAY 500}
{COMBO CTRL+S}
{KEY ENTER}
'@

$validation = Invoke-RestMethod `
  -Method Post `
  -Uri "$baseUrl/api/script/validate" `
  -Headers $headers `
  -ContentType "application/json; charset=utf-8" `
  -Body (@{ script = $script } | ConvertTo-Json)

if ($validation.ok) {
    Invoke-RestMethod `
      -Method Post `
      -Uri "$baseUrl/api/task/start" `
      -Headers $headers `
      -ContentType "application/json; charset=utf-8" `
      -Body (@{ script = $script } | ConvertTo-Json)
}
```

### 11.2 curl

鉴权：

```bash
curl -X POST "http://192.168.1.8:8765/api/auth" \
  -H "Content-Type: application/json" \
  --data '{"pin":"123456"}'
```

查询状态：

```bash
curl "http://192.168.1.8:8765/api/status" \
  -H "Authorization: Bearer TOKEN"
```

发送命令：

```bash
curl -X POST "http://192.168.1.8:8765/api/command" \
  -H "Authorization: Bearer TOKEN" \
  -H "Content-Type: application/json" \
  --data '{"command":"{KEY ENTER}"}'
```

停止任务：

```bash
curl -X POST "http://192.168.1.8:8765/api/task/stop" \
  -H "Authorization: Bearer TOKEN"
```

## 12. 错误码速查

| `code` | HTTP | 建议处理 |
| --- | --- | --- |
| `INVALID_REQUEST` | `400` | 修正 JSON、字段或请求大小 |
| `INVALID_COMMAND` | `400` | 修正 AFS 语法或参数 |
| `EMPTY_SCRIPT` | `400` | 提交至少一条命令 |
| `SINGLE_COMMAND_REQUIRED` | `400` | `/api/command` 只提交一条命令 |
| `REPEAT_NOT_ALLOWED` | `400` | 循环改用完整脚本接口 |
| `AUTH_REQUIRED` | `401` | 使用当前 PIN 重新鉴权 |
| `INVALID_PIN` | `401` | 停止自动重试并请用户核对 PIN |
| `AUTH_LOCKED` | `403` | 等待五分钟 |
| `NETWORK_FORBIDDEN` | `403` | 使用允许的局域网地址 |
| `ORIGIN_FORBIDDEN` | `403` | 使用同源网页或无跨站 `Origin` 的 API 客户端 |
| `CLIENT_CONFLICT` | `409` | 用户在手机端重置会话 |
| `TASK_BUSY` | `409` | 查询状态，暂停/继续/停止已有任务 |
| `PAUSE_REJECTED` | `409` | 仅在 `RUNNING` 时暂停 |
| `RESUME_REJECTED` | `409` | 仅在 `PAUSED` 时继续 |
| `STOP_REJECTED` | `409` | 当前没有可停止任务 |
| `REMOTE_DISABLED` | `503` | 请用户在手机端重新启用服务 |
| `HID_NOT_CONNECTED` | `503` | 请用户连接 Bluetooth HID |
| `CONTROL_TIMEOUT` | `503` | 查询状态，不要立即重发非幂等命令 |
| `CONTROL_INTERRUPTED` | `503` | 查询状态并等待服务恢复 |
| `CONTROL_UNAVAILABLE` | `503` | 查询状态或请用户重启远程服务 |

## 13. 推荐的 AI 执行流程

```text
获取用户提供的 baseUrl 和 PIN
    ↓
POST /api/auth
    ↓
保存 token，仅用于本次会话
    ↓
GET /api/status
    ↓
connectionState == CONNECTED？
    ├─ 否：停止，请用户连接蓝牙 HID
    └─ 是
        ↓
taskState 是否为 RUNNING / PAUSED / STOPPING？
    ├─ 是：不要提交新任务，先处理已有任务
    └─ 否
        ↓
单条命令 → POST /api/command
完整脚本 → validate → POST /api/task/start
        ↓
每 500 ms 查询状态，直到完成、失败或被停止
```

如果任务会产生不可逆操作，例如删除文件、发送消息、提交表单或支付，AI 仍应在调用
AutoFlower API 前取得用户的明确确认。API 鉴权只代表拥有设备控制权限，不代表用户已批准
每一个高影响操作。
