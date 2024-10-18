# Better Forward

为更好地转发 Telegram 消息而设计。

使用“话题”功能实现更好的Telegram PM Bot（私聊机器人）。

将用户的消息转发到群组中，每个用户对应一个主题。

## 特点

- 隐私：管理员的账号不会暴露。
- 灵活性：每个用户对应一个独立的主题，对话体验几乎与私聊相同。
- 团队协作：多个管理员可以同时处理用户的消息。
- 多语言：支持多种语言，包括英语和中文。
- 自动回复：自动回复用户的消息，回复内容可预设。支持正则表达式匹配关键词。
- 验证码：增加了人机验证功能，以确保用户是真人操作，从而有效防止垃圾信息（SPAM）的发送。

## 使用方法

1. 从 [@BotFather](https://t.me/BotFather) 创建一个机器人并获取 token。
2. 创建一个带有主题的群组，并将机器人添加为管理员。
3. 获取群组 ID。这一步可以通过邀请 [@sc_ui_bot](https://t.me/sc_ui_bot) 到群组中并发送`/id`来完成。
4. 将 BetterForward 部署到服务器。

任何发送给机器人的消息都将转发到群组中的相应主题。

更多设置项可以通过向机器人发送 `/help` 命令来查看。

## 部署

以下是可用的语言选项：

- 简体中文 - `zh_CN`
- 英语 - `en`
- 日语 - `ja_JP`

我们欢迎贡献以添加更多语言。

### Docker (推荐)

```bash
docker run -d --name betterforward \
    -e TOKEN=<your_bot_token> \
    -e GROUP_ID=<your_group_id> \
    -e LANGUAGE=zh_CN \
    -v /path/to/data:/app/data \
    --restart unless-stopped \
    pplulee/betterforward:latest
```

## 更新

如果您使用 Docker 进行部署此项目，可以使用[WatchTower](https://github.com/containrrr/watchtower)实现快速更新。**请您自行调整容器名
**。请使用以下命令更新：

```bash
docker run --rm \
-v /var/run/docker.sock:/var/run/docker.sock \
containrrr/watchtower -cR \
<容器名>
```

## 管理员命令

- `/terminate [用户 ID]`：结束与用户的对话。如果该命令在对话主题中发出，则无需包括用户 ID；当前的对话将自动删除。用户将不会接收到任何提示。
- `/help`：显示帮助菜单。
- `/ban`：阻止用户发送更多消息。此命令只能在对话中使用。
- `/unban [用户 ID]`：解除对用户的封禁。如果没有指定用户 ID，该命令将适用于当前对话主题中的用户。

## 交流社区

- Telegram频道 [@betterforward](https://t.me/betterforward)

请使用 `issues` 报告错误和提出功能请求。