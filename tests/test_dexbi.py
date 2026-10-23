import asyncio
from contextlib import asynccontextmanager
from types import SimpleNamespace

import discord
from discord.ext import commands
from discord.ext.commands.view import StringView

import dexbi

BOT_ID = 1
ROLE_ID = 55


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

    def post(self, url, *, json, headers, timeout):
        self.requests.append({"url": url, "body": json, "headers": headers})
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
GUILD = SimpleNamespace(me=SimpleNamespace(id=BOT_ID, roles=[]), get_role=lambda i: ROLE if i == ROLE_ID else None)


class FakeChannel:
    def __init__(self, channel_id=900):
        self.id, self.name, self.guild = channel_id, "alex", GUILD
        self.sent = []

    async def send(self, content=None, embed=None, allowed_mentions=None, files=None):
        sent = SimpleNamespace(content=content, embed=embed, mentions=allowed_mentions, files=files,
                               attachments=[SimpleNamespace(filename=f.filename) for f in files or []])
        self.sent.append(sent)
        return sent

    async def edit(self, *, name, reason=None):
        self.name = name


class FakeThread:
    def __init__(self):
        self.channel = FakeChannel()
        self.recipient = FakeChannel(901)
        self.replies = []
        self.closed_by = None

    async def reply(self, message, content=None, anonymous=False, plain=False):
        self.replies.append(SimpleNamespace(message=message, content=content, anonymous=anonymous))

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
    assert dexbi.image_urls(text) == ["https://cdn.example.com/a.webp", "https://cdn.example.com/b.PNG"]


def test_an_image_modmail_cannot_show_stays_a_link():
    text = "![Diagram](https://example.com/render?id=4)"
    assert dexbi.to_embed_text(text) == "[Diagram](https://example.com/render?id=4)"
    assert dexbi.image_urls(text) == []


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
        "settings": dexbi.TICKET_SETTINGS,
    }
    assert dexbi.TICKET_SETTINGS["reply"] == "smart" and dexbi.TICKET_SETTINGS["agent"] == "ticket"


def test_a_staff_reply_is_sent_so_dexbi_steps_back():
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
    for config in ({"enabled": False}, {"key": None}, {"paused": [900]}):
        cog, session, thread = plugin(config)
        member_says(cog, thread)
        staff_says(cog, thread, "?reply hi")
        assert session.requests == []
    cog, session, thread = plugin()
    member_says(cog, thread, "   ")
    assert session.requests == []


# what the plugin does with the answer


def test_reply_goes_to_the_member_as_the_bot_user():
    cog, session, thread = plugin()
    session.answers.append((200, answer("reply", "## Refunds\nThey take 5 days.")))
    member_says(cog, thread)
    [sent] = thread.replies
    assert sent.content == "**Refunds**\nThey take 5 days." and sent.anonymous is False
    # no roles on the author, so Modmail's footer falls back to the bot's name
    assert sent.message.author is cog.bot.user and not hasattr(sent.message.author, "roles")
    assert sent.message.channel is thread.channel
    assert sent.message.stickers == [] and sent.message.attachments == []


def test_images_ride_on_the_reply_like_staff_attachments_with_no_gap():
    cog, session, thread = plugin()
    reply = "**Steps**\n\n![Deploy](https://cdn.example.com/d.webp)\n\n1. Open Deploy.\n\n![Menu](https://cdn.example.com/m.png)"
    session.answers.append((200, answer("reply", reply)))
    member_says(cog, thread)
    [sent] = thread.replies
    assert sent.content == "**Steps**\n\n1. Open Deploy."
    assert [a.filename for a in sent.message.attachments] == ["d.webp", "m.png"]
    [upload] = cog.bot.log_channel.sent
    assert [f.filename for f in upload.files] == ["d.webp", "m.png"]


def test_without_a_log_channel_images_are_sent_after_the_reply():
    cog, session, thread = plugin()
    cog.bot.log_channel = None
    session.answers.append((200, answer("reply", "Here:\n![Deploy](https://cdn.example.com/d.webp)")))
    member_says(cog, thread)
    [sent] = thread.replies
    assert sent.message.attachments == []
    for target in (thread.recipient, thread.channel):
        [files] = [m.files for m in target.sent if m.files]
        assert [f.filename for f in files] == ["d.webp"]


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
    assert sent.content == "" and [a.filename for a in sent.message.attachments] == ["d.webp"]


def test_a_long_reply_is_sent_in_parts():
    cog, session, thread = plugin()
    session.answers.append((200, answer("reply", ("a" * 3000) + "\n\n" + ("b" * 3000))))
    member_says(cog, thread)
    assert [r.content[0] for r in thread.replies] == ["a", "b"]
    assert all(r.message.content == r.content for r in thread.replies)


def test_reply_follows_the_style_setting():
    cog, session, thread = plugin({"anonymous": True})
    session.answers.append((200, answer("reply", "Refunds take 5 days.")))
    member_says(cog, thread)
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


def test_dexbi_being_down_says_it_will_answer_again():
    cog, session, thread = plugin()

    def down(*args, **kwargs):
        raise dexbi.aiohttp.ClientConnectionError()

    session.post = down
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


# commands


def test_status_shows_settings_and_commands():
    cog, session, thread = plugin({"role_id": ROLE_ID})
    ctx = FakeContext(FakeChannel(1))
    run(cog.dexbi.callback(cog, ctx))
    fields = {field.name: field.value for field in ctx.embed.fields}
    assert fields["Status"] == "On" and fields["API key"] == "Working"
    assert fields["Hand-over ping"] == ROLE.mention and fields["Replies as"] == "The bot"
    assert "?dexbi test" in fields["Commands"] and "?dexbi pause" in fields["Commands"]
    assert ctx.embed.color.value == dexbi.COLOR
    assert session.requests[0]["body"]["author"] == "staff"


def test_status_says_when_the_key_does_not_work():
    cog, session, thread = plugin()
    session.answers.append((401, {"error": {"code": "invalid_key", "message": "Bad key."}}))
    ctx = FakeContext(FakeChannel(1))
    run(cog.dexbi_status.callback(cog, ctx))
    assert {f.name: f.value for f in ctx.embed.fields}["API key"].startswith("Not working: The API key is wrong")


def test_without_a_key_status_shows_the_setup_steps():
    cog, session, thread = plugin({"key": None})
    ctx = FakeContext(FakeChannel(1))
    run(cog.dexbi.callback(cog, ctx))
    assert ctx.embed.title == "Set up Dexbi" and "?dexbi key <your key>" in ctx.embed.description
    assert session.requests == []


def test_key_is_deleted_saved_and_checked():
    cog, session, thread = plugin({"key": None})
    ctx = FakeContext(FakeChannel(1))
    run(cog.dexbi_key.callback(cog, ctx, " dexbi_new "))
    assert ctx.message.deleted and cog.config["key"] == "dexbi_new"
    assert session.requests[0]["headers"]["Authorization"] == "Bearer dexbi_new"
    assert ctx.embed.title == "You're all set"

    session.answers.append((401, {"error": {"code": "invalid_key", "message": "Bad key."}}))
    run(cog.dexbi_key.callback(cog, ctx, "dexbi_bad"))
    assert ctx.embed.title == "Key saved, but it doesn't work" and "?dexbi key" in ctx.embed.description


def test_on_and_off():
    cog, session, thread = plugin()
    ctx = FakeContext(FakeChannel(1))
    run(cog.dexbi_off.callback(cog, ctx))
    assert cog.enabled is False and ctx.embed.title == "Dexbi is off"
    run(cog.dexbi_on.callback(cog, ctx))
    assert cog.enabled is True and ctx.embed.title == "Dexbi is on"


def test_role_and_none():
    cog, session, thread = plugin()
    ctx = FakeContext(FakeChannel(1))
    run(cog.dexbi_role.callback(cog, ctx, ROLE))
    assert cog.config["role_id"] == ROLE_ID and ROLE.mention in ctx.embed.description
    run(cog.dexbi_role.callback(cog, ctx, "none"))
    assert cog.config["role_id"] is None


def test_style_and_the_anon_alias():
    cog, session, thread = plugin()
    ctx = FakeContext(FakeChannel(1))
    run(cog.dexbi_style.callback(cog, ctx, "anonymous"))
    assert cog.config["anonymous"] is True
    run(cog.dexbi_anon.callback(cog, ctx, "off"))
    assert cog.config["anonymous"] is False and "the bot" in ctx.embed.description


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
    assert {"dexbi", "dexbi key", "dexbi on", "dexbi off", "dexbi role", "dexbi style", "dexbi anon",
            "dexbi test", "dexbi pause", "dexbi resume", "dexbi status", "dexbi setup"} <= names
    assert isinstance(dexbi.make_embed("x"), discord.Embed)
