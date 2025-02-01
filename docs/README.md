# Better Forward

[中文README](README_zh.md)

Designed for better message forwarding in Telegram.

Use the "topic" feature to achieve a better PM bot.

Forward users' messages to topics in the group. Each user corresponds to a topic.

## Features

- Privacy: Admins' accounts are not exposed.
- Flexibility: Each user corresponds to an independent topic, and the experience is almost the same as private chat.
- Teamwork: Multiple admins can handle users' messages.
- Multi-language: Supports multiple languages, including English, Chinese and Japanese.
- Auto Response: Automatically replies to users' messages with predefined messages, and supports detection with regex. Allows setting active time for auto-reply.
- Captcha: Added a human verification feature to ensure that users are real people, effectively preventing the sending of spam messages.
- Broadcast Message: Allows admins to send a message to all users at once.

## Usage

1. Create a bot from [@BotFather](https://t.me/BotFather) and get the token.
2. Create a group with topics, and add the bot as an admin.
3. Get the group ID.
   This step can be done by inviting [@sc_ui_bot](https://t.me/sc_ui_bot) to the group and use the command `/id`.
4. Deploy BetterForward to a server.

Any messages sent to the bot will be forwarded to the corresponding topic in the group.

More options and settings can be found by sending the `/help` command to the bot in the group.

## Deployment

The following are the available language options:

- English - `en`
- Chinese - `zh_CN`
- Japanese - `ja_JP`

We welcome contributions to add more languages.

### Docker (Recommended)

```bash
docker run -d --name betterforward \
    -e TOKEN=<your_bot_token> \
    -e GROUP_ID=<your_group_id> \
    -e LANGUAGE=<language> \
    -v /path/to/data:/app/data \
    --restart unless-stopped \
    pplulee/betterforward:latest
```

## Upgrade

If you deploy this project using Docker, you can use [WatchTower](https://github.com/containrrr/watchtower) to quickly
update. **Please adjust the container name yourself**. Use the following command to update:

```bash
docker run --rm \
-v /var/run/docker.sock:/var/run/docker.sock \
containrrr/watchtower -cR \
<Container Name>
```

## Admin Commands

- `/terminate [User ID]`: Ends the conversation with a specified user. When this command is issued within a conversation
  thread, there is no need to include the User ID; the current conversation will be terminated automatically. The user
  will not receive any further prompts or notifications.
- `/help`: Displays the help menu, which includes a list of available commands and their instructions.
- `/ban`: Prevents the user from sending more messages. This command is only applicable within the specific
  conversation thread where it is executed.
- `/unban [User ID]`: Reinstates the ability for a user to send messages. If no User ID is specified, the command will
  apply to the user in the current conversation thread.

## Community

- Telegram Channel [@betterforward](https://t.me/betterforward)

Please use `issues` for bug reports and feature requests.