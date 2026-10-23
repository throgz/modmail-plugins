# Stand-ins for Modmail's core package so the plugin can be imported without a bot.

import enum
import logging
import sys
import types
from pathlib import Path


class PermissionLevel(enum.IntEnum):
    OWNER = 5
    ADMINISTRATOR = 4
    MODERATOR = 3
    SUPPORTER = 2
    REGULAR = 1
    INVALID = -1


class DummyMessage:
    def __init__(self, message):
        if message:
            message.attachments = []
        self._message = message

    def __getattr__(self, name):
        return getattr(self._message, name)

    async def delete(self, *, delay=None):
        return


def _install_core_stubs() -> None:
    core = types.ModuleType("core")
    checks = types.ModuleType("core.checks")
    checks.has_permissions = lambda level: (lambda command: command)
    models = types.ModuleType("core.models")
    models.PermissionLevel = PermissionLevel
    models.DummyMessage = DummyMessage
    models.getLogger = logging.getLogger
    core.checks, core.models = checks, models
    sys.modules.update({"core": core, "core.checks": checks, "core.models": models})


_install_core_stubs()
sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "dexbi"))
