# Dexbi for Modmail

[Dexbi](https://www.dexbi.app) answers your members in Modmail threads. It gives your staff time to reply when they're in a thread, hands threads over to your team when your escalation rules match, and closes threads when the member is done.

Works with self-hosted [Modmail](https://github.com/modmail-dev/modmail) v4.0 and later (tested on 4.0.0 and 4.2.1).

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
| `?dexbi wait <minutes>` | Minutes to give staff to reply before Dexbi answers (1 to 60, default 5) |
| `?dexbi test <question>` | See Dexbi's answer in the channel, without messaging anyone |
| `?dexbi pause` / `?dexbi resume` | Stop or restart Dexbi in the current thread |
| `?dexbi status` / `?dexbi setup` | Status, or the setup steps |

`key`, `on`, `off`, `role`, `style`, `wait` and `test` need the Administrator permission level. The rest work for Supporters.

## Notes

- Each answer from Dexbi counts as one AI reply on your Dexbi plan. Staff messages are free.
- When staff replied last in a thread, Dexbi waits for them before it answers the member. If no staff message comes in time, it answers everything the member said. A message that starts with Dexbi's name is answered right away.
- When your plan's AI replies for the month run out, Dexbi stays quiet until they reset.
- Screenshots from your Dexbi documents show up as images in the reply.
- Dexbi reads screenshots members send (up to 4 per message). Other files are passed on by name only.
- If something goes wrong (bad key, plan limit), the member sees nothing and staff get one note in the thread.
- Docs: https://docs.dexbi.app/tutorials/modmail

## License

MIT
