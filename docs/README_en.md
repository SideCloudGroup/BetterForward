# BetterForward

[中文 README](README.md)

Designed for better Telegram message forwarding.

Forward private messages to topics in a group—one user per topic—so multiple admins can collaborate without exposing personal accounts.

## Features

- **Privacy & teamwork**: User messages appear in group topics; admins reply from the group.
- **Multi-language**: English, Chinese, and Japanese (`en_US` / `zh_CN` / `ja_JP`).
- **Auto reply**: Keyword-based replies with regex support and scheduled active hours.
- **Human verification**: Button, math, or [TGuard](https://github.com/SideCloudGroup/TGuard) advanced verification.
- **Spam filtering**: Keyword-based detection with automatic routing to a dedicated spam topic.
- **Permission controls**: Restrict message types globally and per user; view and reset overrides.
- **User management**: Ban/unban, blocked-user auto-reply, terminate threads, topic notes, broadcast, and more.

Send `/help` in the group main topic to open the admin menu.

## Quick Start

1. Create a bot via [@BotFather](https://t.me/BotFather) and get the token.
2. Create a group with topics enabled and add the bot as admin (manage topics, delete messages).
3. Get the group ID (invite [@sc_ui_bot](https://t.me/sc_ui_bot) and send `/id`).
4. Deploy BetterForward (see Docker below).

Messages users send to the bot are forwarded to their topic in the group.

## Deployment

Available languages: `en_US`, `zh_CN`, `ja_JP`. Contributions for more languages are welcome.

### Docker (Recommended)

#### Docker Compose

1. Download `docker-compose.yml`:

```bash
wget https://github.com/SideCloudGroup/BetterForward/raw/refs/heads/main/docker-compose.yml
```

2. Edit and replace placeholders:
   - `your_bot_token_here` → bot token
   - `your_group_id_here` → group ID
   - `zh_CN` → language (`en_US`, `zh_CN`, or `ja_JP`)
   - `TG_API` → custom API URL (leave empty for default)
   - `WORKER` → worker thread count (increase for higher traffic)

3. Start:

```bash
docker compose up -d
```

#### Docker Run

Replace `/path/to/data` with your data directory:

```bash
docker run -d --name betterforward \
    -e TOKEN=<your_bot_token> \
    -e GROUP_ID=<your_group_id> \
    -e LANGUAGE=en_US \
    -e WORKER=2 \
    -v /path/to/data:/app/data \
    --restart unless-stopped \
    ghcr.io/sidecloudgroup/betterforward:latest
```

### Custom API

Set the `TG_API` environment variable for a custom Telegram API endpoint. Leave empty to use the default API.

## Upgrade

If you deploy with Docker, you can use [WatchTower](https://github.com/containrrr/watchtower) to update automatically (replace the container name below):

```bash
docker run --rm \
-v /var/run/docker.sock:/var/run/docker.sock \
containrrr/watchtower -cR \
<Container Name>
```

## Feature Guide

Configure most features via `/help` in the group main topic unless noted otherwise.

### Auto Reply

- Add and manage multiple auto-reply rules
- Regex keyword matching
- Optional active time windows

Menu: **Auto Reply**

### Default Message

Set the welcome or hint message users receive when they first contact the bot.

Menu: **Default Message**

### Ban & Blocked Reply

- Ban users to stop them from sending messages
- Optional auto-reply when a banned user tries to message

Menus: **Banned Users**, **Blocked User Reply**

### Human Verification

Three methods (choose in **Captcha Settings**):

1. **Button** — tap to verify
2. **Math** — solve a simple arithmetic problem
3. **TGuard** — configure API URL and key under **TGuard API Settings**; verification opens in a Telegram Mini App

Admins can also mark a user as verified with `/verify` in a user topic.

### Spam Filtering

- Add, view, and remove spam keywords from the admin menu
- Matched messages go to a dedicated spam topic instead of creating user threads

Menu: **Spam Keywords**

### Permission Controls

Under **Permission Settings**:

- **Default Permission Settings** — globally allow or deny: photos, stickers/animations, videos, voice, files (documents and audio), links, and @usernames. All are allowed by default.
- **Permission Restriction Reply Message** — optional reply when a user sends a blocked type (`{permission}` is replaced with the type name); can be disabled for silent blocking.

In a user topic, admins can adjust permissions for that user:

| Command | Description |
|---------|-------------|
| `/permissions` | Show permission status for the user |
| `/allow <keys...>` | Grant exceptions, e.g. `/allow photo link` |
| `/disallow <keys...>` | Deny types, e.g. `/disallow sticker` |
| `/resetpermissions <keys...>` | Clear overrides and inherit global defaults |

Valid keys: `photo`, `sticker`, `video`, `voice`, `file`, `link`, `username`; use `all` for every type (plain text is not affected).

### Broadcast & Time Zone

- **Broadcast Message** — notify all users at once
- **Time Zone Settings** — affects time-based features such as auto reply

### Topic Notes

In a user topic:

- `/setnote <text>` — set or clear a note for the topic
- `/getnote` — show the note

## Admin Commands

Available in the group (some apply only inside user topics):

| Command | Description |
|---------|-------------|
| `/help` | Open admin menu |
| `/ban` | Ban the user in the current topic |
| `/unban [User ID]` | Unban; omit ID in a user topic |
| `/terminate [User ID]` | End thread and delete topic; omit ID in a user topic |
| `/delete` | Delete a message (reply to the target message) |
| `/verify` | Mark user as verified |
| `/setnote <text>` | Set topic note |
| `/getnote` | Show topic note |
| `/refresh` | Refresh user info in the topic |
| `/permissions` | Show user permission status |
| `/allow <keys...>` | Grant per-user permissions |
| `/disallow <keys...>` | Deny per-user permissions |
| `/resetpermissions <keys...>` | Reset to global defaults |

In private chat with the bot: `/help`, `/delete`.

## Community

- Telegram channel [@betterforward](https://t.me/betterforward)

Please use `issues` for bug reports and feature requests.
