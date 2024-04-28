# Better Forward
[中文README](README_zh.md)

Designed for better message forwarding in Telegram.

Forward users' messages to topics in the group. Each user corresponds to a topic.
## Features
- Privacy: Admins' accounts are not exposed.
- Flexibility: Each user corresponds to an independent topic, and the experience is almost the same as private chat.
- Teamwork: Multiple admins can handle users' messages.
- Multi-language: Supports multiple languages, including English and Chinese.

## Usage
1. Create a bot from [@BotFather](https://t.me/BotFather) and get the token.
2. Create a group with topics, and add the bot as an admin.
3. Deploy BetterForward to a server.

Any messages sent to the bot will be forwarded to the corresponding topic in the group.

## Deployment
### Docker (Recommended)
```bash
docker run -d --name betterforward \
    -e TOKEN=<your_bot_token> \
    -e GROUP_ID=<your_group_id> \
    -e LANGUAGE=en \
    -v /path/to/data:/app/data \
    --restart unless-stopped \
    pplulee/betterforward:latest
```

## Admin Commands
- `/terminate [User ID]`: Terminates the conversation with the user. If the command is sent in the topic, the conversation with the current user in the topic will be terminated, and there is no need to provide User ID. The user will not receive any prompts.

## Community
- Telegram Channel [@betterforward](https://t.me/betterforward)

Please use `issues` for bug reports and feature requests.