# BetterForward

[中文 README](README.md)

Designed for better Telegram message forwarding.

Forward private messages to topics in a group—one user per topic—so multiple admins can collaborate without exposing personal accounts.

## Features

- **Privacy & teamwork**: User messages appear in group topics; admins reply from the group.
- **Multi-language**: English, Chinese, and Japanese (`en_US` / `zh_CN` / `ja_JP`).
- **Auto reply**: Keyword-based replies with regex support and scheduled active hours.
- **Human verification**: Button, math, emoji, custom Q&A, sticker, or [TGuard](https://github.com/SideCloudGroup/TGuard) verification, with random selection and configurable validity in days.
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

The bot upgrades its database automatically on startup. Before upgrading it backs the database up to `storage.db.bak-<old version>` in the data directory (the latest 3 are kept); if an upgrade fails, stop the container, delete `storage.db-wal` and `storage.db-shm` if present, then copy the backup over `storage.db` to restore.

An existing backup for the same version is preserved across failed upgrade retries. A backup is published at that path only after it has been written completely.

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

Six methods (enable multiple in **Captcha Settings** to choose one at random whenever verification is needed):

1. **Button** — tap to verify
2. **Math** — solve a simple arithmetic problem
3. **Emoji** — tap the emoji matching the prompt
4. **Custom Q&A** — configure questions and accepted answers; supports multiple answers and ignores case and surrounding whitespace
5. **Sticker** — send any sticker or send back an admin-configured sticker
6. **TGuard** — configure API URL and key under **TGuard API Settings**; verification opens in a Telegram Mini App

Under **Captcha Settings → Verification Validity**, enter a non-negative whole number of days, such as `7` or `30`. The default `0` keeps verification permanent. A positive value counts from the latest successful verification; the next message after expiry requires verification again. This applies to every method, and ordinary messages do not extend validity. Disabling captcha bypasses verification.

Validity changes apply immediately to existing verification records. Previously verified users keep their status, with the database upgrade time used as their starting timestamp. The default permanent behavior remains unchanged.

Admins can also use `/verify true` in a user topic to verify a user and renew validity, or `/verify false` to revoke verification. Expired buttons and buttons from older challenges cannot be reused to pass a new challenge.

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

### User Tags

Use a bot start link to record a tag for a user, for example to identify their referral source. No admin menu configuration is required.

1. Replace `YourBot` with your bot's username and share a link such as `https://t.me/YourBot?start=channel_a`.
2. When the user opens the link and presses **Start**, the bot receives `/start channel_a` and saves `channel_a` as their tag. Users can also send `/start channel_a` directly in a private chat with the bot.
3. When a user message creates a topic, the tag appears as `#tag` in the topic's initial pinned user info, e.g. `• Tag: #channel_a`. If the topic already exists, an admin must send `/refresh` in that user topic to send and pin user info containing the latest tag.

The bot adds the `#` prefix when displaying the tag. Use only the tag itself in the start link payload or `/start` argument; do not include `#`.

Tags must contain **1–64 characters**, using only English letters, digits, underscores `_`, and hyphens `-`, e.g. `channel_a`, `website`, or `campaign-2026`. Spaces and Chinese characters are not supported.

Each user has one tag. A later valid `/start <tag>` replaces it; `/start` without a payload, `/help`, and invalid payloads leave the existing tag unchanged. There are currently no admin commands to manually set or clear tags, filter users by tag, or broadcast by tag. Use `/setnote` for notes written by admins.

### Statistics & User Info

- `/stats` — in the group, show user count, new/active users today, messages received and replied today, banned and verified counts, and message queue status
- `/info` — in a user topic, show the user's profile, first contact, last message time, message count, verification and ban status

For users who contacted the bot before upgrading, first contact shows as "Unknown" and the message count is estimated from existing records.

### Reply Templates

Add or delete canned replies in the **Reply Templates** menu. In a user topic, send `/r <number>` to send that template to the user, or `/r` to list templates.

The admin menu shows 10 templates per page. Long template details use a shortened preview, while `/r <number>` still sends the full content. Long `/r` listings are split across messages.

Menu: **Reply Templates**

### Processed Reaction

When a message from a user or an admin is forwarded successfully, the original gets a ❤ reaction that disappears after about 3 seconds, so you can tell the bot is online. Messages stopped by verification, bans, permissions or the spam filter get none.

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
| `/stats` | Show statistics |
| `/info` | Show info about the topic's user |
| `/r [number]` | Send a reply template; lists templates without a number |

In private chat with the bot: `/start [tag]`, `/help`, `/delete`.

## Community

- Telegram channel [@betterforward](https://t.me/betterforward)

Please use `issues` for bug reports and feature requests.
