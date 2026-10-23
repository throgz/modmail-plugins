import asyncio
import copy
import io
import re
import typing
import uuid
from urllib.parse import urlsplit

import aiohttp
import discord
from discord.ext import commands

from core import checks
from core.models import DummyMessage, PermissionLevel, getLogger

logger = getLogger(__name__)

API_URL = "https://api.dexbi.app/v1/messages"
DASHBOARD_URL = "https://dash.dexbi.app"
TIMEOUT = aiohttp.ClientTimeout(total=60)
MAX_IMAGES = 4
MAX_MESSAGE_CHARS = 4000
EMBED_LIMIT = 4096
COLOR = 0x3ECF8E

TICKET_SETTINGS = {
    "agent": "ticket",
    "reply": "smart",
    "escalation": True,
    "afterEscalation": "quiet",
    "smartClose": True,
    "suggestTitle": True,
}

HEADING = re.compile(r"^\s{0,3}#{1,6}\s+(.*?)\s*#*\s*$")
IMAGE = re.compile(r"!\[([^\]]*)\]\((\S+?)(?:\s+\"[^\"]*\")?\)")
IMAGE_TYPES = (".png", ".jpg", ".jpeg", ".gif", ".webp")
RULE = re.compile(r"^\s{0,3}([-*_])(\s*\1){2,}\s*$")
TABLE_DIVIDER = re.compile(r"^\|?\s*:?-{3,}:?\s*(\|\s*:?-{3,}:?\s*)*\|?\s*$")


def table_lines(rows):
    cells = [[cell.strip() for cell in row.strip().strip("|").split("|")] for row in rows]
    if len(cells) > 1 and TABLE_DIVIDER.match(rows[1]):
        header, body = cells[0], cells[2:]
        if body and all(len(row) == len(header) for row in body):
            return [", ".join(f"**{h}:** {v}" for h, v in zip(header, row)) for row in body]
        cells = [header] + body
    return [" | ".join(row) for row in cells]


def shows_inline(url):
    # Modmail only embeds links that end like an image file
    return urlsplit(url).path.lower().endswith(IMAGE_TYPES)


def image_link(match):
    if shows_inline(match.group(2)):
        return ""
    return f"[{match.group(1) or 'Image'}]({match.group(2)})"


def to_embed_text(markdown):
    """Dexbi answers in Markdown; embeds don't render headings, images or tables, so rewrite those."""
    lines, table, in_code = [], [], False
    for line in markdown.replace("\r\n", "\n").split("\n"):
        if line.strip().startswith("```"):
            in_code = not in_code
        if not in_code and line.strip().startswith("|"):
            table.append(line)
            continue
        if table:
            lines += table_lines(table)
            table = []
        if in_code or line.strip().startswith("```"):
            lines.append(line)
        elif HEADING.match(line):
            lines.append(f"**{HEADING.match(line).group(1).replace('**', '')}**")
        elif not RULE.match(line):
            text = IMAGE.sub(image_link, line).rstrip()
            if text or not IMAGE.search(line):
                lines.append(text)
    if table:
        lines += table_lines(table)
    return re.sub(r"\n{3,}", "\n\n", "\n".join(lines)).strip()


def image_urls(markdown):
    return [m.group(2) for m in IMAGE.finditer(markdown) if shows_inline(m.group(2))]


def image_name(alt, url, number):
    ext = urlsplit(url).path.rsplit(".", 1)[-1].lower()
    words = re.findall(r"[a-z0-9]+", alt.lower())[:6]
    return f"{'-'.join(words) or f'screenshot-{number}'}.{ext}"


def named_images(markdown):
    found = [(m.group(1), m.group(2)) for m in IMAGE.finditer(markdown) if shows_inline(m.group(2))]
    return [(image_name(alt, url, i), url) for i, (alt, url) in enumerate(found, 1)]


def split_text(text, limit=EMBED_LIMIT):
    if len(text) <= limit:
        return [text]
    for separator in ("\n\n", "\n", " "):
        cut = text.rfind(separator, 0, limit)
        if cut > 0:
            return [text[:cut].rstrip()] + split_text(text[cut:].lstrip(), limit)
    return [text[:limit]] + split_text(text[limit:], limit)


class DexbiError(Exception):
    def __init__(self, kind, detail):
        super().__init__(detail)
        self.kind = kind
        self.detail = detail


def conversation_of(thread):
    return f"modmail-{thread.channel.id}"


def person_of(user):
    return {"id": str(user.id), "name": user.display_name[:100]}


def explain(error, prefix):
    if error.kind == "invalid_key":
        return (
            f"The API key is wrong or was deleted. Create a new one in the [Dexbi dashboard]({DASHBOARD_URL}) "
            f"under **Settings > API keys**, then run `{prefix}dexbi key <key>`."
        )
    if error.kind == "unreachable":
        return "Dexbi can't be reached right now. It answers again once it's back."
    return error.detail


def make_embed(title, description=None):
    return discord.Embed(title=title, description=description, color=COLOR)


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
    def enabled(self):
        return bool(self.config.get("key")) and self.config.get("enabled", True)

    def paused(self, channel_id):
        return channel_id in self.config.get("paused", [])

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
            return explain(e, self.bot.prefix)
        return None

    async def ask(self, thread, body):
        try:
            return await self.call_api({"conversation": conversation_of(thread), "settings": TICKET_SETTINGS, **body})
        except DexbiError as e:
            await self.note_failure(thread, e.kind, "Dexbi couldn't answer", explain(e, self.bot.prefix))
            return None

    async def note_failure(self, thread, kind, title, text):
        self.last_error = f"{title}: {text}"
        logger.warning(self.last_error)
        noted = self.noted_errors.setdefault(thread.channel.id, set())
        if kind in noted:
            return
        noted.add(kind)
        embed = make_embed(title, text)
        embed.set_footer(text="Only your staff can see this. The member got no reply.")
        await thread.channel.send(embed=embed)

    @commands.Cog.listener()
    async def on_thread_reply(self, thread, from_mod, message, anonymous, plain):
        if not self.enabled or self.paused(thread.channel.id) or message.author.id == self.bot.user.id:
            return
        lock = self.locks.setdefault(thread.channel.id, asyncio.Lock())
        async with lock:
            if from_mod:
                await self.relay_staff_message(thread, message)
            else:
                await self.answer_member(thread, message)

    @commands.Cog.listener()
    async def on_thread_close(self, thread, closer, silent, delete_channel, message, scheduled):
        channel_id = thread.channel.id
        self.noted_errors.pop(channel_id, None)
        self.renamed.discard(channel_id)
        self.locks.pop(channel_id, None)
        if self.paused(channel_id):
            await self.save(paused=[c for c in self.config["paused"] if c != channel_id])

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

    async def reply(self, thread, member_message, markdown):
        files = await self.download_images(named_images(markdown))
        attachments = await self.host_images(files)
        text = to_embed_text(markdown)
        parts = split_text(text) if text else [""] if attachments else []
        for i, part in enumerate(parts):
            # thread.reply needs a message: reply with a copy authored by the bot user, which has no
            # roles, so Modmail's footer shows the bot's name
            msg = DummyMessage(copy.copy(member_message))
            msg.author = self.bot.user
            msg.channel = thread.channel
            msg.content = part
            msg.attachments = attachments if i == len(parts) - 1 else []
            msg.stickers = []
            msg.embeds = []
            msg.message_snapshots = []
            await thread.reply(msg, part, anonymous=self.config.get("anonymous", False))
        if files and not attachments:
            await self.send_files(thread, files)

    async def download_images(self, images):
        files = []
        for name, url in images[:MAX_IMAGES]:
            try:
                async with self.bot.session.get(url, timeout=TIMEOUT) as resp:
                    if resp.status != 200:
                        continue
                    data = await resp.read()
            except (aiohttp.ClientError, asyncio.TimeoutError):
                continue
            files.append((name, data))
        return files

    async def host_images(self, files):
        # Modmail shows a reply's attachments inside its embed, the same as when staff attach a file.
        # Upload the screenshots to the log channel so they have Discord links that always load.
        channel = getattr(self.bot, "log_channel", None)
        if not files or channel is None:
            return []
        try:
            message = await channel.send(
                "Screenshots from a Dexbi answer",
                files=[discord.File(io.BytesIO(data), filename=name) for name, data in files],
            )
        except discord.HTTPException:
            return []
        return message.attachments

    async def send_files(self, thread, files):
        for target in (thread.recipient, thread.channel):
            if target is None:
                continue
            try:
                await target.send(files=[discord.File(io.BytesIO(data), filename=name) for name, data in files])
            except discord.HTTPException:
                pass

    async def alert_staff(self, thread, staff_note):
        role = thread.channel.guild.get_role(self.config.get("role_id") or 0)
        embed = make_embed("Dexbi handed this thread to your team", staff_note or None)
        embed.set_footer(text="Dexbi stays quiet in this thread now.")
        mentions = discord.AllowedMentions(everyone=False, users=False, roles=[role] if role else False)
        await thread.channel.send(role.mention if role else None, embed=embed, allowed_mentions=mentions)

    async def rename(self, thread, title):
        channel = thread.channel
        if channel.id in self.renamed:
            return
        self.renamed.add(channel.id)
        try:
            await channel.edit(name=f"{channel.name}-{title}"[:100], reason="Dexbi: thread title")
        except discord.Forbidden:
            await self.note_failure(
                thread, "rename", "Dexbi couldn't rename this thread", "Give the bot the Manage Channels permission."
            )
        except discord.HTTPException as e:
            await self.note_failure(thread, "rename", "Dexbi couldn't rename this thread", e.text or str(e))

    async def status_embed(self, ctx):
        p = ctx.prefix
        if not self.config.get("key"):
            return self.setup_embed(ctx)
        problem = await self.check_key(ctx.author)
        role = ctx.guild.get_role(self.config.get("role_id") or 0) if ctx.guild else None
        embed = make_embed("Dexbi", "Dexbi answers your members in Modmail threads.")
        embed.add_field(name="Status", value="On" if self.config.get("enabled", True) else f"Off (`{p}dexbi on`)")
        embed.add_field(name="API key", value=f"Not working: {problem}" if problem else "Working")
        embed.add_field(name="Hand-over ping", value=role.mention if role else "No one")
        embed.add_field(name="Replies as", value="Anonymous staff" if self.config.get("anonymous") else "The bot")
        embed.add_field(name="Paused threads", value=str(len(self.config.get("paused", []))))
        embed.add_field(name="Last error", value=(self.last_error or "None")[:1024], inline=False)
        embed.add_field(
            name="Commands",
            value="\n".join(
                [
                    f"`{p}dexbi key <key>` set your API key",
                    f"`{p}dexbi on` / `{p}dexbi off` turn Dexbi on or off",
                    f"`{p}dexbi role @Support` ping a role on hand-over (`none` to stop)",
                    f"`{p}dexbi style bot` / `anonymous` how replies look",
                    f"`{p}dexbi test How do refunds work?` try an answer here",
                    f"`{p}dexbi pause` / `{p}dexbi resume` stop or restart Dexbi in one thread",
                    f"`{p}dexbi setup` the setup steps",
                ]
            ),
            inline=False,
        )
        return embed

    def setup_embed(self, ctx):
        p = ctx.prefix
        return make_embed(
            "Set up Dexbi",
            f"1. Open the [Dexbi dashboard]({DASHBOARD_URL}), go to **Settings > API keys** and create a key.\n"
            f"2. Run `{p}dexbi key <your key>` here. Your message gets deleted right away.\n"
            f"3. Done. Dexbi answers new messages in your Modmail threads.\n\n"
            f"Run `{p}dexbi` any time to see the status and all commands.",
        )

    async def cog_command_error(self, ctx, error):
        if isinstance(error, commands.CheckFailure):
            level = self.bot.command_perm(ctx.command.qualified_name).name.title()
            await ctx.send(
                embed=make_embed(
                    "No permission",
                    f"`{ctx.prefix}{ctx.command.qualified_name}` needs Modmail's **{level}** permission level. "
                    "Ask an admin or the bot owner.",
                )
            )
        elif isinstance(error, commands.UserInputError):
            usage = f"{ctx.prefix}{ctx.command.qualified_name} {ctx.command.signature}".strip()
            await ctx.send(embed=make_embed("Not quite", f"Usage: `{usage}`\n{ctx.command.help or ''}"))
        else:
            await self.bot.on_command_error(ctx, error, unhandled_by_cog=True)

    async def thread_here(self, ctx):
        thread = await self.bot.threads.find(channel=ctx.channel)
        if thread is None:
            await ctx.send(embed=make_embed("Not a thread", "Run this inside a Modmail thread."))
        return thread

    @commands.group(invoke_without_command=True)
    @checks.has_permissions(PermissionLevel.SUPPORTER)
    async def dexbi(self, ctx):
        """Show Dexbi's status and commands."""
        await ctx.send(embed=await self.status_embed(ctx))

    @dexbi.command(name="status")
    @checks.has_permissions(PermissionLevel.SUPPORTER)
    async def dexbi_status(self, ctx):
        """Show Dexbi's status and commands."""
        await ctx.send(embed=await self.status_embed(ctx))

    @dexbi.command(name="setup")
    @checks.has_permissions(PermissionLevel.SUPPORTER)
    async def dexbi_setup(self, ctx):
        """Show the setup steps."""
        await ctx.send(embed=self.setup_embed(ctx))

    @dexbi.command(name="key")
    @checks.has_permissions(PermissionLevel.ADMINISTRATOR)
    async def dexbi_key(self, ctx, key: str):
        """Set your Dexbi API key."""
        try:
            await ctx.message.delete()
            warning = ""
        except discord.HTTPException:
            warning = "\n\nI couldn't delete your message. Please delete it yourself, the key is secret."
        await self.save(key=key.strip())
        problem = await self.check_key(ctx.author)
        if problem:
            embed = make_embed("Key saved, but it doesn't work", f"{problem}{warning}")
        else:
            embed = make_embed("You're all set", f"Dexbi now answers in your Modmail threads.{warning}")
        await ctx.send(embed=embed)

    @dexbi.command(name="on")
    @checks.has_permissions(PermissionLevel.ADMINISTRATOR)
    async def dexbi_on(self, ctx):
        """Turn Dexbi on."""
        await self.save(enabled=True)
        if self.config.get("key"):
            await ctx.send(embed=make_embed("Dexbi is on", "Dexbi answers in your Modmail threads."))
        else:
            await ctx.send(embed=self.setup_embed(ctx))

    @dexbi.command(name="off")
    @checks.has_permissions(PermissionLevel.ADMINISTRATOR)
    async def dexbi_off(self, ctx):
        """Turn Dexbi off."""
        await self.save(enabled=False)
        await ctx.send(embed=make_embed("Dexbi is off", f"Turn it back on with `{ctx.prefix}dexbi on`."))

    @dexbi.command(name="role")
    @checks.has_permissions(PermissionLevel.ADMINISTRATOR)
    async def dexbi_role(self, ctx, role: typing.Union[discord.Role, typing.Literal["none"]]):
        """Role to ping when Dexbi hands a thread over, or none."""
        if role == "none":
            await self.save(role_id=None)
            await ctx.send(embed=make_embed("Hand-over ping off", "Dexbi won't ping anyone on a hand-over."))
        else:
            await self.save(role_id=role.id)
            await ctx.send(embed=make_embed("Hand-over ping set", f"Dexbi pings {role.mention} on a hand-over."))

    @dexbi.command(name="style")
    @checks.has_permissions(PermissionLevel.ADMINISTRATOR)
    async def dexbi_style(self, ctx, style: typing.Literal["bot", "anonymous"]):
        """Reply as the bot or as anonymous staff."""
        await self.save(anonymous=style == "anonymous")
        await ctx.send(embed=make_embed("Reply style saved", f"Dexbi replies as {'anonymous staff' if style == 'anonymous' else 'the bot'}."))

    @dexbi.command(name="anon", hidden=True)
    @checks.has_permissions(PermissionLevel.ADMINISTRATOR)
    async def dexbi_anon(self, ctx, setting: typing.Literal["on", "off"]):
        """Same as style: on is anonymous, off is bot."""
        await self.dexbi_style.callback(self, ctx, "anonymous" if setting == "on" else "bot")

    @dexbi.command(name="test")
    @checks.has_permissions(PermissionLevel.ADMINISTRATOR)
    async def dexbi_test(self, ctx, *, question: str):
        """Ask Dexbi something and see the answer here. No member gets a message."""
        if not self.config.get("key"):
            return await ctx.send(embed=self.setup_embed(ctx))
        async with ctx.typing():
            try:
                answer = await self.call_api(
                    {
                        "conversation": f"modmail-test-{uuid.uuid4().hex}",
                        "member": person_of(ctx.author),
                        "message": question[:MAX_MESSAGE_CHARS],
                        "settings": TICKET_SETTINGS,
                    }
                )
            except DexbiError as e:
                return await ctx.send(embed=make_embed("Dexbi couldn't answer", explain(e, ctx.prefix)))
        text = to_embed_text(answer.get("message") or "") or "*(no message)*"
        embed = make_embed("Test answer", split_text(text)[0])
        embed.add_field(name="Action", value=answer.get("action", "none"))
        if answer.get("staffNote"):
            embed.add_field(name="Staff note", value=answer["staffNote"][:1024], inline=False)
        if answer.get("title"):
            embed.add_field(name="Suggested title", value=answer["title"][:1024])
        images = image_urls(answer.get("message") or "")
        if images:
            embed.set_image(url=images[0])
        embed.set_footer(text="Only shown here. This counts as one AI reply.")
        await ctx.send(embed=embed)

    @dexbi.command(name="pause")
    @checks.has_permissions(PermissionLevel.SUPPORTER)
    async def dexbi_pause(self, ctx):
        """Stop Dexbi in this thread."""
        thread = await self.thread_here(ctx)
        if thread is None:
            return
        if not self.paused(thread.channel.id):
            await self.save(paused=self.config.get("paused", []) + [thread.channel.id])
        await ctx.send(embed=make_embed("Dexbi paused here", f"Dexbi won't answer in this thread. `{ctx.prefix}dexbi resume` to undo."))

    @dexbi.command(name="resume")
    @checks.has_permissions(PermissionLevel.SUPPORTER)
    async def dexbi_resume(self, ctx):
        """Let Dexbi answer in this thread again."""
        thread = await self.thread_here(ctx)
        if thread is None:
            return
        await self.save(paused=[c for c in self.config.get("paused", []) if c != thread.channel.id])
        await ctx.send(embed=make_embed("Dexbi resumed here", "Dexbi answers new messages in this thread again."))


async def setup(bot):
    await bot.add_cog(Dexbi(bot))
