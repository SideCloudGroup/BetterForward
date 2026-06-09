# BetterForward

[中文README](README.md)

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
  of spam messages. Supports multiple verification methods:
  - **Button Verification**: Simple button click verification
  - **Math Verification**: Users solve simple math problems to prove they are human
  - **TGuard Verification**: Advanced human verification service integrated with [TGuard](https://github.com/SideCloudGroup/TGuard), supporting multiple captcha drivers (hCaptcha, Cap.js, Turnstile) with a beautiful Telegram Mini Web App interface
- Spam Protection: Intelligent spam filtering system with keyword-based detection. Supports extensible detector interface for integrating AI models, external APIs, and custom detection methods.
- Permission Controls: Admins can globally restrict photos, stickers/animations, videos, voice messages, files, links,
  and usernames, and independently configure permissions from each user's topic.
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
    ghcr.io/sidecloudgroup/betterforward:latest
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

## Human Verification

BetterForward supports multiple human verification methods to ensure only real users can send messages.

### Supported Verification Methods

1. **Button Verification** - The simplest verification method, users only need to click a button
2. **Math Verification** - Users need to solve simple math problems to prove they are human
3. **TGuard Verification** - Advanced human verification service integrated with [TGuard](https://github.com/SideCloudGroup/TGuard)
   - Supports multiple captcha drivers: hCaptcha, Cap.js, Turnstile
   - Beautiful verification interface via Telegram Mini Web App
   - Requires TGuard API URL and API key configuration
   - Automatically marks users as verified after completion

### Configuring TGuard Verification

1. Send `/help` command in the main group topic
2. Select "🌐 TGuard API Settings" menu
3. Set TGuard API URL (e.g., `https://your-tguard-domain.com`)
4. Set TGuard API key
5. Return to captcha settings and select "TGuard Captcha"

### TGuard Verification Flow

1. When a user sends their first message, BetterForward creates a verification request via TGuard API
2. TGuard returns a verification URL and token
3. BetterForward sends a message with a verification button (Mini Web App) to the user
4. User clicks the button and completes human verification in the Web App
5. After verification, when the user sends another message, BetterForward checks the verification status
6. Once verified, user messages are forwarded normally

## Spam Protection

BetterForward includes an intelligent spam filtering system to help you effectively manage and isolate spam content.

### Keyword Filtering

- **Smart Matching**: Supports fuzzy matching to automatically identify messages containing spam keywords
- **High Performance**: Uses optimized regex with O(n) time complexity for extremely fast processing
- **Easy Management**: Add, view, and delete keywords through the admin menu
- **Auto Isolation**: Matched spam messages are automatically forwarded to a dedicated spam topic without creating user threads
- **Silent Mode**: Forwarding uses silent mode to avoid notification spam

### Usage

1. Send `/help` command in the main group topic
2. Select "🚫 Spam Keywords" menu
3. Click "➕ Add Keyword" to add keywords to filter
4. Click "📋 View Keywords" to manage existing keywords

Keyword configuration is saved in `data/spam_keywords.json` and supports manual editing.

## Permission Controls

Admins can send `/help` in the group main topic, open the admin menu, and choose `Default Permission Settings` to manage which message types users may send. All permission categories are enabled by default, and admins can disable one or more global defaults.

The default-permissions menu includes:

- `photo`: photo messages (Photo Permission)
- `sticker`: stickers and animations (Sticker & Animation Permission)
- `video`: video messages (Video Permission)
- `voice`: voice messages (Voice Permission)
- `file`: document/file messages (File Permission)
- `link`: links in text and media captions (Link Permission)
- `username`: `@username` mentions in text and media captions (Username Permission)

Effective permission precedence:

- A per-user `/disallow` denial wins over a global allow.
- A per-user `/allow` grant wins over a global denial.
- Users without a per-user override inherit the global default.

Restricted messages are blocked before admin-topic forwarding, message mapping, and auto replies. In `Default Permission Settings`, admins can open `Permission Restriction Reply Message` to customize the user reply template, for example `You are not allowed to send {permission} messages. Please contact the other party to lift the restriction.` The `{permission}` placeholder is replaced with labels such as `Photo` or `Link`. Admins can also disable this reply for no-reply silent blocking.

Link and username permissions inspect only normal text and media captions. BetterForward does not perform OCR and does not inspect image or file contents.

## Admin Commands

- `/terminate [User ID]`: Ends the conversation with a specified user. When this command is issued within a conversation
  thread, there is no need to include the User ID; the current conversation will be terminated automatically. The user
  will not receive any further prompts or notifications.
- `/help`: Displays the help menu, which includes a list of available commands and their instructions.
- `/ban`: Prevents the user from sending more messages. This command is only applicable within the specific
  conversation thread where it is executed.
- `/unban [User ID]`: Reinstates the ability for a user to send messages. If no User ID is specified, the command will
  apply to the user in the current conversation thread.
- `/allow <permission keys...>`: Grants one or more per-user permission exceptions in the current user topic, for
  example `/allow photo video link`.
- `/disallow <permission keys...>`: Denies one or more per-user permissions in the current user topic, for example
  `/disallow photo link`.

`/allow` and `/disallow` can be used only by admins in mapped user conversation topics. Valid keys are: `photo`,
`sticker`, `video`, `voice`, `file`, `link`, `username`, and `all`.

Using the `all` permission key grants or denies all permissions, excluding text messages.

## Community

- Telegram Channel [@betterforward](https://t.me/betterforward)

Please use `issues` for bug reports and feature requests.