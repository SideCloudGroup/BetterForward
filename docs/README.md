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
- 验证码：增加了人机验证功能，以确保用户是真人操作，从而有效防止垃圾信息（SPAM）的发送。
- 垃圾消息防御：基于关键词的智能垃圾消息过滤系统，自动识别并隔离垃圾内容。支持可扩展的检测器接口，可轻松对接其他检测方法（如AI模型、外部API等）。
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

## 管理员命令

- `/terminate [用户 ID]`：结束与用户的对话。如果该命令在对话主题中发出，则无需包括用户 ID；当前的对话将自动删除。用户将不会接收到任何提示。
- `/help`：显示帮助菜单。
- `/ban`：阻止用户发送更多消息。此命令只能在对话中使用。
- `/unban [用户 ID]`：解除对用户的封禁。如果没有指定用户 ID，该命令将适用于当前对话主题中的用户。

## 交流社区

- Telegram频道 [@betterforward](https://t.me/betterforward)

请使用 `issues` 报告错误和提出功能请求。