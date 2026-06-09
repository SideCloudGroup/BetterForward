# BetterForward

[English README](README_en.md)

为更好地转发 Telegram 消息而设计。

使用群组「话题」功能，将用户私聊消息转发到群组中——每位用户对应一个独立话题，多位管理员可协同处理，且无需暴露个人账号。

## 特点

- **隐私与协作**：用户消息进入群组话题，管理员在群内回复，个人账号不直接暴露。
- **多语言**：支持英语、简体中文、日语（`en_US` / `zh_CN` / `ja_JP`）。
- **自动回复**：按关键词（支持正则）自动回复，可设置生效时段。
- **人机验证**：按钮验证、数学题验证，或接入 [TGuard](https://github.com/SideCloudGroup/TGuard) 高级验证。
- **垃圾消息过滤**：关键词识别后自动转入垃圾话题，不打扰正常会话。
- **权限控制**：按消息类型限制用户可发送的内容；支持全局默认与单用户单独设置，可查看与重置。
- **用户管理**：封禁/解封、封禁用户自动回复、结束会话、话题备注、广播消息等。

在群组主话题发送 `/help` 可打开管理菜单。

## 快速开始

1. 从 [@BotFather](https://t.me/BotFather) 创建机器人并获取 Token。
2. 创建开启话题的群组，将机器人设为管理员（需能管理话题、删除消息）。
3. 获取群组 ID（可邀请 [@sc_ui_bot](https://t.me/sc_ui_bot) 发送 `/id`）。
4. 部署 BetterForward（见下方 Docker 说明）。

用户私聊机器人的消息将转发到群组中对应的话题。

## 部署

可用语言：`zh_CN`（简体中文）、`en_US`（英语）、`ja_JP`（日语）。欢迎贡献更多语言翻译。

### Docker（推荐）

#### Docker Compose

1. 下载 `docker-compose.yml`：

```bash
wget https://github.com/SideCloudGroup/BetterForward/raw/refs/heads/main/docker-compose.yml
```

2. 编辑并替换占位符：
   - `your_bot_token_here` → 机器人 Token
   - `your_group_id_here` → 群组 ID
   - `zh_CN` → 语言（`en_US` / `zh_CN` / `ja_JP`）
   - `TG_API` → 自定义 API 地址（留空使用默认）
   - `WORKER` → 工作线程数（流量大时可适当调高）

3. 启动：

```bash
docker compose up -d
```

#### Docker Run

将 `/path/to/data` 替换为实际数据目录：

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

### 自定义 API

通过环境变量 `TG_API` 设置自定义 Telegram API 地址；留空则使用官方 API。

## 更新

使用 Docker 部署时，可通过 [WatchTower](https://github.com/containrrr/watchtower) 自动更新（请将下方命令中的容器名改为实际名称）：

```bash
docker run --rm \
-v /var/run/docker.sock:/var/run/docker.sock \
containrrr/watchtower -cR \
<容器名>
```

## 功能说明

以下功能均在群组主话题发送 `/help` 后，通过管理菜单配置（除非另有说明）。

### 自动回复

- 添加、管理多条自动回复规则
- 支持正则匹配关键词
- 可设置规则生效时间段

菜单：**自动回复**

### 默认消息

设置用户首次联系机器人时收到的欢迎/提示消息。

菜单：**默认消息**

### 封禁与封禁回复

- 封禁指定用户，阻止其继续发消息
- 可为被封禁用户配置自动回复文案（也可关闭回复）

菜单：**已封禁用户**、**封禁用户回复**

### 人机验证

支持三种方式（在 **验证码设置** 中选择）：

1. **按钮验证** — 点击按钮即可
2. **数学题验证** — 解答简单算术题
3. **TGuard 验证** — 需在 **TGuard API 设置** 中配置 API 地址与密钥，验证界面通过 Telegram Mini App 打开

管理员也可在用户话题中使用 `/verify` 手动标记用户为已验证。

### 垃圾消息过滤

- 在管理菜单中添加、查看、删除垃圾关键词
- 命中关键词的消息会转入专用垃圾话题，不创建用户话题

菜单：**垃圾关键词**

### 权限控制

在 **权限设置** 中可配置：

- **默认权限设置** — 全局控制用户能否发送：图片、贴图/动图、视频、语音、文件（含文档与音频）、链接、@用户名。默认全部允许，可按需关闭。
- **权限限制时的回复消息** — 用户发送被禁止类型的消息时，可自动回复提示（支持自定义模板，`{permission}` 会替换为对应类型名称）；也可关闭回复以实现静默拦截。

在用户话题中，管理员可单独调整该用户的权限：

| 命令 | 说明 |
|------|------|
| `/permissions` | 查看该用户各项权限状态 |
| `/allow <类型...>` | 单独允许，例如 `/allow photo link` |
| `/disallow <类型...>` | 单独禁止，例如 `/disallow sticker` |
| `/resetpermissions <类型...>` | 清除单独设置，恢复继承全局默认 |

类型键：`photo`、`sticker`、`video`、`voice`、`file`、`link`、`username`；使用 `all` 可一次操作全部类型（不含纯文本）。

### 广播与时区

- **广播消息** — 向所有用户发送通知
- **时区设置** — 影响自动回复等与时间相关的功能

### 话题备注

在用户话题中：

- `/setnote <内容>` — 设置或清除该话题的备注
- `/getnote` — 查看备注

## 管理员命令

在群组内可用（部分命令仅适用于用户话题）：

| 命令 | 说明 |
|------|------|
| `/help` | 打开管理菜单 |
| `/ban` | 封禁当前话题用户 |
| `/unban [用户 ID]` | 解封用户；在用户话题中可省略 ID |
| `/terminate [用户 ID]` | 结束会话并删除话题；在用户话题中可省略 ID |
| `/delete` | 删除消息（回复要删除的消息后使用） |
| `/verify` | 将用户标记为已通过验证 |
| `/setnote <内容>` | 设置话题备注 |
| `/getnote` | 查看话题备注 |
| `/refresh` | 刷新话题中的用户信息 |
| `/permissions` | 查看用户权限状态 |
| `/allow <类型...>` | 单独允许权限 |
| `/disallow <类型...>` | 单独禁止权限 |
| `/resetpermissions <类型...>` | 重置为继承全局默认 |

用户私聊中可用：`/help`、`/delete`。

## 交流社区

- Telegram 频道 [@betterforward](https://t.me/betterforward)

请通过 `issues` 报告问题或提出功能建议。
