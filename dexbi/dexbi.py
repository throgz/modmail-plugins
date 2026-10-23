import asyncio
import copy
import typing

import aiohttp
import discord
from discord.ext import commands

from core import checks
from core.models import DummyMessage, PermissionLevel, getLogger

logger = getLogger(__name__)

API_URL = "https://api.dexbi.app/v1/messages"
TIMEOUT = aiohttp.ClientTimeout(total=60)
MAX_MESSAGE_CHARS = 4000

TICKET_SETTINGS = {
    "agent": "ticket",
    "reply": "smart",
    "escalation": True,
    "afterEscalation": "quiet",
    "smartClose": True,
    "suggestTitle": True,
}


class DexbiError(Exception):
    def __init__(self, kind, detail):
        super().__init__(detail)
        self.kind = kind
        self.detail = detail


def conversation_of(thread):
    return f"modmail-{thread.channel.id}"


def person_of(user):
    return {"id": str(user.id), "name": user.display_name[:100]}


def explain(error):
    if error.kind == "invalid_key":
        return f"{error.detail} Create a new key in the Dexbi dashboard and run `?dexbi key <key>`."
    return error.detail


class Dexbi(commands.Cog):
    """Dexbi answers your members in Modmail threads."""

    def __init__(self, bot):
        self.bot = bot
        self.db = bot.api.get_plugin_partition(self)
        self.config = {}
        self.last_error = None
        self.noted_errors = {}
        self.renamed = set()
        self.locks = {}

    async def cog_load(self):
        self.config = await self.db.find_one({"_id": "config"}) or {}

    @property
    def active(self):
        return bool(self.config.get("key")) and self.config.get("enabled", True)

    async def save(self, **changes):
        self.config.update(changes)
        await self.db.update_one({"_id": "config"}, {"$set": changes}, upsert=True)

    async def call_api(self, body):
        headers = {"Authorization": f"Bearer {self.config.get('key', '')}"}
        try:
            async with self.bot.session.post(API_URL, json=body, headers=headers, timeout=TIMEOUT) as resp:
                payload = await resp.json(content_type=None)
                status = resp.status
        except (aiohttp.ClientError, asyncio.TimeoutError, ValueError) as e:
            raise DexbiError("unreachable", "Dexbi can't be reached right now.") from e
        if status == 200 and isinstance(payload, dict):
            return payload
        error = payload.get("error", {}) if isinstance(payload, dict) else {}
        raise DexbiError(error.get("code", "internal"), error.get("message", f"Dexbi answered with HTTP {status}."))

    async def check_key(self, user):
        # staff messages are free, so this doesn't use up anything
        try:
            await self.call_api(
                {"conversation": "modmail-key-check", "member": person_of(user), "message": "Key check", "author": "staff"}
            )
        except DexbiError as e:
            return explain(e)
        return None

    async def ask(self, thread, body):
        try:
            return await self.call_api({"conversation": conversation_of(thread), "settings": TICKET_SETTINGS, **body})
        except DexbiError as e:
            await self.note_failure(thread, e.kind, f"Dexbi couldn't answer: {explain(e)}")
            return None

    async def note_failure(self, thread, kind, text):
        self.last_error = text
        logger.warning(text)
        noted = self.noted_errors.setdefault(thread.channel.id, set())
        if kind in noted:
            return
        noted.add(kind)
        await thread.channel.send(text, allowed_mentions=discord.AllowedMentions.none())

    @commands.Cog.listener()
    async def on_thread_reply(self, thread, from_mod, message, anonymous, plain):
        if not self.active or message.author.id == self.bot.user.id:
            return
        lock = self.locks.setdefault(thread.channel.id, asyncio.Lock())
        async with lock:
            if from_mod:
                await self.relay_staff_message(thread, message)
            else:
                await self.answer_member(thread, message)

    @commands.Cog.listener()
    async def on_thread_close(self, thread, closer, silent, delete_channel, message, scheduled):
        self.noted_errors.pop(thread.channel.id, None)
        self.renamed.discard(thread.channel.id)
        self.locks.pop(thread.channel.id, None)

    async def answer_member(self, thread, message):
        text = message.content.strip()
        if not text:
            return
        answer = await self.ask(
            thread,
            {"messageId": str(message.id), "member": person_of(message.author), "message": text[:MAX_MESSAGE_CHARS]},
        )
        if answer:
            await self.act(thread, message, answer)

    async def relay_staff_message(self, thread, message):
        text = await self.staff_words(message)
        if not text:
            return
        await self.ask(
            thread,
            {
                "messageId": str(message.id),
                "member": person_of(message.author),
                "message": text[:MAX_MESSAGE_CHARS],
                "author": "staff",
            },
        )

    async def staff_words(self, message):
        # Modmail 4.2+ passes the command message (`?reply hi`), older versions pass the reply text
        for ctx in await self.bot.get_contexts(message):
            if ctx.command is not None:
                words = ctx.view.read_rest().strip()
                if words:
                    return words
        if message.content.strip():
            return message.content.strip()
        return ", ".join(f"[file: {a.filename}]" for a in message.attachments)

    async def act(self, thread, member_message, answer):
        action, text = answer.get("action"), answer.get("message")
        if action in ("reply", "handover", "close") and text:
            await self.reply(thread, member_message, text)
        if action == "handover":
            await self.alert_staff(thread, answer.get("staffNote"))
        if answer.get("title"):
            await self.rename(thread, answer["title"])
        if action == "close":
            await thread.close(closer=thread.channel.guild.me)

    async def reply(self, thread, member_message, text):
        # thread.reply needs a message, so reply with a copy authored by the bot (like Modmail's thread menu)
        msg = DummyMessage(copy.copy(member_message))
        msg.author = thread.channel.guild.me
        msg.channel = thread.channel
        msg.content = text
        msg.stickers = []
        msg.embeds = []
        msg.message_snapshots = []
        await thread.reply(msg, text, anonymous=self.config.get("anonymous", False))

    async def alert_staff(self, thread, staff_note):
        role = thread.channel.guild.get_role(self.config.get("role_id") or 0)
        lines = [role.mention] if role else []
        lines.append("**Dexbi handed this thread to your team.**")
        if staff_note:
            lines.append(staff_note)
        mentions = discord.AllowedMentions(everyone=False, users=False, roles=[role] if role else False)
        await thread.channel.send("\n".join(lines), allowed_mentions=mentions)

    async def rename(self, thread, title):
        channel = thread.channel
        if channel.id in self.renamed:
            return
        self.renamed.add(channel.id)
        try:
            await channel.edit(name=f"{channel.name}-{title}"[:100], reason="Dexbi: thread title")
        except discord.HTTPException as e:
            await self.note_failure(thread, "rename", f"Dexbi couldn't rename this thread: {e.text or e}")

    @commands.group(invoke_without_command=True)
    @checks.has_permissions(PermissionLevel.ADMINISTRATOR)
    async def dexbi(self, ctx):
        """Dexbi answers your members in Modmail threads."""
        await ctx.send_help(ctx.command)

    @dexbi.command(name="key")
    @checks.has_permissions(PermissionLevel.ADMINISTRATOR)
    async def dexbi_key(self, ctx, key: str):
        """Save your Dexbi API key."""
        try:
            await ctx.message.delete()
            warning = ""
        except discord.HTTPException:
            warning = " I couldn't delete your message, please delete it yourself."
        await self.save(key=key.strip())
        problem = await self.check_key(ctx.author)
        if problem:
            await ctx.send(f"Key saved, but it doesn't work yet: {problem}{warning}")
        else:
            await ctx.send(f"Key saved. Dexbi now answers in your Modmail threads.{warning}")

    @dexbi.command(name="on")
    @checks.has_permissions(PermissionLevel.ADMINISTRATOR)
    async def dexbi_on(self, ctx):
        """Turn Dexbi on."""
        await self.save(enabled=True)
        await ctx.send("Dexbi is on." if self.config.get("key") else "Dexbi is on. Set a key with `?dexbi key <key>`.")

    @dexbi.command(name="off")
    @checks.has_permissions(PermissionLevel.ADMINISTRATOR)
    async def dexbi_off(self, ctx):
        """Turn Dexbi off."""
        await self.save(enabled=False)
        await ctx.send("Dexbi is off. Turn it back on with `?dexbi on`.")

    @dexbi.command(name="role")
    @checks.has_permissions(PermissionLevel.ADMINISTRATOR)
    async def dexbi_role(self, ctx, role: discord.Role = None):
        """Role to ping when Dexbi hands a thread over. Leave empty for no ping."""
        await self.save(role_id=role.id if role else None)
        await ctx.send(
            f"Dexbi pings {role.name} on a hand-over." if role else "Dexbi pings no one on a hand-over.",
            allowed_mentions=discord.AllowedMentions.none(),
        )

    @dexbi.command(name="anon")
    @checks.has_permissions(PermissionLevel.ADMINISTRATOR)
    async def dexbi_anon(self, ctx, setting: typing.Literal["on", "off"]):
        """Reply as anonymous staff (on) or as the bot (off)."""
        await self.save(anonymous=setting == "on")
        await ctx.send("Dexbi replies as anonymous staff." if setting == "on" else "Dexbi replies as the bot.")

    @dexbi.command(name="status")
    @checks.has_permissions(PermissionLevel.ADMINISTRATOR)
    async def dexbi_status(self, ctx):
        """Show Dexbi's settings and the last error."""
        if not self.config.get("key"):
            key = "not set, run `?dexbi key <key>`"
        else:
            problem = await self.check_key(ctx.author)
            key = f"doesn't work: {problem}" if problem else "works"
        role = ctx.guild.get_role(self.config.get("role_id") or 0) if ctx.guild else None
        lines = [
            f"Dexbi is **{'on' if self.config.get('enabled', True) else 'off'}**.",
            f"Key: {key}",
            f"Hand-over ping: {role.name if role else 'none'}",
            f"Replies as: {'anonymous staff' if self.config.get('anonymous') else 'the bot'}",
            f"Last error: {self.last_error or 'none'}",
        ]
        await ctx.send("\n".join(lines), allowed_mentions=discord.AllowedMentions.none())


async def setup(bot):
    await bot.add_cog(Dexbi(bot))
