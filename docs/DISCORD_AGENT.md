# Discord Local Agent

This optional daemon lets the repo owner send mobile Discord commands to this local machine. The bot runs locally, edits this checkout, and pushes to `goood2280/flow` using the repo's SSH Git configuration.

## Security Model

- The bot rejects every Discord user not listed in `DISCORD_ALLOWED_USER_IDS`.
- `DISCORD_ALLOWED_CHANNEL_IDS` is recommended so commands work only in one private channel.
- Arbitrary shell commands are not supported.
- The real `.env.discord` file is ignored by Git and must never be committed.
- Keep this bot out of public servers. If the token is exposed, reset it in the Discord Developer Portal.

## Discord Setup

1. Create an application in the Discord Developer Portal.
2. Open the app's **Bot** page and copy or reset the bot token.
3. Enable **Message Content Intent** on the Bot page so prefix commands can be read.
4. Invite the bot to your private server/channel with at least:
   - View Channels
   - Send Messages
   - Read Message History
5. In Discord, enable Developer Mode and copy:
   - your user ID
   - the private command channel ID

## Local Setup

```bash
cd /mnt/d/TEST_Making_Video/semi_all/flow
python3 -m pip install -r requirements-discord.txt
cp .env.discord.example .env.discord
```

If your system Python blocks package installation, use a local virtual environment:

```bash
python3 -m venv .venv-discord
.venv-discord/bin/python -m pip install -r requirements-discord.txt
```

If `python3-venv` is not installed and you cannot use `sudo`, install into the user Python site:

```bash
python3 -m pip install --user --break-system-packages -r requirements-discord.txt
```

Edit `.env.discord`:

```dotenv
DISCORD_BOT_TOKEN=your-real-token
DISCORD_ALLOWED_USER_IDS=your-discord-user-id
DISCORD_ALLOWED_CHANNEL_IDS=your-private-channel-id
FLOW_AGENT_REPO=/mnt/d/TEST_Making_Video/semi_all/flow
FLOW_DISCORD_PREFIX=!flow
FLOW_AGENT_ALLOW_CODEX=1
```

Run it:

```bash
python3 scripts/discord_flow_agent.py
```

If you used `.venv-discord`, run `.venv-discord/bin/python scripts/discord_flow_agent.py` instead.

The local machine must stay on and the bot process must keep running. For a Linux/systemd environment, adapt `scripts/flow-discord-agent.service.example`.

## Commands

```text
!flow help
!flow status
!flow build
!flow pull
!flow push
!flow deploy <commit message>
!flow task <natural language request>
```

`!flow task` runs `codex exec` locally with workspace-write permissions, then runs the frontend production build, commits changes, and pushes `main`.

## Operational Notes

- `!flow task` requires a clean working tree before it starts.
- If Codex changes files but the build fails, the bot leaves the changes uncommitted for manual inspection.
- `!flow deploy` is for already-made local edits: it builds, commits, and pushes.
- The repo should remain on `main`; `FLOW_AGENT_REQUIRE_BRANCH` defaults to `main`.
