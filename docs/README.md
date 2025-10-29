# BetterForward

[中文README](README_zh.md)

Designed for better message forwarding in Telegram.

Use the "topic" feature to achieve a better PM bot.

Forward users' messages to topics in the group. Each user corresponds to a topic.

## Features

- Privacy: Admins' accounts are not exposed.
- Flexibility: Each user corresponds to an independent topic, and the experience is almost the same as private chat.
- Teamwork: Multiple admins can handle users' messages.
- Multi-language: Supports multiple languages, including English, Chinese and Japanese.
- Auto Response: Automatically replies to users' messages with predefined messages, and supports detection with regex.
  Allows setting active time for auto-reply.
- Captcha: Added a human verification feature to ensure that users are real people, effectively preventing the sending
  of spam messages.
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

- English - `en_US`
- Chinese - `zh_CN`
- Japanese - `ja_JP`

We welcome contributions to add more languages.

### Docker (Recommended)

#### Using Docker Compose (Recommended)

1. Download the `docker-compose.yml` file:

```bash
wget https://github.com/SideCloudGroup/BetterForward/raw/refs/heads/main/docker-compose.yml
```

2. Edit the `docker-compose.yml` file and replace the placeholder values:
    - `your_bot_token_here` with your actual bot token
    - `your_group_id_here` with your actual group ID
    - `zh_CN` with your preferred language (`en_US`, `zh_CN`, or `ja_JP`)
    - Leave `TG_API` empty or set your custom API endpoint
    - `WORKER=2` sets the number of worker threads (default: 2)

3. Run with Docker Compose:

```bash
docker compose up -d
```

#### Using Docker Run

Replace `/path/to/data` with your actual data directory path:

```bash
docker run -d --name betterforward \
    -e TOKEN=<your_bot_token> \
    -e GROUP_ID=<your_group_id> \
    -e LANGUAGE=<language> \
    -e WORKER=2 \
    -v /path/to/data:/app/data \
    --restart unless-stopped \
    pplulee/betterforward:latest
```

If you need to use a custom API, you can set the environment variable `TG_API`. Leave it empty or unset to use the
default API.

## Multithreading Support

BetterForward supports multithreading through the `WORKER` parameter to improve performance and handle concurrent
requests:

- **Default**: `WORKER=2` - Balanced performance for most deployments
- **High Traffic**: `WORKER=3-5` - Recommended for high-traffic scenarios
- **Thread Safety**: The application uses thread-safe database operations and message queues to prevent conflicts
- **Conflict Resolution**: Database transactions and locking mechanisms ensure data consistency across multiple worker
  threads

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