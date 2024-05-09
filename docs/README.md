# Better Forward
[中文README](README_zh.md)

Designed for better message forwarding in Telegram.

Forward users' messages to topics in the group. Each user corresponds to a topic.
## Features
- Privacy: Admins' accounts are not exposed.
- Flexibility: Each user corresponds to an independent topic, and the experience is almost the same as private chat.
- Teamwork: Multiple admins can handle users' messages.
- Multi-language: Supports multiple languages, including English and Chinese.
- Auto Response: Automatically replies to users' messages with predefined messages, and supports detection with regex.

## Usage
1. Create a bot from [@BotFather](https://t.me/BotFather) and get the token.
2. Create a group with topics, and add the bot as an admin.
3. Get the group ID. 
This step can be done by inviting [@tg_get_userid_bot](https://t.me/tg_get_userid_bot) to the group. 
For privacy reason, remember to remove it after getting the group ID.
4. Deploy BetterForward to a server.

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
- `/terminate [User ID]`: Ends the conversation with a specified user. When this command is issued within a conversation thread, there is no need to include the User ID; the current conversation will be terminated automatically. The user will not receive any further prompts or notifications.
- `/help`: Displays the help menu, which includes a list of available commands and instructions on how to use them.
- `/ban`: Prevents the user from sending any more messages. This command is only applicable within the specific conversation thread where it is executed.
- `/unban [User ID]`: Reinstates the ability for a user to send messages. If no User ID is specified, the command will apply to the user in the current conversation thread.

## Community
- Telegram Channel [@betterforward](https://t.me/betterforward)

Please use `issues` for bug reports and feature requests.