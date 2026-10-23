import asyncio
from types import SimpleNamespace

from discord.ext.commands.view import StringView

import dexbi

BOT_ID = 1
STAFF_ROLE_ID = 55


class FakeResponse:
    def __init__(self, status: int, payload):
        self.status, self.payload = status, payload

    async def json(self, content_type=None):
        return self.payload

    async def __aenter__(self):
        return self

    async def __aexit__(self, *exc):
        return False


class FakeSession:
    def __init__(self):
        self.requests: list[dict] = []
        self.answers: list[tuple[int, object]] = []

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


class FakeChannel:
    def __init__(self, guild):
        self.id, self.name, self.guild = 900, "alex", guild
        self.sent: list[tuple[str, object]] = []

    async def send(self, content, allowed_mentions=None):
        self.sent.append((content, allowed_mentions))

    async def edit(self, *, name, reason=None):
        self.name = name


class FakeThread:
    def __init__(self):
        role = SimpleNamespace(id=STAFF_ROLE_ID, name="Support", mention=f"<@&{STAFF_ROLE_ID}>")
        guild = SimpleNamespace(me=user(BOT_ID, "Dexbi"), get_role=lambda role_id: role if role_id == STAFF_ROLE_ID else None)
        self.channel = FakeChannel(guild)
        self.replies: list[tuple[object, str, bool]] = []
        self.closed_by = None

    async def reply(self, message, content=None, anonymous=False, plain=False):
        self.replies.append((message, content, anonymous))

    async def close(self, *, closer):
        self.closed_by = closer


def user(user_id: int, name: str):
    return SimpleNamespace(id=user_id, display_name=name)


def message(content: str, author_id: int = 42, name: str = "Alex"):
    return SimpleNamespace(id=777, content=content, author=user(author_id, name), attachments=[], stickers=["sticker"])


def answer(action: str, text: str | None = None, staff_note=None, title=None) -> dict:
    return {"action": action, "message": text, "staffNote": staff_note, "title": title, "conversation": "modmail-900"}


async def get_contexts(message):
    view = StringView(message.content)
    if not view.skip_string("?"):
        return [SimpleNamespace(command=None, view=view)]
    word = view.get_word()
    return [SimpleNamespace(command="reply" if word == "reply" else None, view=view)]


def plugin(config: dict | None = None):
    session = FakeSession()
    bot = SimpleNamespace(
        user=user(BOT_ID, "Dexbi"),
        session=session,
        api=SimpleNamespace(get_plugin_partition=lambda cog: FakeCollection()),
        get_contexts=get_contexts,
    )
    cog = dexbi.Dexbi(bot)
    cog.config = {"key": "dexbi_test", **(config or {})}
    return cog, session, FakeThread()


def member_says(cog, thread, text: str = "I was charged twice"):
    asyncio.run(cog.on_thread_reply(thread, False, message(text), False, False))




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


def test_a_staff_reply_is_sent_as_a_staff_message_with_the_words_sent_to_the_member():
    cog, session, thread = plugin()
    asyncio.run(cog.on_thread_reply(thread, True, message("?reply I've refunded it", 7, "Sam"), False, False))
    body = session.requests[0]["body"]
    assert body["author"] == "staff"
    assert body["message"] == "I've refunded it"
    assert body["member"] == {"id": "7", "name": "Sam"}
    assert thread.replies == [] and thread.channel.sent == []


def test_an_older_modmail_hands_over_the_reply_text_itself():
    cog, session, thread = plugin()
    asyncio.run(cog.on_thread_reply(thread, True, message("I've refunded it", 7, "Sam"), False, False))
    assert session.requests[0]["body"]["message"] == "I've refunded it"


def test_dexbis_own_replies_are_never_sent_back():
    cog, session, thread = plugin()
    asyncio.run(cog.on_thread_reply(thread, True, message("Here's how refunds work.", BOT_ID), False, False))
    assert session.requests == []


def test_nothing_is_sent_while_off_or_without_a_key_or_without_words():
    for config in ({"enabled": False}, {"key": None}):
        cog, session, thread = plugin(config)
        member_says(cog, thread)
        assert session.requests == []
    cog, session, thread = plugin()
    member_says(cog, thread, "   ")
    assert session.requests == []




def test_reply_is_sent_to_the_member_as_the_bot():
    cog, session, thread = plugin()
    session.answers.append((200, answer("reply", "Refunds take 5 days.")))
    member_says(cog, thread)
    [(sent, content, anonymous)] = thread.replies
    assert content == "Refunds take 5 days." and anonymous is False
    assert sent.author.id == BOT_ID and sent.channel is thread.channel
    assert sent.content == "Refunds take 5 days." and sent.stickers == [] and sent.attachments == []
    assert thread.closed_by is None and thread.channel.sent == []


def test_reply_follows_the_anon_setting():
    cog, session, thread = plugin({"anonymous": True})
    session.answers.append((200, answer("reply", "Refunds take 5 days.")))
    member_says(cog, thread)
    assert thread.replies[0][2] is True


def test_handover_tells_the_member_briefs_staff_and_pings_the_role():
    cog, session, thread = plugin({"role_id": STAFF_ROLE_ID})
    session.answers.append((200, answer("handover", "A teammate will help.", staff_note="Charged twice.")))
    member_says(cog, thread)
    assert thread.replies[0][1] == "A teammate will help."
    [(note, mentions)] = thread.channel.sent
    assert note.startswith(f"<@&{STAFF_ROLE_ID}>") and note.endswith("Charged twice.")
    assert [role.id for role in mentions.roles] == [STAFF_ROLE_ID]


def test_handover_without_a_role_pings_no_one():
    cog, session, thread = plugin()
    session.answers.append((200, answer("handover", None, staff_note="Charged twice.")))
    member_says(cog, thread)
    assert thread.replies == []
    [(note, mentions)] = thread.channel.sent
    assert "<@&" not in note and mentions.roles is False


def test_close_says_goodbye_then_closes_the_thread():
    cog, session, thread = plugin()
    session.answers.append((200, answer("close", "Glad that's sorted!")))
    member_says(cog, thread)
    assert thread.replies[0][1] == "Glad that's sorted!"
    assert thread.closed_by.id == BOT_ID


def test_none_does_nothing():
    cog, session, thread = plugin()
    session.answers.append((200, answer("none")))
    member_says(cog, thread)
    assert thread.replies == [] and thread.channel.sent == [] and thread.closed_by is None


def test_a_title_renames_the_thread_once_keeping_its_name():
    cog, session, thread = plugin()
    session.answers += [(200, answer("reply", "On it.", title="double-charge")), (200, answer("none", title="other"))]
    member_says(cog, thread)
    member_says(cog, thread)
    assert thread.channel.name == "alex-double-charge"




def test_a_failure_is_told_to_staff_once_per_kind_and_never_to_the_member():
    cog, session, thread = plugin()
    bad_key = (401, {"error": {"code": "invalid_key", "message": "Missing or invalid API key."}})
    limit = (429, {"error": {"code": "daily_limit_reached", "message": "Your plan's API calls for today are used up."}})
    session.answers += [bad_key, bad_key, limit]
    for _ in range(3):
        member_says(cog, thread)
    notes = [note for note, _ in thread.channel.sent]
    assert len(notes) == 2
    assert notes[0].startswith("Dexbi couldn't answer: Missing or invalid API key.") and "?dexbi key" in notes[0]
    assert "used up" in notes[1]
    assert thread.replies == []
    assert cog.last_error == notes[1]


def test_an_unreadable_answer_is_a_failure_not_a_crash():
    cog, session, thread = plugin()
    session.answers.append((502, "<html>Bad gateway</html>"))
    member_says(cog, thread)
    assert thread.channel.sent[0][0] == "Dexbi couldn't answer: Dexbi answered with HTTP 502."


def test_closing_a_thread_forgets_it():
    cog, session, thread = plugin()
    session.answers.append((401, {"error": {"code": "invalid_key", "message": "Bad key."}}))
    member_says(cog, thread)
    asyncio.run(cog.on_thread_close(thread, None, False, True, None, False))
    assert cog.noted_errors == {} and cog.locks == {}
