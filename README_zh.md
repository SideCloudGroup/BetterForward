# Better Forward
为更好地转发 Telegram 消息而设计。

将用户的消息转发到群组中，每个用户对应一个主题。
## 特点
- 隐私：管理员的账号不会暴露。
- 灵活性：每个用户对应一个独立的主题，对话体验几乎与私聊相同。
- 团队协作：多个管理员可以同时处理用户的消息。
- 多语言：支持多种语言，包括英语和中文。

## 使用方法
1. 从 [@BotFather](https://t.me/BotFather) 创建一个机器人并获取 token。
2. 创建一个带有主题的群组，并将机器人添加为管理员。
3. 将 BetterForward 部署到服务器。

任何发送给机器人的消息都将转发到群组中的相应主题。

## 部署
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

## 管理员命令
- `/terminate [User ID]`: 删除与用户的对话主题。如果在话题中发送该命令，则将终止与当前用户的对话，且无需提供 User ID。用户不会收到提示。

## 交流社区
- Telegram频道 [@betterforward](https://t.me/betterforward)

请使用 `issues` 报告错误和提出功能请求。