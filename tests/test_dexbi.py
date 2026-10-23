import asyncio
from contextlib import asynccontextmanager
from types import SimpleNamespace

import discord
from discord.ext import commands
from discord.ext.commands.view import StringView

import dexbi

BOT_ID = 1
ROLE_ID = 55
OTHER_ROLE_ID = 56


def run(coro):
    return asyncio.run(coro)


async def no_wait(seconds):
    pass


dexbi.asyncio.sleep = no_wait


class FakeResponse:
    def __init__(self, status, payload):
        self.status, self.payload = status, payload

    async def json(self, content_type=None):
        return self.payload

    async def read(self):
        return self.payload

    async def __aenter__(self):
        return self

    async def __aexit__(self, *exc):
        return False


class FakeSession:
    def __init__(self):
        self.requests = []
        self.answers = []

    def get(self, url, *, timeout):
        self.requests.append({"url": url})
        return FakeResponse(404 if "missing" in url else 200, b"png-bytes")

    def request(self, method, url, *, json=None, headers=None, timeout=None):
        self.requests.append({"method": method, "url": url, "body": json, "headers": headers})
        status, payload = self.answers.pop(0) if self.answers else (200, answer("none"))
        return FakeResponse(status, payload)


class FakeCollection:
    def __init__(self):
        self.document = None

    async def find_one(self, query):
        return self.document

    async def update_one(self, query, update, upsert=False):
        self.document = {**(self.document or {}), **update["$set"]}


ROLE = SimpleNamespace(id=ROLE_ID, name="Support", mention=f"<@&{ROLE_ID}>")
OTHER_ROLE = SimpleNamespace(id=OTHER_ROLE_ID, name="Billing", mention=f"<@&{OTHER_ROLE_ID}>")
ROLES = {ROLE_ID: ROLE, OTHER_ROLE_ID: OTHER_ROLE}
GUILD = SimpleNamespace(me=SimpleNamespace(id=BOT_ID, roles=[]), get_role=ROLES.get)


class FakeChannel:
    def __init__(self, channel_id=900):
        self.id, self.name, self.guild = channel_id, "alex", GUILD
        self.sent = []

    async def send(self, content=None, embed=None, allowed_mentions=None, files=None):
        sent = SimpleNamespace(content=content, embed=embed, mentions=allowed_mentions, files=files,
                               attachments=[SimpleNamespace(filename=f.filename, url=f"https://cdn.discordapp.com/{f.filename}") for f in files or []])
        self.sent.append(sent)
        return sent

    async def edit(self, *, name, reason=None):
        self.name = name


class SentCard:
    def __init__(self):
        self.embeds = [dexbi.discord.Embed(description="answer")]

    async def edit(self, *, embeds):
        self.embeds = embeds


class FakeThread:
    def __init__(self):
        self.channel = FakeChannel()
        self.recipient = FakeChannel(901)
        self.replies = []
        self.cards = []
        self.closed_by = None

    # Modmail 4.0 and 4.1 take no content argument, so the text rides on the message
    async def reply(self, message, anonymous=False, plain=False):
        self.replies.append(SimpleNamespace(message=message, content=message.content, anonymous=anonymous))
        dm, copy = SentCard(), SentCard()
        self.cards += [dm, copy]
        return [dm], copy

    async def close(self, *, closer):
        self.closed_by = closer


class FakeContext:
    def __init__(self, channel, command="dexbi"):
        self.prefix = "?"
        self.author = SimpleNamespace(id=7, display_name="Sam")
        self.guild = GUILD
        self.channel = channel
        self.command = SimpleNamespace(qualified_name=command, signature="<key>", help="Set your Dexbi API key.")
        self.message = SimpleNamespace(deleted=False)
        self.sent = []

        async def delete():
            self.message.deleted = True

        self.message.delete = delete

    async def send(self, content=None, embed=None):
        self.sent.append(embed)

    @asynccontextmanager
    async def typing(self):
        yield

    @property
    def embed(self):
        return self.sent[-1]


def user(user_id, name):
    return SimpleNamespace(id=user_id, display_name=name)


def message(content, author_id=42, name="Alex"):
    return SimpleNamespace(id=777, content=content, author=user(author_id, name), attachments=[], stickers=["sticker"])


def answer(action, text=None, staff_note=None, title=None):
    return {"action": action, "message": text, "staffNote": staff_note, "title": title, "conversation": "modmail-900"}


def deployment(reply_as="bot", roles=()):
    return {"id": "dep-1", "name": "Modmail", "replyAs": reply_as, "handoverRoleIds": [str(r) for r in roles]}


async def get_contexts(message):
    view = StringView(message.content)
    if not view.skip_string("?"):
        return [SimpleNamespace(command=None, view=view)]
    word = view.get_word()
    return [SimpleNamespace(command="reply" if word == "reply" else None, view=view)]


def plugin(config=None, thread=None):
    session = FakeSession()
    thread = thread or FakeThread()

    async def find(channel):
        return thread if channel is thread.channel else None

    bot = SimpleNamespace(
        user=SimpleNamespace(id=BOT_ID, display_name="Dexbi"),
        session=session,
        api=SimpleNamespace(get_plugin_partition=lambda cog: FakeCollection()),
        get_contexts=get_contexts,
        threads=SimpleNamespace(find=find),
        log_channel=FakeChannel(902),
        command_perm=lambda name: dexbi.PermissionLevel.ADMINISTRATOR,
        prefix="?",
    )
    cog = dexbi.Dexbi(bot)
    cog.config = {"key": "dexbi_test", **(config or {})}
    return cog, session, thread


def member_says(cog, thread, text="I was charged twice"):
    run(cog.on_thread_reply(thread, False, message(text), False, False))


def staff_says(cog, thread, text):
    run(cog.on_thread_reply(thread, True, message(text, 7, "Sam"), False, False))


# translator


def test_headings_become_bold_lines():
    assert dexbi.to_embed_text("## Refunds\nText\n### **Steps**") == "**Refunds**\nText\n**Steps**"


def test_images_are_taken_out_of_the_text_to_be_shown_as_images():
    text = 'See this:\n![The settings page](https://cdn.example.com/a.webp)\n\n*The page.*\n![](https://cdn.example.com/b.PNG "B")'
    assert dexbi.to_embed_text(text) == "See this:\n\n*The page.*"
    assert [url for _, url in dexbi.named_images(text)] == ["https://cdn.example.com/a.webp", "https://cdn.example.com/b.PNG"]


def test_an_image_modmail_cannot_show_stays_a_link():
    text = "![Diagram](https://example.com/render?id=4)"
    assert dexbi.to_embed_text(text) == "[Diagram](https://example.com/render?id=4)"
    assert dexbi.named_images(text) == []


def test_tables_become_lines():
    table = "| Plan | Price |\n| --- | ---: |\n| Lite | $8.99 |\n| Pro | $14.99 |"
    assert dexbi.to_embed_text(table) == "**Plan:** Lite, **Price:** $8.99\n**Plan:** Pro, **Price:** $14.99"


def test_bold_italic_lists_links_and_code_are_kept():
    text = "**Bold** and *italic*\n- one\n1. two\n[docs](https://docs.dexbi.app)\n```\n# not a heading\n| not | a table |\n```"
    assert dexbi.to_embed_text(text) == text


def test_rules_and_extra_blank_lines_go():
    assert dexbi.to_embed_text("One\n\n\n\n---\n\nTwo\n") == "One\n\nTwo"


def test_long_text_splits_at_paragraphs():
    first, second = "a" * 3000, "b" * 3000
    assert dexbi.split_text(f"{first}\n\n{second}") == [first, second]
    parts = dexbi.split_text("x" * 9000)
    assert [len(p) for p in parts] == [4096, 4096, 808]


# what Dexbi is sent


def test_a_members_message_is_sent_as_a_smart_reply_ticket_turn():
    cog, session, thread = plugin()
    member_says(cog, thread)
    request = session.requests[0]
    assert request["url"] == "https://api.dexbi.app/v1/messages"
    assert request["headers"]["Authorization"] == "Bearer dexbi_test"
    assert request["body"] == {
        "conversation": "modmail-900",
        "messageId": "777",
        "member": {"id": "42", "name": "Alex"},
        "message": "I was charged twice",
        "settings": {**dexbi.TICKET_SETTINGS, "teamWaitMinutes": 5},
    }
    assert dexbi.TICKET_SETTINGS["reply"] == "smart" and dexbi.TICKET_SETTINGS["agent"] == "ticket"


def test_a_staff_reply_is_sent_so_dexbi_knows_staff_are_there():
    cog, session, thread = plugin()
    staff_says(cog, thread, "?reply I've refunded it")
    body = session.requests[0]["body"]
    assert body["author"] == "staff" and body["message"] == "I've refunded it"
    assert body["member"] == {"id": "7", "name": "Sam"}
    assert thread.replies == [] and thread.channel.sent == []


def test_an_older_modmail_hands_over_the_reply_text_itself():
    cog, session, thread = plugin()
    staff_says(cog, thread, "I've refunded it")
    assert session.requests[0]["body"]["message"] == "I've refunded it"


def test_dexbis_own_replies_are_never_sent_back():
    cog, session, thread = plugin()
    run(cog.on_thread_reply(thread, True, message("Here's how refunds work.", BOT_ID), False, False))
    assert session.requests == []


def test_nothing_is_sent_while_off_paused_without_a_key_or_without_words():
    for config in ({"key": None}, {"paused": [900]}):
        cog, session, thread = plugin(config)
        member_says(cog, thread)
        staff_says(cog, thread, "?reply hi")
        assert session.requests == []
    cog, session, thread = plugin()
    member_says(cog, thread, "   ")
    assert session.requests == []


def attached(content, *names):
    sent = message(content)
    sent.attachments = [SimpleNamespace(filename=n, url=f"https://cdn.discordapp.com/attachments/1/2/{n}?ex=1&is=2&hm=3&") for n in names]
    return sent


def test_a_members_screenshots_are_sent_as_images_and_other_files_as_names():
    cog, session, thread = plugin()
    run(cog.on_thread_reply(thread, False, attached("It breaks here", "error.PNG", "log.txt"), False, False))
    body = session.requests[0]["body"]
    assert body["message"] == "It breaks here\n[file: log.txt]"
    assert body["images"] == ["https://cdn.discordapp.com/attachments/1/2/error.PNG?ex=1&is=2&hm=3&"]


def test_a_screenshot_on_its_own_is_answered_but_a_file_on_its_own_is_not():
    cog, session, thread = plugin()
    run(cog.on_thread_reply(thread, False, attached("", "a.png", "b.jpg", "c.webp", "d.gif", "e.jpeg"), False, False))
    body = session.requests[0]["body"]
    assert body["message"] == "" and len(body["images"]) == dexbi.MAX_IMAGES
    run(cog.on_thread_reply(thread, False, attached("", "invoice.pdf"), False, False))
    assert len(session.requests) == 1


# what the plugin does with the answer


def test_reply_goes_to_the_member_as_the_bot_user():
    cog, session, thread = plugin()
    session.answers.append((200, answer("reply", "## Refunds\nThey take 5 days.")))
    member_says(cog, thread)
    [sent] = thread.replies
    assert sent.content == "**Refunds**\nThey take 5 days." and sent.anonymous is False
    # Modmail's footer shows the author's top hoisted role: here, the bot's name
    author = sent.message.author
    top = sorted(author.roles, key=lambda r: r.position, reverse=True)[0]
    assert author.id == cog.bot.user.id and top.hoist and str(top) == cog.bot.user.display_name
    # and the name above the reply (Modmail 4.0-4.1 print the author)
    assert str(author) == str(cog.bot.user)
    assert sent.message.channel is thread.channel
    assert sent.message.stickers == [] and sent.message.attachments == []


def test_images_ride_on_the_reply_like_staff_attachments_with_no_gap():
    cog, session, thread = plugin()
    reply = "**Steps**\n\n![Deploy](https://cdn.example.com/d.webp)\n\n1. Open Deploy.\n\n![Menu](https://cdn.example.com/m.png)"
    session.answers.append((200, answer("reply", reply)))
    member_says(cog, thread)
    [sent] = thread.replies
    assert sent.content == "**Steps**\n\n1. Open Deploy."
    assert [a.filename for a in sent.message.attachments] == ["deploy.webp"]
    [upload] = cog.bot.log_channel.sent
    assert [f.filename for f in upload.files] == ["deploy.webp", "menu.png"]
    # the second screenshot joins the same card in the DM and the thread
    for card in thread.cards:
        assert len(card.embeds) == 2 and card.embeds[0].url == card.embeds[1].url


def test_without_a_log_channel_images_are_sent_after_the_reply():
    cog, session, thread = plugin()
    cog.bot.log_channel = None
    session.answers.append((200, answer("reply", "Here:\n![Deploy](https://cdn.example.com/d.webp)")))
    member_says(cog, thread)
    [sent] = thread.replies
    assert sent.message.attachments == []
    for target in (thread.recipient, thread.channel):
        [files] = [m.files for m in target.sent if m.files]
        assert [f.filename for f in files] == ["deploy.webp"]


def test_an_image_that_cannot_be_downloaded_is_skipped():
    cog, session, thread = plugin()
    session.answers.append((200, answer("reply", "Here:\n![Gone](https://cdn.example.com/missing.png)")))
    member_says(cog, thread)
    [sent] = thread.replies
    assert sent.content == "Here:" and sent.message.attachments == []
    assert cog.bot.log_channel.sent == []


def test_an_answer_that_is_only_an_image_sends_it_in_a_reply():
    cog, session, thread = plugin()
    session.answers.append((200, answer("reply", "![Deploy](https://cdn.example.com/d.webp)")))
    member_says(cog, thread)
    [sent] = thread.replies
    assert sent.content == "" and [a.filename for a in sent.message.attachments] == ["deploy.webp"]


def test_image_names_come_from_what_they_show():
    assert dexbi.image_name("The Deploy page with New highlighted", "https://x.io/a1b2.webp", 1) == "the-deploy-page-with-new-highlighted.webp"
    assert dexbi.image_name("", "https://x.io/a1b2.PNG", 2) == "screenshot-2.png"


def test_a_long_reply_is_sent_in_parts():
    cog, session, thread = plugin()
    session.answers.append((200, answer("reply", ("a" * 3000) + "\n\n" + ("b" * 3000))))
    member_says(cog, thread)
    assert [r.content[0] for r in thread.replies] == ["a", "b"]
    assert all(r.message.content == r.content for r in thread.replies)


def test_reply_as_anonymous_staff_comes_from_the_dashboard():
    cog, session, thread = plugin({"deployment": deployment("anonymous"), "anonymous": False})
    session.answers.append((200, answer("reply", "Refunds take 5 days.")))
    member_says(cog, thread)
    assert thread.replies[0].anonymous is True


def test_a_manual_key_keeps_the_settings_it_had():
    cog, session, thread = plugin({"anonymous": True, "role_id": ROLE_ID, "wait": 10})
    session.answers.append((200, answer("handover", "A teammate will help.")))
    member_says(cog, thread)
    assert session.requests[0]["body"]["settings"]["teamWaitMinutes"] == 10
    assert thread.replies[0].anonymous is True
    assert thread.channel.sent[0].content == ROLE.mention


def test_handover_pings_the_roles_from_the_dashboard():
    cog, session, thread = plugin({"deployment": deployment(roles=[ROLE_ID, OTHER_ROLE_ID, 99]), "role_id": None})
    session.answers.append((200, answer("handover", None, staff_note="Charged twice.")))
    member_says(cog, thread)
    [alert] = thread.channel.sent
    assert alert.content == f"{ROLE.mention} {OTHER_ROLE.mention}"
    assert [role.id for role in alert.mentions.roles] == [ROLE_ID, OTHER_ROLE_ID]


def test_an_answer_brings_the_latest_dashboard_settings():
    cog, session, thread = plugin({"deployment": deployment()})
    session.answers.append((200, {**answer("reply", "Refunds take 5 days."), "deployment": deployment("anonymous")}))
    member_says(cog, thread)
    assert cog.config["deployment"]["replyAs"] == "anonymous"
    assert thread.replies[0].anonymous is True


def test_handover_tells_the_member_briefs_staff_and_pings_the_role():
    cog, session, thread = plugin({"role_id": ROLE_ID})
    session.answers.append((200, answer("handover", "A teammate will help.", staff_note="Charged twice.")))
    member_says(cog, thread)
    assert thread.replies[0].content == "A teammate will help."
    [alert] = thread.channel.sent
    assert alert.content == f"<@&{ROLE_ID}>" and alert.embed.description == "Charged twice."
    assert [role.id for role in alert.mentions.roles] == [ROLE_ID]


def test_handover_without_a_role_pings_no_one():
    cog, session, thread = plugin()
    session.answers.append((200, answer("handover", None, staff_note="Charged twice.")))
    member_says(cog, thread)
    assert thread.replies == []
    [alert] = thread.channel.sent
    assert alert.content is None and alert.mentions.roles is False


def test_close_says_goodbye_then_closes_the_thread():
    cog, session, thread = plugin()
    session.answers.append((200, answer("close", "Glad that's sorted!")))
    member_says(cog, thread)
    assert thread.replies[0].content == "Glad that's sorted!"
    assert thread.closed_by is GUILD.me


def test_none_does_nothing():
    cog, session, thread = plugin()
    member_says(cog, thread)
    assert thread.replies == [] and thread.channel.sent == [] and thread.closed_by is None


def test_a_title_renames_the_thread_once_keeping_its_name():
    cog, session, thread = plugin()
    session.answers += [(200, answer("reply", "On it.", title="double-charge")), (200, answer("none", title="other"))]
    member_says(cog, thread)
    member_says(cog, thread)
    assert thread.channel.name == "alex-double-charge"


# failures


def test_a_failure_is_told_to_staff_once_per_kind_and_never_to_the_member():
    cog, session, thread = plugin()
    bad_key = (401, {"error": {"code": "invalid_key", "message": "Missing or invalid API key."}})
    limit = (429, {"error": {"code": "daily_limit_reached", "message": "Your plan's API calls for today are used up."}})
    session.answers += [bad_key, bad_key, limit]
    for _ in range(3):
        member_says(cog, thread)
    notes = [sent.embed for sent in thread.channel.sent]
    assert [note.title for note in notes] == ["Dexbi couldn't answer", "Dexbi couldn't answer"]
    assert "Settings > API keys" in notes[0].description and "`?dexbi key <key>`" in notes[0].description
    assert "used up" in notes[1].description
    assert thread.replies == [] and cog.last_error.endswith(notes[1].description)


def test_a_bot_that_was_disconnected_says_how_to_connect_again():
    cog, session, thread = plugin({"deployment": deployment()})
    session.answers.append((401, {"error": {"code": "invalid_key", "message": "Missing or invalid API key."}}))
    member_says(cog, thread)
    note = thread.channel.sent[0].embed.description
    assert "disconnected from Dexbi" in note and "**Reconnect**" in note and "`?dexbi connect`" in note


def test_dexbi_being_down_says_it_will_answer_again():
    cog, session, thread = plugin()

    def down(*args, **kwargs):
        raise dexbi.aiohttp.ClientConnectionError()

    session.request = down
    member_says(cog, thread)
    assert "answers again once it's back" in thread.channel.sent[0].embed.description


def test_a_rename_without_manage_channels_says_what_to_fix():
    cog, session, thread = plugin()

    async def forbidden(**kwargs):
        raise discord.Forbidden(SimpleNamespace(status=403, reason="Forbidden"), "Missing Permissions")

    thread.channel.edit = forbidden
    session.answers.append((200, answer("none", title="refund")))
    member_says(cog, thread)
    assert thread.channel.sent[0].embed.description == "Give the bot the Manage Channels permission."


def test_an_unreadable_answer_is_a_failure_not_a_crash():
    cog, session, thread = plugin()
    session.answers.append((502, "<html>Bad gateway</html>"))
    member_says(cog, thread)
    assert thread.channel.sent[0].embed.description == "Dexbi answered with HTTP 502."


def test_closing_a_thread_forgets_it():
    cog, session, thread = plugin({"paused": [900, 901]})
    cog.noted_errors[900] = {"invalid_key"}
    run(cog.on_thread_close(thread, None, False, True, None, False))
    assert cog.noted_errors == {} and cog.config["paused"] == [901]


# waiting for staff


class Countdown:
    """Stands in for the plugin's sleep: the wait ends when the test says so."""

    def __init__(self):
        self.slept = []
        self.done = None

    async def sleep(self, seconds):
        self.slept.append(seconds)
        self.done = self.done or asyncio.Event()
        await self.done.wait()

    def end(self):
        self.done = self.done or asyncio.Event()
        self.done.set()


def waiting(seconds):
    return {**answer("none"), "teamWaitSeconds": seconds}


def said(text, message_id):
    sent = message(text)
    sent.id = message_id
    return sent


def test_dexbi_waits_for_staff_then_answers_everything_the_member_said(monkeypatch):
    cog, session, thread = plugin()
    countdown = Countdown()
    monkeypatch.setattr(dexbi.asyncio, "sleep", countdown.sleep)
    session.answers += [(200, waiting(300)), (200, waiting(240)), (200, answer("reply", "It ships tomorrow."))]

    async def scenario():
        await cog.on_thread_reply(thread, False, said("it's A-48213", 1), False, False)
        await cog.on_thread_reply(thread, False, said("hello?", 2), False, False)
        assert thread.replies == []
        task = cog.waits[900]["task"]
        countdown.end()
        await task

    run(scenario())
    assert countdown.slept == [300], "one countdown, from the first message"
    body = session.requests[-1]["body"]
    assert body["teamWaitOver"] is True and body["message"] == "it's A-48213\nhello?" and body["messageId"] == "2"
    assert [reply.content for reply in thread.replies] == ["It ships tomorrow."]
    assert cog.waits == {}


def test_a_staff_reply_cancels_the_wait(monkeypatch):
    cog, session, thread = plugin()
    countdown = Countdown()
    monkeypatch.setattr(dexbi.asyncio, "sleep", countdown.sleep)
    session.answers.append((200, waiting(300)))

    async def scenario():
        await cog.on_thread_reply(thread, False, said("it's A-48213", 1), False, False)
        task = cog.waits[900]["task"]
        await cog.on_thread_reply(thread, True, message("?reply found it, refunding now", 7, "Sam"), False, False)
        countdown.end()
        await asyncio.gather(task, return_exceptions=True)
        return task

    task = run(scenario())
    assert task.cancelled() and cog.waits == {}
    assert [request["body"].get("teamWaitOver") for request in session.requests] == [None, None]
    assert thread.replies == []


def test_an_answer_from_dexbi_ends_the_wait(monkeypatch):
    cog, session, thread = plugin()
    countdown = Countdown()
    monkeypatch.setattr(dexbi.asyncio, "sleep", countdown.sleep)
    session.answers += [(200, waiting(300)), (200, answer("reply", "Refunds take 5 days."))]

    async def scenario():
        await cog.on_thread_reply(thread, False, said("it's A-48213", 1), False, False)
        task = cog.waits[900]["task"]
        await cog.on_thread_reply(thread, False, said("dexbi how long do refunds take?", 2), False, False)
        await asyncio.gather(task, return_exceptions=True)
        return task

    assert run(scenario()).cancelled()
    assert [reply.content for reply in thread.replies] == ["Refunds take 5 days."]


def test_closing_or_pausing_a_thread_ends_its_wait(monkeypatch):
    for end in ("close", "pause"):
        cog, session, thread = plugin()
        countdown = Countdown()
        monkeypatch.setattr(dexbi.asyncio, "sleep", countdown.sleep)
        session.answers.append((200, waiting(300)))

        async def scenario():
            await cog.on_thread_reply(thread, False, said("it's A-48213", 1), False, False)
            task = cog.waits[900]["task"]
            if end == "close":
                await cog.on_thread_close(thread, None, False, True, None, False)
            else:
                await cog.dexbi_pause.callback(cog, FakeContext(thread.channel))
            await asyncio.gather(task, return_exceptions=True)
            return task

        assert run(scenario()).cancelled(), end
        assert cog.waits == {}


# connecting


def test_connect_saves_the_key_and_the_deployment():
    cog, session, thread = plugin({"key": None})
    session.answers.append((200, {"key": "dexbi_connected", "deployment": deployment("anonymous", [ROLE_ID])}))
    ctx = FakeContext(FakeChannel(1), command="dexbi connect")
    run(cog.dexbi_connect.callback(cog, ctx, code="abcd-2345"))
    request = session.requests[0]
    assert request["method"] == "POST" and request["url"] == "https://api.dexbi.app/v1/modmail/connect"
    assert request["body"] == {"code": "abcd-2345"} and request["headers"] == {}
    assert cog.config["key"] == "dexbi_connected" and cog.config["deployment"]["name"] == "Modmail"
    assert ctx.embed.title == "Connected" and "**Modmail**" in ctx.embed.description
    member_says(cog, thread)
    assert session.requests[-1]["headers"]["Authorization"] == "Bearer dexbi_connected"


def test_connect_deletes_the_message_with_the_code():
    cog, session, thread = plugin({"key": None})
    session.answers.append((200, {"key": "dexbi_connected", "deployment": deployment()}))
    ctx = FakeContext(FakeChannel(1), command="dexbi connect")
    run(cog.dexbi_connect.callback(cog, ctx, code="abcd-2345"))
    assert ctx.message.deleted and "delete it yourself" not in ctx.embed.description

    async def forbidden():
        raise discord.Forbidden(SimpleNamespace(status=403, reason="Forbidden"), "Missing Permissions")

    ctx.message.delete = forbidden
    session.answers.append((200, {"key": "dexbi_connected", "deployment": deployment()}))
    run(cog.dexbi_connect.callback(cog, ctx, code="abcd-2345"))
    assert "Please delete it yourself, the code is secret." in ctx.embed.description


def test_connect_and_key_need_the_administrator_level():
    for command in (dexbi.Dexbi.dexbi_connect, dexbi.Dexbi.dexbi_key, dexbi.Dexbi.dexbi_test):
        assert command.callback.permission_level == dexbi.PermissionLevel.ADMINISTRATOR


def test_the_key_and_the_code_never_show_up_in_errors(caplog):
    cog, session, thread = plugin({"key": None})
    session.answers.append((400, {"error": {"code": "invalid_code", "message": "That code didn't work."}}))
    ctx = FakeContext(FakeChannel(1), command="dexbi connect")
    run(cog.dexbi_connect.callback(cog, ctx, code="SECRET-CODE"))

    cog.config.update(key="dexbi_secretkey", deployment=deployment())
    session.answers.append((401, {"error": {"code": "invalid_key", "message": "Missing or invalid API key."}}))
    run(cog.check_in())

    def down(*args, **kwargs):
        raise dexbi.aiohttp.ClientConnectionError()

    session.request = down
    run(cog.check_in())
    member_says(cog, thread)
    shown = [caplog.text, cog.last_error, *(e.description for e in ctx.sent), *(m.embed.description for m in thread.channel.sent)]
    assert not any("SECRET-CODE" in text or "secretkey" in text for text in shown)


def test_a_bad_code_says_why():
    cog, session, thread = plugin({"key": None})
    message = "That code didn't work. Get a new one in the Dexbi dashboard."
    session.answers.append((400, {"error": {"code": "invalid_code", "message": message}}))
    ctx = FakeContext(FakeChannel(1), command="dexbi connect")
    run(cog.dexbi_connect.callback(cog, ctx, code="nope"))
    assert ctx.embed.title == "Couldn't connect" and ctx.embed.description == message
    assert cog.config["key"] is None


def test_checking_in_picks_up_dashboard_changes():
    cog, session, thread = plugin({"deployment": deployment()})
    session.answers.append((200, {"deployment": deployment("anonymous", [ROLE_ID])}))
    run(cog.check_in())
    request = session.requests[0]
    assert request["method"] == "GET" and request["url"] == "https://api.dexbi.app/v1/modmail/status"
    assert request["headers"]["Authorization"] == "Bearer dexbi_test"
    assert cog.config["deployment"]["replyAs"] == "anonymous" and cog.last_error is None


def test_checking_in_quietly_skips_an_older_dexbi_and_notes_a_dead_key():
    cog, session, thread = plugin({"deployment": deployment()})
    session.answers += [(404, "Not Found"), (401, {"error": {"code": "invalid_key", "message": "Bad key."}})]
    run(cog.check_in())
    assert cog.last_error is None and cog.config["deployment"] == deployment()
    run(cog.check_in())
    assert "disconnected from Dexbi" in cog.last_error
    assert thread.channel.sent == []


def test_without_a_key_there_is_no_check_in():
    cog, session, thread = plugin({"key": None})
    run(cog.check_in())
    assert session.requests == []


# commands


def test_status_shows_the_connection_settings_and_commands():
    cog, session, thread = plugin({"deployment": deployment("anonymous", [ROLE_ID])})
    session.answers.append((200, {"deployment": deployment("anonymous", [ROLE_ID])}))
    ctx = FakeContext(FakeChannel(1))
    run(cog.dexbi.callback(cog, ctx))
    fields = {field.name: field.value for field in ctx.embed.fields}
    assert fields["Connection"] == "Connected to **Modmail**" and fields["Check"] == "Working"
    assert fields["Hand-over ping"] == ROLE.mention and fields["Replies as"] == "Anonymous staff"
    assert "?dexbi connect" in fields["Commands"] and "?dexbi pause" in fields["Commands"]
    assert "?dexbi wait" not in fields["Commands"] and "?dexbi role" not in fields["Commands"]
    assert ctx.embed.color.value == dexbi.COLOR
    assert session.requests[1]["body"]["author"] == "staff"


def test_status_with_a_manual_key():
    cog, session, thread = plugin({"role_id": ROLE_ID})
    session.answers.append((200, {"deployment": None}))
    ctx = FakeContext(FakeChannel(1))
    run(cog.dexbi.callback(cog, ctx))
    fields = {field.name: field.value for field in ctx.embed.fields}
    assert fields["Connection"] == "Manual API key" and fields["Hand-over ping"] == ROLE.mention


def test_status_says_when_the_bot_was_disconnected():
    cog, session, thread = plugin({"deployment": deployment()})
    bad_key = (401, {"error": {"code": "invalid_key", "message": "Missing or invalid API key."}})
    session.answers += [bad_key, bad_key]
    ctx = FakeContext(FakeChannel(1))
    run(cog.dexbi.callback(cog, ctx))
    fields = {field.name: field.value for field in ctx.embed.fields}
    assert fields["Connection"] == "Disconnected"
    assert fields["Check"] == "Not working" and "Last error" not in fields
    assert "**Reconnect**" in fields["What to do"] and "`?dexbi connect`" in fields["What to do"]


def test_status_says_when_the_key_does_not_work():
    cog, session, thread = plugin()
    bad_key = (401, {"error": {"code": "invalid_key", "message": "Bad key."}})
    session.answers += [bad_key, bad_key]
    ctx = FakeContext(FakeChannel(1))
    run(cog.dexbi_status.callback(cog, ctx))
    fields = {f.name: f.value for f in ctx.embed.fields}
    assert fields["Check"] == "Not working" and fields["What to do"].startswith("The API key is wrong")


def test_without_a_key_status_shows_the_setup_steps():
    cog, session, thread = plugin({"key": None})
    ctx = FakeContext(FakeChannel(1))
    run(cog.dexbi.callback(cog, ctx))
    assert ctx.embed.title == "Set up Dexbi" and "?dexbi connect <code>" in ctx.embed.description
    assert "**Deploy**" in ctx.embed.description and "**Modmail**" in ctx.embed.description
    assert session.requests == []


def test_key_is_deleted_saved_and_checked():
    cog, session, thread = plugin({"key": None, "deployment": deployment()})
    ctx = FakeContext(FakeChannel(1))
    run(cog.dexbi_key.callback(cog, ctx, " dexbi_new "))
    assert ctx.message.deleted and cog.config["key"] == "dexbi_new" and cog.config["deployment"] is None
    assert session.requests[0]["headers"]["Authorization"] == "Bearer dexbi_new"
    assert ctx.embed.title == "You're all set"

    session.answers.append((401, {"error": {"code": "invalid_key", "message": "Bad key."}}))
    run(cog.dexbi_key.callback(cog, ctx, "dexbi_bad"))
    assert ctx.embed.title == "Key saved, but it doesn't work" and "?dexbi key" in ctx.embed.description


def test_test_shows_the_answer_here_and_messages_no_member():
    cog, session, thread = plugin()
    reply = "## Refunds\nThey take 5 days.\n![Form](https://cdn.example.com/f.png)"
    session.answers.append((200, answer("handover", reply, staff_note="Wants a refund.", title="refund")))
    ctx = FakeContext(FakeChannel(1))
    run(cog.dexbi_test.callback(cog, ctx, question="How do refunds work?"))
    body = session.requests[0]["body"]
    assert body["conversation"].startswith("modmail-test-") and "author" not in body
    assert ctx.embed.description.startswith("**Refunds**")
    assert ctx.embed.image.url == "https://cdn.example.com/f.png"
    fields = {f.name: f.value for f in ctx.embed.fields}
    assert fields == {"Action": "handover", "Staff note": "Wants a refund.", "Suggested title": "refund"}
    assert thread.replies == []


def test_pause_and_resume_in_a_thread():
    cog, session, thread = plugin()
    ctx = FakeContext(thread.channel)
    run(cog.dexbi_pause.callback(cog, ctx))
    assert cog.config["paused"] == [900] and ctx.embed.title == "Dexbi paused here"
    member_says(cog, thread)
    assert session.requests == []
    run(cog.dexbi_resume.callback(cog, ctx))
    member_says(cog, thread)
    assert cog.config["paused"] == [] and len(session.requests) == 1


def test_pause_outside_a_thread_says_where_to_run_it():
    cog, session, thread = plugin()
    ctx = FakeContext(FakeChannel(1))
    run(cog.dexbi_pause.callback(cog, ctx))
    assert ctx.embed.title == "Not a thread" and "paused" not in cog.config


def test_no_permission_says_which_level_is_needed():
    cog, session, thread = plugin()
    ctx = FakeContext(FakeChannel(1), command="dexbi key")
    run(cog.cog_command_error(ctx, commands.CheckFailure()))
    assert ctx.embed.title == "No permission" and "**Administrator**" in ctx.embed.description


def test_a_wrong_argument_shows_the_usage():
    cog, session, thread = plugin()
    ctx = FakeContext(FakeChannel(1), command="dexbi key")
    run(cog.cog_command_error(ctx, commands.MissingRequiredArgument(SimpleNamespace(name="key", displayed_name="key"))))
    assert ctx.embed.title == "Not quite" and "`?dexbi key <key>`" in ctx.embed.description


def test_the_cog_and_its_commands_load():
    names = {command.qualified_name for command in dexbi.Dexbi.__cog_commands__}
    assert names == {"dexbi", "dexbi connect", "dexbi key", "dexbi test", "dexbi pause", "dexbi resume",
                     "dexbi status", "dexbi setup"}
    assert isinstance(dexbi.make_embed("x"), discord.Embed)


def test_a_bare_image_link_is_attached_and_taken_out_of_the_text():
    cog, session, thread = plugin()
    reply = "Here's the button: https://cdn.example.com/a/new-button.webp\nThen click it."
    session.answers.append((200, answer("reply", reply)))
    member_says(cog, thread)
    [sent] = thread.replies
    assert sent.content == "Here's the button:\nThen click it."
    assert [a.filename for a in sent.message.attachments] == ["screenshot-1.webp"]


def test_a_normal_link_stays_in_the_text():
    text = dexbi.to_embed_text("See [the docs](https://docs.example.com/setup) and https://example.com/page")
    assert text == "See [the docs](https://docs.example.com/setup) and https://example.com/page"


def test_a_link_to_a_screenshot_is_attached_not_shown_as_an_address():
    url = "https://pub-1.r2.dev/tenants/9dcf/documents/d0/v1/images/d27d.webp"
    reply = f"Here's the New button: [{url}]({url})\nThen click it."
    assert dexbi.to_embed_text(reply) == "Here's the New button:\nThen click it."
    assert dexbi.named_images(reply) == [("screenshot-1.webp", url)]

