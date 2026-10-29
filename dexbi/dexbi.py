import asyncio
import copy
import io
import re
import time
import uuid
from types import SimpleNamespace
from urllib.parse import urlsplit

import aiohttp
import discord
from discord.ext import commands

from core import checks
from core.models import DummyMessage, PermissionLevel, getLogger

__version__ = "1.3.0"

logger = getLogger(__name__)

API_URL = "https://api.dexbi.app/v1"
DASHBOARD_URL = "https://dash.dexbi.app"
TIMEOUT = aiohttp.ClientTimeout(total=60)
MAX_IMAGES = 4
MAX_MESSAGE_CHARS = 4000
EMBED_LIMIT = 4096
COLOR = 0x3ECF8E
DEFAULT_WAIT = 5
CHECK_IN_SECONDS = 600
PLUGIN_PATH = "throgz/modmail-plugins/dexbi"
# `?dexbi pause <length>`: minutes, and how the embeds say it
PAUSE_LENGTHS = {"30m": (30, "30 minutes"), "1h": (60, "1 hour"), "24h": (1440, "24 hours")}

TICKET_SETTINGS = {
    "agent": "ticket",
    "reply": "smart",
    "escalation": True,
    "afterEscalation": "quiet",
    "smartClose": True,
    "suggestTitle": True,
}

HEADING = re.compile(r"^\s{0,3}#{1,6}\s+(.*?)\s*#*\s*$")
# ![alt](url), and [text](url) too: the model sometimes links a screenshot instead of showing it
IMAGE = re.compile(r"!?\[([^\]]*)\]\((\S+?)(?:\s+\"[^\"]*\")?\)")
IMAGE_TYPES = (".png", ".jpg", ".jpeg", ".gif", ".webp")
# a bare link to an image, not part of a markdown link or image
BARE_IMAGE = re.compile(r"(?<![(\[<])https?://\S+?\.(?:png|jpe?g|gif|webp)(?:\?\S*)?(?=[\s)>.,]*(?:\s|$))", re.IGNORECASE)
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
            text = BARE_IMAGE.sub("", IMAGE.sub(image_link, line)).rstrip()
            if text or not (IMAGE.search(line) or BARE_IMAGE.search(line)):
                lines.append(text)
    if table:
        lines += table_lines(table)
    return re.sub(r"\n{3,}", "\n\n", "\n".join(lines)).strip()


def is_image(attachment):
    return urlsplit(attachment.url).path.lower().endswith(IMAGE_TYPES)


def readable(message):
    # a file on its own gives Dexbi nothing to read
    return bool(message.content.strip()) or any(is_image(a) for a in message.attachments)


def member_body(messages):
    """One request for a member's messages: their words, file names and screenshots."""
    texts, images = [], []
    for message in messages:
        images += [a.url for a in message.attachments if is_image(a)]
        files = [f"[file: {a.filename}]" for a in message.attachments if not is_image(a)]
        texts.append("\n".join([message.content.strip(), *files]).strip())
    last = messages[-1]
    text = "\n".join(filter(None, texts))
    body = {"messageId": str(last.id), "member": person_of(last.author), "message": text[:MAX_MESSAGE_CHARS]}
    if images:
        body["images"] = images[:MAX_IMAGES]
    return body


def image_name(alt, url, number):
    ext = urlsplit(url).path.rsplit(".", 1)[-1].lower()
    words = [] if alt.startswith("http") else re.findall(r"[a-z0-9]+", alt.lower())[:6]
    return f"{'-'.join(words) or f'screenshot-{number}'}.{ext}"


def named_images(markdown):
    found = [(m.group(1), m.group(2)) for m in IMAGE.finditer(markdown) if shows_inline(m.group(2))]
    found += [("", m.group(0)) for m in BARE_IMAGE.finditer(IMAGE.sub("", markdown))]
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


def explain(error, prefix, connected=False):
    if error.kind == "invalid_key" and connected:
        return (
            f"This bot is disconnected from Dexbi. To reconnect, open **Deploy** in the [Dexbi dashboard]({DASHBOARD_URL}), "
            f"open your Modmail deployment and click **Reconnect**, then run the `{prefix}dexbi connect` command it shows."
        )
    if error.kind == "invalid_key":
        return (
            f"The API key is wrong or was deleted. Create a new one in the [Dexbi dashboard]({DASHBOARD_URL}) "
            f"under **Settings > API keys**, then run `{prefix}dexbi key <key>`."
        )
    if error.kind == "unreachable":
        return "Dexbi can't be reached right now. It answers again once it's back."
    return error.detail


class NameTag:
    def __init__(self, name):
        self.name = name
        self.position = 0
        self.hoist = True

    def __str__(self):
        return self.name


class ReplyAuthor:
    """The author of Dexbi's replies: the bot, or the sender Dexbi names (the plan's branding).

    Modmail puts the author's top role in a reply's footer. The bot has no roles there:
    Modmail 4.2 falls back to its name and 4.0-4.1 fail, so give it its own name as the role.
    """

    def __init__(self, user, sender=None):
        self._user = user
        self._name = (sender or {}).get("name")
        icon = (sender or {}).get("iconUrl")
        if self._name:
            self.name = self.display_name = self._name
        if icon:
            # Modmail shows a server member's own avatar, so this author can't be one
            self.id = 0
            self.display_avatar = SimpleNamespace(url=icon)
        self.roles = [NameTag(self._name or user.display_name)]

    def __getattr__(self, name):
        return getattr(self._user, name)

    def __str__(self):
        return self._name or str(self._user)


def make_embed(title, description=None):
    return discord.Embed(title=title, description=description, color=COLOR)


def pause_length(argument):
    length = argument.strip().lower()
    if length not in PAUSE_LENGTHS:
        raise commands.BadArgument("Pick 30m, 1h or 24h, or leave it out to pause until you resume.")
    return length


def invoked_command(ctx):
    """The command a message runs, down to its subcommand (`plugins add`)."""
    command = ctx.command
    while isinstance(command, commands.Group):
        ctx.view.skip_ws()
        subcommand = command.get_command(ctx.view.get_word())
        if subcommand is None:
            break
        command = subcommand
    return command


def installed_entry(installed, typed):
    """The installed plugin `?plugins add <typed>` names when it's this one, read the way Modmail reads it."""
    name = typed.strip()
    if "@" not in name:
        name += "@master"
    return name if name in installed and name.split("@", 1)[0] == PLUGIN_PATH else None


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
        # thread channel id -> the countdown and the member messages waiting for staff
        self.waits = {}
        self.checking_in = None
        # who replies are sent as, from the last answer
        self.branding = None

    async def cog_load(self):
        self.config = await self.db.find_one({"_id": "config"}) or {}
        self.checking_in = asyncio.ensure_future(self.keep_checking_in())

    async def cog_unload(self):
        if self.checking_in:
            self.checking_in.cancel()

    @property
    def enabled(self):
        return bool(self.config.get("key"))

    @property
    def deployment(self):
        # the dashboard deployment this bot is connected to, None with a manual key
        return self.config.get("deployment")

    def paused(self, channel_id):
        if channel_id not in self.config.get("paused", []):
            return False
        # a timed pause keeps its end, so it lifts itself, also across restarts
        ends = self.config.get("pause_ends", {}).get(str(channel_id))
        return ends is None or ends > time.time()

    def paused_threads(self):
        return [c for c in self.config.get("paused", []) if self.paused(c)]

    async def pause(self, channel_id, minutes=None):
        paused, ends = self.pauses_without(channel_id)
        if minutes:
            ends[str(channel_id)] = time.time() + minutes * 60
        await self.save(paused=paused + [channel_id], pause_ends=ends)

    async def lift_pause(self, channel_id):
        if channel_id in self.config.get("paused", []):
            paused, ends = self.pauses_without(channel_id)
            await self.save(paused=paused, pause_ends=ends)

    def pauses_without(self, channel_id):
        # the pauses still running, minus this thread's
        paused = [c for c in self.paused_threads() if c != channel_id]
        ends = {k: v for k, v in self.config.get("pause_ends", {}).items() if int(k) in paused}
        return paused, ends

    async def save(self, **changes):
        self.config.update(changes)
        await self.db.update_one({"_id": "config"}, {"$set": changes}, upsert=True)

    async def call(self, method, path, body=None, auth=True):
        headers = {"X-Dexbi-Plugin": f"modmail/{__version__}"}
        if auth:
            headers["Authorization"] = f"Bearer {self.config.get('key', '')}"
        try:
            async with self.bot.session.request(
                method, f"{API_URL}/{path}", json=body, headers=headers, timeout=TIMEOUT
            ) as resp:
                payload = await resp.json(content_type=None)
                status = resp.status
        except (aiohttp.ClientError, asyncio.TimeoutError, ValueError) as e:
            raise DexbiError("unreachable", "Dexbi can't be reached right now.") from e
        if status == 200 and isinstance(payload, dict):
            return payload
        error = payload.get("error", {}) if isinstance(payload, dict) else {}
        kind = error.get("code") or ("not_found" if status == 404 else "internal")
        raise DexbiError(kind, error.get("message", f"Dexbi answered with HTTP {status}."))

    async def control(self, thread, action):
        # resume also takes back a thread Dexbi escalated, or couldn't answer while out of replies
        return await self.call("POST", f"conversations/{action}", {"conversation": conversation_of(thread)})

    async def call_api(self, body):
        answer = await self.call("POST", "messages", body)
        if "deployment" in answer:
            await self.remember(answer["deployment"])
        if "branding" in answer:
            self.branding = answer["branding"]
        return answer

    async def remember(self, deployment):
        if deployment != self.deployment:
            await self.save(deployment=deployment)

    async def check_in(self):
        # lets the dashboard see this bot, and picks up changes made there
        if not self.enabled:
            return
        try:
            await self.remember((await self.call("GET", "modmail/status")).get("deployment"))
        except DexbiError as e:
            if e.kind != "not_found":
                self.last_error = f"Dexbi check-in failed: {explain(e, self.bot.prefix, bool(self.deployment))}"
                logger.warning(self.last_error)

    async def keep_checking_in(self):
        while True:
            await self.check_in()
            await asyncio.sleep(CHECK_IN_SECONDS)

    async def check_key(self, user):
        # staff messages are free, so this doesn't use up anything
        try:
            await self.call_api(
                {"conversation": "modmail-key-check", "member": person_of(user), "message": "Key check", "author": "staff"}
            )
        except DexbiError as e:
            return e
        return None

    # A connected bot gets these from the dashboard. The stored values are for servers set up with `?dexbi key`.
    def ticket_settings(self):
        return {**TICKET_SETTINGS, "teamWaitMinutes": self.config.get("wait", DEFAULT_WAIT)}

    def replies_anonymously(self):
        if self.deployment:
            return self.deployment.get("replyAs") == "anonymous"
        return self.config.get("anonymous", False)

    def replies_as(self):
        branding = self.branding or {}
        if branding.get("required"):
            return f"{branding.get('name')} (free plan)"
        if self.replies_anonymously():
            return "Anonymous staff"
        return branding.get("name") or "The bot"

    def handover_roles(self, guild):
        ids = self.deployment.get("handoverRoleIds", []) if self.deployment else [self.config.get("role_id")]
        return [role for role in (guild.get_role(int(i)) for i in ids if i) if role]

    async def ask(self, thread, body):
        try:
            return await self.call_api({"conversation": conversation_of(thread), "settings": self.ticket_settings(), **body})
        except DexbiError as e:
            explanation = explain(e, self.bot.prefix, bool(self.deployment))
            await self.note_failure(thread, e.kind, "Dexbi couldn't answer", explanation)
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
    async def on_message(self, message):
        # Adding the plugin again only gets "This plugin is already installed." from Modmail.
        # Update it then, so step one of the setup always leaves the newest version.
        if message.author.bot or message.guild is None:
            return
        for ctx in await self.bot.get_contexts(message):
            if getattr(invoked_command(ctx), "qualified_name", None) == "plugins add":
                return await self.update_when_added_again(ctx)

    async def update_when_added_again(self, ctx):
        installed = installed_entry(self.bot.config["plugins"], ctx.view.read_rest())
        update = self.bot.get_command("plugins update")
        if installed is None or update is None:
            return
        try:
            if not await update.can_run(ctx):
                return
        except commands.CommandError:
            return
        # Modmail's own update reloads this cog; nothing here runs after it
        try:
            await ctx.invoke(update, plugin_name=installed)
        except Exception:
            logger.warning("Updating the Dexbi plugin failed.", exc_info=True)

    @commands.Cog.listener()
    async def on_thread_reply(self, thread, from_mod, message, anonymous, plain):
        if not self.enabled or self.sent_by_dexbi(message):
            return
        lock = self.locks.setdefault(thread.channel.id, asyncio.Lock())
        async with lock:
            # staff replies are still sent while paused, so Dexbi knows what they said when it's back
            if from_mod:
                await self.relay_staff_message(thread, message)
            elif not self.paused(thread.channel.id):
                await self.answer_member(thread, message)

    def sent_by_dexbi(self, message):
        return isinstance(message.author, ReplyAuthor) or message.author.id == self.bot.user.id

    @commands.Cog.listener()
    async def on_thread_close(self, thread, closer, silent, delete_channel, message, scheduled):
        channel_id = thread.channel.id
        self.stop_waiting(channel_id)
        self.noted_errors.pop(channel_id, None)
        self.renamed.discard(channel_id)
        self.locks.pop(channel_id, None)
        await self.lift_pause(channel_id)

    async def answer_member(self, thread, message):
        if not readable(message):
            return
        answer = await self.ask(thread, member_body([message]))
        if not answer:
            return
        if answer.get("teamWaitSeconds"):
            self.wait_for_staff(thread, message, answer["teamWaitSeconds"])
        elif answer.get("action") != "none":
            self.stop_waiting(thread.channel.id)
        await self.act(thread, message, answer)

    def wait_for_staff(self, thread, message, seconds):
        # staff replied last: Dexbi answers only if they don't reply within the wait
        wait = self.waits.get(thread.channel.id)
        if wait:
            wait["messages"].append(message)
            return
        task = asyncio.ensure_future(self.answer_after_wait(thread, seconds))
        self.waits[thread.channel.id] = {"task": task, "messages": [message]}

    def stop_waiting(self, channel_id):
        wait = self.waits.pop(channel_id, None)
        if wait:
            wait["task"].cancel()

    async def answer_after_wait(self, thread, seconds):
        await asyncio.sleep(seconds)
        channel_id = thread.channel.id
        async with self.locks.setdefault(channel_id, asyncio.Lock()):
            wait = self.waits.pop(channel_id, None)
            if not wait or not self.enabled or self.paused(channel_id):
                return
            answer = await self.ask(thread, {**member_body(wait["messages"]), "teamWaitOver": True})
            if answer:
                await self.act(thread, wait["messages"][-1], answer)

    async def relay_staff_message(self, thread, message):
        self.stop_waiting(thread.channel.id)
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
            await self.reply(thread, member_message, text, answer.get("branding"))
        if action == "handover":
            await self.alert_staff(thread, answer.get("staffNote"))
        if answer.get("title"):
            await self.rename(thread, answer["title"])
        if action == "close":
            await thread.close(closer=thread.channel.guild.me)

    async def reply(self, thread, member_message, markdown, branding=None):
        files = await self.download_images(named_images(markdown))
        attachments = await self.host_images(files)
        text = to_embed_text(markdown)
        parts = split_text(text) if text else [""] if attachments else []
        # on the free plan replies are always sent as Dexbi, never anonymously
        anonymous = self.replies_anonymously() and not (branding or {}).get("required")
        sender = None if anonymous else branding
        sent = None
        for i, part in enumerate(parts):
            # thread.reply needs a message: reply with a copy authored by the bot
            msg = DummyMessage(copy.copy(member_message))
            msg.author = ReplyAuthor(self.bot.user, sender)
            msg.channel = thread.channel
            msg.content = part
            msg.attachments = attachments[:1] if i == len(parts) - 1 else []
            msg.stickers = []
            msg.embeds = []
            msg.message_snapshots = []
            sent = await thread.reply(msg, anonymous=anonymous)
        if files and not attachments:
            await self.send_files(thread, files)
        elif len(attachments) > 1 and not await self.add_to_gallery(sent, attachments):
            await self.send_files(thread, files[1:])

    async def add_to_gallery(self, sent, attachments):
        # Discord shows embeds that share a url as one card with an image grid
        if not isinstance(sent, tuple):
            return False
        user_messages, thread_message = sent
        messages = [m for m in [*(user_messages or []), thread_message] if m and m.embeds]
        if not messages:
            return False
        for message in messages:
            main = message.embeds[0]
            main.url = attachments[0].url
            extra = [discord.Embed(url=main.url).set_image(url=a.url) for a in attachments[1:]]
            try:
                await message.edit(embeds=[main, *extra])
            except discord.HTTPException:
                return False
        return True

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
        roles = self.handover_roles(thread.channel.guild)
        embed = make_embed("Dexbi escalated this thread to your team", staff_note or None)
        embed.set_footer(text=f"Dexbi stays quiet in this thread now. {self.bot.prefix}dexbi resume gives it back to Dexbi.")
        mentions = discord.AllowedMentions(everyone=False, users=False, roles=roles or False)
        content = " ".join(role.mention for role in roles) or None
        await thread.channel.send(content, embed=embed, allowed_mentions=mentions)

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
        await self.check_in()
        error = await self.check_key(ctx.author)
        roles = self.handover_roles(ctx.guild) if ctx.guild else []
        if self.deployment and error and error.kind == "invalid_key":
            connection = "Disconnected"
        elif self.deployment:
            connection = f"Connected to **{self.deployment.get('name')}**"
        else:
            connection = "Manual API key"
        embed = make_embed("Dexbi", "Dexbi answers your members in Modmail threads.")
        embed.add_field(name="Connection", value=connection)
        embed.add_field(name="Check", value="Not working" if error else "Working")
        embed.add_field(name="Replies as", value=self.replies_as())
        embed.add_field(name="Hand-over ping", value=" ".join(r.mention for r in roles) or "No one")
        embed.add_field(name="Paused threads", value=str(len(self.paused_threads())))
        embed.add_field(name="Plugin", value=f"v{__version__}")
        # what's wrong now says how to fix it; otherwise show the last thing that went wrong
        if error:
            embed.add_field(name="What to do", value=explain(error, p, bool(self.deployment))[:1024], inline=False)
        else:
            embed.add_field(name="Last error", value=(self.last_error or "None")[:1024], inline=False)
        embed.add_field(
            name="Commands",
            value="\n".join(
                [
                    f"`{p}dexbi connect <code>` connect to your Dexbi dashboard",
                    f"`{p}dexbi test How do refunds work?` try an answer here",
                    f"`{p}dexbi pause [30m|1h|24h]` stop Dexbi in one thread, it still reads your replies",
                    f"`{p}dexbi resume` let Dexbi answer in one thread again, also after it escalated",
                    f"`{p}dexbi forget` start one thread fresh",
                    f"`{p}dexbi setup` the setup steps",
                    f"`{p}dexbi key <key>` use an API key instead",
                ]
            ),
            inline=False,
        )
        embed.set_footer(text="Change how Dexbi answers in the Dexbi dashboard, under Deploy.")
        return embed

    def setup_embed(self, ctx):
        p = ctx.prefix
        return make_embed(
            "Set up Dexbi",
            f"1. Open the [Dexbi dashboard]({DASHBOARD_URL}), go to **Deploy** and click **New** under Ticket agent. "
            f"Pick **Modmail**.\n"
            f"2. Run the `{p}dexbi connect <code>` command it shows here.\n"
            f"3. Done. Dexbi answers new messages in your Modmail threads.\n\n"
            f"Using an API key instead? Run `{p}dexbi key <key>`.",
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

    async def delete_command(self, ctx, secret):
        try:
            await ctx.message.delete()
            return ""
        except discord.HTTPException:
            return f"\n\nI couldn't delete your message. Please delete it yourself, the {secret} is secret."

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
        warning = await self.delete_command(ctx, "key")
        await self.save(key=key.strip(), deployment=None)
        error = await self.check_key(ctx.author)
        if error:
            embed = make_embed("Key saved, but it doesn't work", f"{explain(error, ctx.prefix)}{warning}")
        else:
            embed = make_embed("You're all set", f"Dexbi now answers in your Modmail threads.{warning}")
        await ctx.send(embed=embed)

    @dexbi.command(name="connect")
    @checks.has_permissions(PermissionLevel.ADMINISTRATOR)
    async def dexbi_connect(self, ctx, *, code: str):
        """Connect to Dexbi with the code from the dashboard (Deploy > New > Modmail)."""
        warning = await self.delete_command(ctx, "code")
        try:
            connected = await self.call("POST", "modmail/connect", {"code": code}, auth=False)
        except DexbiError as e:
            return await ctx.send(embed=make_embed("Couldn't connect", f"{explain(e, ctx.prefix)}{warning}"))
        await self.save(key=connected["key"], deployment=connected["deployment"])
        text = (
            f"Dexbi now answers in your Modmail threads as **{connected['deployment']['name']}**. "
            f"Change how it answers in the [Dexbi dashboard]({DASHBOARD_URL}) under **Deploy**.{warning}"
        )
        await ctx.send(embed=make_embed("Connected", text))

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
                        "settings": self.ticket_settings(),
                    }
                )
            except DexbiError as e:
                return await ctx.send(embed=make_embed("Dexbi couldn't answer", explain(e, ctx.prefix, bool(self.deployment))))
        text = to_embed_text(answer.get("message") or "") or "*(no message)*"
        embed = make_embed("Test answer", split_text(text)[0])
        embed.add_field(name="Action", value=answer.get("action", "none"))
        if answer.get("staffNote"):
            embed.add_field(name="Staff note", value=answer["staffNote"][:1024], inline=False)
        if answer.get("title"):
            embed.add_field(name="Suggested title", value=answer["title"][:1024])
        images = named_images(answer.get("message") or "")
        if images:
            embed.set_image(url=images[0][1])
        embed.set_footer(text="Only shown here. This counts as one AI reply.")
        await ctx.send(embed=embed)

    @dexbi.command(name="pause")
    @checks.has_permissions(PermissionLevel.SUPPORTER)
    async def dexbi_pause(self, ctx, length: pause_length = None):
        """Stop Dexbi in this thread until you resume it, or for 30m, 1h or 24h."""
        thread = await self.thread_here(ctx)
        if thread is None:
            return
        self.stop_waiting(thread.channel.id)
        minutes, label = PAUSE_LENGTHS.get(length, (None, None))
        await self.pause(thread.channel.id, minutes)
        until = f"for {label}" if label else "until you resume it"
        text = (
            f"Dexbi won't answer in this thread {until}. It still reads your replies, so it knows what you told "
            f"the member. `{ctx.prefix}dexbi resume` to undo."
        )
        await ctx.send(embed=make_embed("Dexbi paused here", text))

    @dexbi.command(name="resume")
    @checks.has_permissions(PermissionLevel.SUPPORTER)
    async def dexbi_resume(self, ctx):
        """Let Dexbi answer in this thread again, also after it escalated the thread to your team."""
        thread = await self.thread_here(ctx)
        if thread is None:
            return
        await self.lift_pause(thread.channel.id)
        if self.enabled:
            try:
                await self.control(thread, "resume")
            except DexbiError as e:
                text = f"Dexbi isn't paused here, but it couldn't take the thread back: {explain(e, ctx.prefix, bool(self.deployment))}"
                return await ctx.send(embed=make_embed("Dexbi couldn't resume", text))
        await ctx.send(embed=make_embed("Dexbi resumed here", "Dexbi answers new messages in this thread again."))

    @dexbi.command(name="forget")
    @checks.has_permissions(PermissionLevel.SUPPORTER)
    async def dexbi_forget(self, ctx):
        """Make Dexbi forget this thread so far and start fresh."""
        thread = await self.thread_here(ctx)
        if thread is None:
            return
        if not self.enabled:
            return await ctx.send(embed=self.setup_embed(ctx))
        self.stop_waiting(thread.channel.id)
        try:
            await self.control(thread, "forget")
        except DexbiError as e:
            return await ctx.send(embed=make_embed("Dexbi couldn't forget", explain(e, ctx.prefix, bool(self.deployment))))
        text = "Dexbi starts fresh with the next message here."
        if self.paused(thread.channel.id):
            text += f" It stays paused until you run `{ctx.prefix}dexbi resume`."
        await ctx.send(embed=make_embed("Dexbi forgot this thread", text))


async def setup(bot):
    await bot.add_cog(Dexbi(bot))
