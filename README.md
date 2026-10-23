# Dexbi for Modmail

[Dexbi](https://www.dexbi.app) answers your members in Modmail threads. It stays out of the way while your staff are talking, hands threads over to your team when your escalation rules match, and closes threads when the member is done.

Needs a self-hosted [Modmail](https://github.com/modmail-dev/modmail) bot, v4 or later.

## Install

1. In the [Dexbi dashboard](https://dash.dexbi.app), go to **Settings > API keys** and create a key.
2. `?plugins add throgz/modmail-plugins/dexbi`
3. `?dexbi key <your key>` (the message with your key gets deleted)

## Commands

| Command | Description |
| --- | --- |
| `?dexbi` | Status and all commands (setup steps if there's no key yet) |
| `?dexbi key <key>` | Set your Dexbi API key |
| `?dexbi on` / `?dexbi off` | Turn Dexbi on or off |
| `?dexbi role @role` / `?dexbi role none` | Role to ping when Dexbi hands a thread over |
| `?dexbi style bot` / `?dexbi style anonymous` | Reply as the bot (default) or as anonymous staff |
| `?dexbi test <question>` | See Dexbi's answer in the channel, without messaging anyone |
| `?dexbi pause` / `?dexbi resume` | Stop or restart Dexbi in the current thread |
| `?dexbi status` / `?dexbi setup` | Status, or the setup steps |

`key`, `on`, `off`, `role`, `style` and `test` need the Administrator permission level. The rest work for Supporters.

## Notes

- Each answer from Dexbi counts as one AI reply on your Dexbi plan. Staff messages are free.
- If something goes wrong (bad key, plan limit), the member sees nothing and staff get one note in the thread.
- Docs: https://docs.dexbi.app/tutorials/modmail

## License

MIT
