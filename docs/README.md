# BetterForward

[English README](README_en.md)

为更好地转发 Telegram 消息而设计。

使用“话题”功能实现更好的Telegram PM Bot（私聊机器人）。

将用户的消息转发到群组中，每个用户对应一个主题。

## 特点

- 隐私：管理员的账号不会暴露。
- 灵活性：每个用户对应一个独立的主题，对话体验几乎与私聊相同。
- 团队协作：多个管理员可以同时处理用户的消息。
- 多语言：支持多种语言，包括英语和中文。
- 自动回复：自动回复用户的消息，回复内容可预设。支持正则表达式匹配关键词。允许设置自动回复的生效时间。
- 验证码：增加了人机验证功能，以确保用户是真人操作，从而有效防止垃圾信息（SPAM）的发送。支持多种验证方式：
  - **按钮验证**：简单的按钮点击验证
  - **数学题验证**：通过解答数学题进行验证
  - **TGuard验证**：集成 [TGuard](https://github.com/SideCloudGroup/TGuard) 提供的高级人机验证服务，支持多种验证码驱动（hCaptcha、Cap.js、Turnstile），通过Telegram Mini Web App提供美观的验证界面
- 垃圾消息防御：基于关键词的智能垃圾消息过滤系统，自动识别并隔离垃圾内容。支持可扩展的检测器接口，可轻松对接其他检测方法（如AI模型、外部API等）。
- 权限控制：管理员可以按消息类型全局限制图片、贴图/动图、视频、语音、文件、链接和用户名，并可在单个用户话题中独立设置权限。
- 广播消息：允许管理员一次性向所有用户发送消息。

## 使用方法

1. 从 [@BotFather](https://t.me/BotFather) 创建一个机器人并获取 token。
2. 创建一个带有主题的群组，并将机器人添加为管理员。
3. 获取群组 ID。这一步可以通过邀请 [@sc_ui_bot](https://t.me/sc_ui_bot) 到群组中并发送`/id`来完成。
4. 将 BetterForward 部署到服务器。

任何发送给机器人的消息都将转发到群组中的相应主题。

更多操作和设置项可以通过在群组内向机器人发送 `/help` 命令来查看。

## 部署

以下是可用的语言选项：

- 简体中文 - `zh_CN`
- 英语 - `en_US`
- 日语 - `ja_JP`

我们欢迎贡献以添加更多语言。

### Docker (推荐)

#### 使用 Docker Compose (推荐)

1. 下载 `docker-compose.yml` 文件：

```bash
wget https://github.com/SideCloudGroup/BetterForward/raw/refs/heads/main/docker-compose.yml
```

2. 编辑 `docker-compose.yml` 文件并替换占位符值：
    - `your_bot_token_here` 替换为您的实际机器人令牌
    - `your_group_id_here` 替换为您的实际群组 ID
    - `zh_CN` 替换为您偏好的语言 (`en_US`, `zh_CN`, 或 `ja_JP`)
    - `TG_API` 留空或设置您的自定义 API 端点
    - `WORKER=2` 设置工作线程数量（默认：2）

3. 使用 Docker Compose 运行：

```bash
docker compose up -d
```

#### 使用 Docker Run

将 `/path/to/data` 替换为您的实际数据目录路径：

```bash
docker run -d --name betterforward \
    -e TOKEN=<your_bot_token> \
    -e GROUP_ID=<your_group_id> \
    -e LANGUAGE=zh_CN \
    -e WORKER=2 \
    -v /path/to/data:/app/data \
    --restart unless-stopped \
    ghcr.io/sidecloudgroup/betterforward:latest
```

## 自定义 API

如果需要使用自定义 API，可以设置环境变量 `TG_API`。留空或不设置将使用默认 API。

## 多线程支持

BetterForward 通过 `WORKER` 参数支持多线程，以提高性能并处理并发请求：

- **默认**：`WORKER=2` - 适用于大多数部署的平衡性能
- **高流量**：`WORKER=3-5` - 推荐用于高流量场景
- **线程安全**：应用程序使用线程安全的数据库操作和消息队列来防止冲突
- **冲突解决**：数据库事务和锁定机制确保多个工作线程之间的数据一致性

## 更新

如果您使用 Docker 进行部署此项目，可以使用[WatchTower](https://github.com/containrrr/watchtower)实现快速更新。**请您自行调整容器名
**。请使用以下命令更新：

```bash
docker run --rm \
-v /var/run/docker.sock:/var/run/docker.sock \
containrrr/watchtower -cR \
<容器名>
```

## 人机验证

BetterForward 支持多种人机验证方式，确保只有真实用户才能发送消息。

### 支持的验证方式

1. **按钮验证** - 最简单的验证方式，用户只需点击按钮即可完成验证
2. **数学题验证** - 用户需要解答简单的数学题来证明自己是真人
3. **TGuard验证** - 集成 [TGuard](https://github.com/SideCloudGroup/TGuard) 提供的高级人机验证服务
   - 支持多种验证码驱动：hCaptcha、Cap.js、Turnstile
   - 通过Telegram Mini Web App提供美观的验证界面
   - 需要配置TGuard API地址和API密钥
   - 验证完成后自动标记用户为已验证状态

### 配置TGuard验证

1. 在群组主话题中发送 `/help` 命令
2. 选择 "🌐 TGuard API设置" 菜单
3. 设置TGuard API地址（例如：`https://your-tguard-domain.com`）
4. 设置TGuard API密钥
5. 返回验证码设置，选择 "TGuard Captcha"

### TGuard验证流程

1. 用户首次发送消息时，BetterForward会向TGuard API发起验证请求
2. TGuard返回验证链接和token
3. BetterForward向用户发送包含验证按钮的消息（Mini Web App）
4. 用户点击按钮，在Web App中完成人机验证
5. 用户完成验证后，再次发送消息时，BetterForward会检查验证状态
6. 验证通过后，用户消息正常转发

## 垃圾消息防御

BetterForward 内置了智能垃圾消息过滤系统，帮助你有效管理和隔离垃圾内容。

### 关键词过滤

- **智能匹配**：支持模糊匹配，自动识别包含垃圾关键词的消息
- **高性能算法**：使用优化的正则表达式，时间复杂度为 O(n)，处理速度极快
- **集中管理**：通过管理菜单轻松添加、查看、删除关键词
- **自动隔离**：匹配到的垃圾消息自动转发到专门的垃圾话题，不会创建用户话题
- **静默处理**：转发时使用静默模式，不会产生通知打扰

### 使用方法

1. 在群组主话题中发送 `/help` 命令
2. 选择 "🚫 垃圾关键词" 菜单
3. 点击 "➕ 添加关键词" 添加要过滤的关键词
4. 点击 "📋 查看关键词" 管理现有关键词

关键词配置保存在 `data/spam_keywords.json` 文件中，支持手动编辑。

## 权限控制

管理员可以在群组主话题发送 `/help` 打开管理菜单，然后进入 `默认权限设置` 管理用户可发送的消息类型。所有权限默认开启，管理员可以按需关闭一项或多项全局默认权限。

默认权限菜单包含以下项目：

- `图片权限`：控制图片消息。
- `贴图&动图权限`：控制贴图和动图消息。
- `视频权限`：控制视频消息。
- `语音权限`：控制语音消息。
- `文件权限`：控制文件消息。
- `链接权限`：控制文本和媒体 caption 中的链接。
- `用户名权限`：控制文本和媒体 caption 中的 `@username`。

权限生效优先级：

- 在用户话题中执行 `/disallow` 设置的单用户禁止优先级最高，即使全局默认开启也会阻止该用户。
- 在用户话题中执行 `/allow` 设置的单用户允许会覆盖全局默认关闭。
- 没有单用户设置时，用户继承全局默认权限。

受限消息会在转发到管理员话题、创建消息映射和自动回复之前被拦截。管理员可以在 `默认权限设置` 中进入 `权限限制时的回复消息` 中设置回复模板，例如 `您不被允许发出“{permission}”类型消息，请先联系对方解限。`；`{permission}` 会替换成 `图片`、`链接` 等权限名。也可以关闭该回复，实现静默阻止。

链接和用户名只检查普通文本和媒体 caption，不进行 OCR，也不读取图片或文件内容。

## 管理员命令

- `/terminate [用户 ID]`：结束与用户的对话。如果该命令在对话主题中发出，则无需包括用户 ID；当前的对话将自动删除。用户将不会接收到任何提示。
- `/help`：显示帮助菜单。
- `/ban`：阻止用户发送更多消息。此命令只能在对话中使用。
- `/unban [用户 ID]`：解除对用户的封禁。如果没有指定用户 ID，该命令将适用于当前对话主题中的用户。
- `/allow <权限键...>`：在当前用户话题中授予一个或多个单用户权限，例如 `/allow photo video link`。
- `/disallow <权限键...>`：在当前用户话题中禁止一个或多个单用户权限，例如 `/disallow photo link`。

`/allow` 和 `/disallow` 只能由管理员在已映射的用户话题中使用。有效权限键为：`photo`、`sticker`、`video`、`voice`、`file`、`link`、`username`、`all`。

使用 `all` 权限键将授予/禁止所有权限（不包括文本）。

## 交流社区

- Telegram频道 [@betterforward](https://t.me/betterforward)

请使用 `issues` 报告错误和提出功能请求。