#!/usr/bin/env python3
"""Discord control plane for the local Flow repository.

The bot intentionally exposes a small allowlist of actions. It does not run
arbitrary shell commands from Discord.
"""

from __future__ import annotations

import argparse
import asyncio
import os
import shlex
import shutil
from pathlib import Path
from typing import Iterable

import discord


ROOT = Path(__file__).resolve().parents[1]
DISCORD_LIMIT = 1900


def load_env_file(path: Path) -> None:
    if not path.exists():
        return
    for raw_line in path.read_text(encoding="utf-8").splitlines():
        line = raw_line.strip()
        if not line or line.startswith("#") or "=" not in line:
            continue
        key, value = line.split("=", 1)
        key = key.strip()
        value = value.strip().strip('"').strip("'")
        os.environ.setdefault(key, value)


def parse_ids(raw: str | None) -> set[int]:
    ids: set[int] = set()
    if not raw:
        return ids
    for part in raw.replace(" ", "").split(","):
        if part:
            ids.add(int(part))
    return ids


def env_int(name: str, default: int) -> int:
    try:
        return int(os.environ.get(name, str(default)))
    except ValueError:
        return default


def truncate_middle(text: str, limit: int = DISCORD_LIMIT) -> str:
    if len(text) <= limit:
        return text
    keep = max(200, (limit - 80) // 2)
    return f"{text[:keep]}\n\n... truncated ...\n\n{text[-keep:]}"


def short_task(text: str, limit: int = 64) -> str:
    cleaned = " ".join(text.split())
    return cleaned[:limit].rstrip() or "update"


class FlowDiscordAgent(discord.Client):
    def __init__(self, repo: Path, prefix: str, allowed_users: set[int], allowed_channels: set[int]) -> None:
        intents = discord.Intents.default()
        intents.message_content = True
        super().__init__(intents=intents)
        self.repo = repo
        self.prefix = prefix
        self.allowed_users = allowed_users
        self.allowed_channels = allowed_channels
        self.lock = asyncio.Lock()
        self.command_timeout = env_int("FLOW_AGENT_COMMAND_TIMEOUT_SECONDS", 600)
        self.codex_timeout = env_int("FLOW_AGENT_CODEX_TIMEOUT_SECONDS", 3600)
        self.required_branch = os.environ.get("FLOW_AGENT_REQUIRE_BRANCH", "main")
        self.allow_codex = os.environ.get("FLOW_AGENT_ALLOW_CODEX", "1").lower() in {"1", "true", "yes", "on"}

    async def on_ready(self) -> None:
        print(f"Flow Discord agent ready as {self.user} in {self.repo}", flush=True)

    async def on_message(self, message: discord.Message) -> None:
        if message.author.bot:
            return
        if not self.is_allowed(message):
            return

        content = message.content.strip()
        if not content.startswith(self.prefix):
            return

        rest = content[len(self.prefix) :].strip()
        if not rest:
            await self.send_help(message.channel)
            return

        command, _, arg = rest.partition(" ")
        command = command.lower()

        async with self.lock:
            try:
                await self.dispatch_command(message.channel, command, arg.strip())
            except Exception as exc:  # noqa: BLE001 - Discord boundary should never crash the daemon.
                await self.send_block(message.channel, "error", str(exc))

    def is_allowed(self, message: discord.Message) -> bool:
        if self.allowed_users and message.author.id not in self.allowed_users:
            return False
        if self.allowed_channels and message.channel.id not in self.allowed_channels:
            return False
        return True

    async def dispatch_command(self, channel: discord.abc.Messageable, command: str, arg: str) -> None:
        if command in {"help", "h"}:
            await self.send_help(channel)
        elif command == "ping":
            await channel.send("pong")
        elif command == "status":
            await self.status(channel)
        elif command == "build":
            await self.run_and_report(channel, "frontend build", ["npm", "--prefix", "frontend", "run", "build"])
        elif command == "pull":
            await self.pull(channel)
        elif command == "push":
            await self.push(channel)
        elif command == "deploy":
            await self.deploy(channel, arg or "Discord deploy")
        elif command in {"task", "codex"}:
            await self.codex_task(channel, arg)
        else:
            await self.send_block(channel, "unknown command", f"`{command}`\n\nUse `{self.prefix} help`.")

    async def send_help(self, channel: discord.abc.Messageable) -> None:
        body = f"""Commands
`{self.prefix} status` - show branch, dirty files, and recent commits
`{self.prefix} build` - run frontend production build
`{self.prefix} pull` - fast-forward local main from origin/main
`{self.prefix} push` - push committed local main to GitHub
`{self.prefix} deploy [message]` - build, commit local changes, and push
`{self.prefix} task <request>` - run Codex locally, build, commit, and push

Safety
Only configured Discord user IDs/channels are accepted.
Arbitrary shell commands are intentionally not supported."""
        await self.send_block(channel, "flow agent", body)

    async def status(self, channel: discord.abc.Messageable) -> None:
        code1, out1 = await self.run(["git", "status", "-sb"])
        code2, out2 = await self.run(["git", "log", "--oneline", "--decorate", "--max-count=6"])
        await self.send_block(channel, "status", f"$ git status -sb\n{out1}\n$ git log --oneline --decorate --max-count=6\n{out2}", code1 or code2)

    async def pull(self, channel: discord.abc.Messageable) -> None:
        dirty = await self.git_dirty()
        if dirty:
            await self.send_block(channel, "pull blocked", f"Working tree has local changes:\n{dirty}")
            return
        await self.ensure_branch()
        await self.run_and_report(channel, "pull", ["git", "pull", "--ff-only", "origin", self.required_branch])

    async def push(self, channel: discord.abc.Messageable) -> None:
        dirty = await self.git_dirty()
        if dirty:
            await self.send_block(channel, "push blocked", f"Commit or discard local changes first:\n{dirty}")
            return
        await self.ensure_branch()
        await self.run_and_report(channel, "push", ["git", "push", "origin", f"{self.required_branch}:{self.required_branch}"])

    async def deploy(self, channel: discord.abc.Messageable, message: str) -> None:
        await self.ensure_branch()
        dirty = await self.git_dirty()
        if dirty:
            code, build = await self.run(["npm", "--prefix", "frontend", "run", "build"], timeout=self.command_timeout)
            if code != 0:
                await self.send_block(channel, "build failed", build, code)
                return
            await self.run_or_raise(["git", "add", "-A"])
            still_dirty = await self.git_dirty()
            if still_dirty:
                commit_message = short_task(message, 72)
                code, out = await self.run(["git", "commit", "-m", commit_message])
                if code != 0:
                    await self.send_block(channel, "commit failed", out, code)
                    return
        await self.run_and_report(channel, "deploy push", ["git", "push", "origin", f"{self.required_branch}:{self.required_branch}"])

    async def codex_task(self, channel: discord.abc.Messageable, request: str) -> None:
        if not self.allow_codex:
            await self.send_block(channel, "codex disabled", "Set `FLOW_AGENT_ALLOW_CODEX=1` to enable this command.")
            return
        if not request:
            await self.send_block(channel, "missing request", f"Use `{self.prefix} task <what to change>`.")
            return
        if shutil.which("codex") is None:
            await self.send_block(channel, "codex missing", "`codex` CLI was not found in PATH.")
            return
        await self.ensure_branch()
        dirty = await self.git_dirty()
        if dirty:
            await self.send_block(channel, "task blocked", f"Working tree must be clean before Codex runs:\n{dirty}")
            return

        await channel.send(f"Starting Codex task: `{short_task(request, 120)}`")
        pull_code, pull_out = await self.run(["git", "pull", "--ff-only", "origin", self.required_branch])
        if pull_code != 0:
            await self.send_block(channel, "pull failed", pull_out, pull_code)
            return

        prompt = (
            "You are running inside the local Flow repository because the owner sent a Discord "
            "mobile command. Implement the request below. Keep changes scoped, preserve existing "
            "style, and run relevant checks when feasible. Do not commit or push; the Discord "
            "agent will build, commit, and push after you finish.\n\n"
            f"Request:\n{request}"
        )
        code, out = await self.run(
            ["codex", "exec", "-C", str(self.repo), "-s", "workspace-write", "-a", "never", prompt],
            timeout=self.codex_timeout,
        )
        if code != 0:
            await self.send_block(channel, "codex failed", out, code)
            return

        dirty_after = await self.git_dirty()
        if not dirty_after:
            await self.send_block(channel, "codex finished", f"No file changes detected.\n\n{truncate_middle(out, 1000)}")
            return

        build_code, build_out = await self.run(["npm", "--prefix", "frontend", "run", "build"], timeout=self.command_timeout)
        if build_code != 0:
            await self.send_block(channel, "build failed after codex", build_out, build_code)
            return

        await self.run_or_raise(["git", "add", "-A"])
        commit_message = f"Discord task: {short_task(request)}"
        commit_code, commit_out = await self.run(["git", "commit", "-m", commit_message])
        if commit_code != 0:
            await self.send_block(channel, "commit failed", commit_out, commit_code)
            return

        push_code, push_out = await self.run(["git", "push", "origin", f"{self.required_branch}:{self.required_branch}"])
        summary = (
            f"Codex output:\n{truncate_middle(out, 800)}\n\n"
            f"Build:\n{truncate_middle(build_out, 500)}\n\n"
            f"Commit:\n{commit_out}\n\n"
            f"Push:\n{push_out}"
        )
        await self.send_block(channel, "task deployed" if push_code == 0 else "push failed", summary, push_code)

    async def run_and_report(self, channel: discord.abc.Messageable, title: str, args: list[str]) -> None:
        code, out = await self.run(args)
        display = "$ " + " ".join(shlex.quote(part) for part in args) + "\n" + out
        await self.send_block(channel, title, display, code)

    async def git_dirty(self) -> str:
        code, out = await self.run(["git", "status", "--porcelain=v1"])
        if code != 0:
            raise RuntimeError(out)
        return out.strip()

    async def ensure_branch(self) -> None:
        if not self.required_branch:
            return
        code, out = await self.run(["git", "branch", "--show-current"])
        if code != 0:
            raise RuntimeError(out)
        branch = out.strip()
        if branch != self.required_branch:
            raise RuntimeError(f"Expected branch `{self.required_branch}`, but current branch is `{branch}`.")

    async def run_or_raise(self, args: list[str]) -> str:
        code, out = await self.run(args)
        if code != 0:
            raise RuntimeError(f"{' '.join(args)} failed:\n{out}")
        return out

    async def run(self, args: list[str], timeout: int | None = None) -> tuple[int, str]:
        proc = await asyncio.create_subprocess_exec(
            *args,
            cwd=str(self.repo),
            stdout=asyncio.subprocess.PIPE,
            stderr=asyncio.subprocess.STDOUT,
        )
        try:
            stdout, _ = await asyncio.wait_for(proc.communicate(), timeout or self.command_timeout)
        except asyncio.TimeoutError:
            proc.kill()
            stdout, _ = await proc.communicate()
            return 124, stdout.decode("utf-8", "replace") + "\nTimed out."
        return proc.returncode or 0, stdout.decode("utf-8", "replace")

    async def send_block(self, channel: discord.abc.Messageable, title: str, body: str, code: int = 0) -> None:
        prefix = "OK" if code == 0 else f"EXIT {code}"
        text = truncate_middle(body.strip() or "(no output)")
        await channel.send(f"**{prefix}: {title}**\n```text\n{text}\n```")


def main() -> None:
    parser = argparse.ArgumentParser(description="Run the Flow Discord control bot.")
    parser.add_argument("--env-file", default=".env.discord", help="Path to the Discord agent env file.")
    args = parser.parse_args()

    env_path = Path(args.env_file)
    if not env_path.is_absolute():
        env_path = ROOT / env_path
    load_env_file(env_path)

    token = os.environ.get("DISCORD_BOT_TOKEN")
    if not token:
        raise SystemExit("DISCORD_BOT_TOKEN is required. Copy .env.discord.example to .env.discord first.")

    allowed_users = parse_ids(os.environ.get("DISCORD_ALLOWED_USER_IDS"))
    if not allowed_users:
        raise SystemExit("DISCORD_ALLOWED_USER_IDS is required. Do not run this bot without a user allowlist.")

    repo = Path(os.environ.get("FLOW_AGENT_REPO", str(ROOT))).expanduser().resolve()
    allowed_channels = parse_ids(os.environ.get("DISCORD_ALLOWED_CHANNEL_IDS"))
    prefix = os.environ.get("FLOW_DISCORD_PREFIX", "!flow")

    client = FlowDiscordAgent(repo=repo, prefix=prefix, allowed_users=allowed_users, allowed_channels=allowed_channels)
    client.run(token)


if __name__ == "__main__":
    main()
