# Dexbi for Modmail

[Dexbi](https://www.dexbi.app) answers your members in Modmail threads. It gives your staff time to reply when they're in a thread, hands threads over to your team when your escalation rules match, and closes threads when the member is done.

Works with self-hosted [Modmail](https://github.com/modmail-dev/modmail) v4.0 and later (tested on 4.0.0 and 4.2.1).

## Install

1. In the [Dexbi dashboard](https://dash.dexbi.app), open **Deploy** and click **New** under Ticket agent. Pick **Modmail**.
2. Run the two commands the dashboard shows in your Modmail server:
   - `?plugins add throgz/modmail-plugins/dexbi`
   - `?dexbi connect <code>` (the code works once and runs out after a while; the dashboard always shows a fresh one)
3. The dashboard shows **Connected**. Dexbi now answers new messages in your Modmail threads.

How Dexbi answers is set in the dashboard, on your Modmail deployment: when it replies, how long it waits for staff, hand-overs and who gets pinged, Smart close, Smart rename, and whether it replies as the bot or as anonymous staff. Changes apply right away, no command needed.

Rather use an API key? Create one under **Settings > API keys** and run `?dexbi key <your key>` instead of `connect`.

## Commands

| Command | Description |
| --- | --- |
| `?dexbi` | Status and all commands (setup steps if it isn't set up yet) |
| `?dexbi connect <code>` | Connect to your Dexbi dashboard with the code from Deploy |
| `?dexbi key <key>` | Use a Dexbi API key instead of connecting |
| `?dexbi test <question>` | See Dexbi's answer in the channel, without messaging anyone |
| `?dexbi pause` / `?dexbi resume` | Stop or restart Dexbi in the current thread |
| `?dexbi status` / `?dexbi setup` | Status, or the setup steps |

`connect`, `key` and `test` need the Administrator permission level. The rest work for Supporters.

## Notes

- Each answer from Dexbi counts as one AI reply on your Dexbi plan. Staff messages are free.
- When staff replied last in a thread, Dexbi waits for them before it answers the member. If no staff message comes in time, it answers everything the member said. A message that starts with Dexbi's name is answered right away.
- When your plan's AI replies for the month run out, Dexbi stays quiet until they reset.
- Screenshots from your Dexbi documents show up as images in the reply.
- Dexbi reads screenshots members send (up to 4 per message). Other files are passed on by name only.
- If something goes wrong (bot disconnected, plan limit), the member sees nothing and staff get one note in the thread.
- Docs: https://docs.dexbi.app/tutorials/modmail

## License

MIT
