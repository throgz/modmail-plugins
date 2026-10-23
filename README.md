# Dexbi for Modmail

[Dexbi](https://www.dexbi.app) answers your members in Modmail threads. It gives your staff time to reply when they're in a thread, hands threads over to your team when your escalation rules match, and closes threads when the member is done.

Works with self-hosted [Modmail](https://github.com/modmail-dev/modmail) v4 (4.0 to 4.2). Modmail v5 is still in development and has no plugin system yet, so plugins can't run on it. Adding one can stop a v5 bot from starting.

## Install

1. In the [Dexbi dashboard](https://dash.dexbi.app), open **Deploy** and click **New** under Ticket agent. Pick **Modmail**.
2. Run the two commands the dashboard shows in your Modmail server:
   - `?plugins add throgz/modmail-plugins/dexbi`
   - `?dexbi connect <code>` (the code works once and runs out after 15 minutes; the dashboard always shows a fresh one, and the plugin deletes your message)
3. The dashboard shows **Connected**. Dexbi now answers new messages in your Modmail threads.

How Dexbi answers is set in the dashboard, on your Modmail deployment: when it replies, how long it waits for staff, hand-overs and who gets pinged, Smart close, Smart rename, and whether it replies as the bot or as anonymous staff. Changes apply right away, no command needed.

Rather use an API key? Create one under **Settings > API keys** and run `?dexbi key <your key>` instead of `connect`.

## Updating

```
?plugins update throgz/modmail-plugins/dexbi
```

`?dexbi` shows which version you have, and your Modmail deployment in the dashboard says when a newer one is out. Running `?plugins add throgz/modmail-plugins/dexbi` again also updates it (from 1.1.0 on).

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
- If the bot gets disconnected (Disconnect in the dashboard, or the deployment was deleted), `?dexbi` shows **Disconnected**. To reconnect, open your Modmail deployment under **Deploy**, click **Reconnect** and run the `?dexbi connect` command it shows.
- Docs: https://docs.dexbi.app/tutorials/modmail

## License

MIT
