# todoist-bridge

Sync Google Tasks and Todoist so voice assistants that only speak Google Tasks can manage Todoist.

Gemini (Android Auto, Google Assistant) creates, reads aloud and completes Google Tasks, but it has no Todoist integration. todoist-bridge runs every minute and keeps one Google Tasks list in sync with Todoist, so you can say "remind me to buy milk tomorrow at 4pm" in the car and find the task in Todoist.

## How it works

- **Voice capture:** a new task in Google Tasks goes to Todoist through [Quick Add](https://www.todoist.com/help/articles/use-task-quick-add-in-todoist-va4Lhpzz), so Todoist parses dates, `#project`, `@label` and `p1` in your account language. Spoken prefixes such as "remind me to" or "me lembra de" are removed first.
- **Today's list:** Todoist tasks due today or overdue appear in Google Tasks, so "what are my tasks for today?" reads your Todoist tasks.
- **Completion:** completing a task on either side completes it on the other.
- **Rescheduling:** when a mirrored task moves to a future date in Todoist, its Google copy is deleted. A voice task with a future date or no date leaves Google Tasks after it reaches Todoist.

Todoist is the source of truth. Google Tasks holds only what is due now. The Todoist id of each mirrored task lives in the Google task notes (`todoist:<id>`), so the bridge needs no database.

## Setup

You need [uv](https://docs.astral.sh/uv/), a Todoist account, a Google account and a [Modal](https://modal.com) account.

```bash
git clone https://github.com/rodbv/todoist-bridge && cd todoist-bridge
uv sync
cp .env.example .env
```

### 1. Todoist

Copy your API token from Todoist Settings, Integrations, Developer into `TODOIST_API_TOKEN`.

Quick Add parses dates in the language of your Todoist account. If you speak Portuguese to your assistant, set Todoist to Portuguese.

### 2. Google OAuth client

In the [Google Cloud console](https://console.cloud.google.com/), signed in with the account your phone uses:

1. Create a project and enable the **Google Tasks API**.
2. Configure the OAuth consent screen as **External** and add your account as a test user.
3. **Publish** the app (status "In production"). A refresh token from an app in "Testing" status expires after 7 days.
4. Create an OAuth client of type **Desktop app**. Put its id and secret in `GOOGLE_CLIENT_ID` and `GOOGLE_CLIENT_SECRET`.

Then get a refresh token:

```bash
uv run --env-file .env todoist-bridge auth
```

The browser shows "Google hasn't verified this app". Choose Advanced, then continue: you are the developer of this app. Put the printed token in `GOOGLE_REFRESH_TOKEN`.

### 3. Dry run

On the first run the bridge sends every open task in your Google Tasks list to Todoist. Check what it would do first:

```bash
uv run --env-file .env todoist-bridge sync --dry-run
```

Then run it for real once:

```bash
uv run --env-file .env todoist-bridge sync
```

### 4. Deploy to Modal

```bash
uv run modal setup
uv run modal secret create todoist-bridge --from-dotenv .env
uv run modal deploy modal_app.py
```

The function runs every minute and prints its stats as JSON. Read them with `uv run modal app logs todoist-bridge`. A run takes a few seconds, so the monthly cost stays well inside Modal's free credits.

To run the sync somewhere else, call `todoist-bridge sync` from cron or a systemd timer with the same environment variables.

### 5. Teach Gemini to keep the time in the title

Google Tasks stores only the date of a task, not the time. Ask Gemini to keep the time in the title, where Todoist can parse it. Add this to [Saved Info](https://gemini.google.com/saved-info):

```text
Quando eu pedir para criar um lembrete ou uma tarefa, salve sempre no Google Tasks, na lista Minhas tarefas. Mantenha no título a data e a hora exatamente como eu falei, por exemplo "comprar leite amanhã às 16h" ou "ligar para o João em 2 horas". Nunca remova do título as expressões de data e hora.
```

In English:

```text
When I ask you to create a reminder or a task, always save it in Google Tasks, in the My Tasks list. Keep the date and time in the title exactly as I said it, for example "buy milk tomorrow at 4pm" or "call John in 2 hours". Never remove date and time expressions from the title.
```

## Limitations

- The `todoist:<id>` marker is visible in the task notes if you open the Google Tasks app.
- Gemini can complete only tasks that are in Google Tasks, which means tasks due today or overdue.
- A task completed in Google Tasks before the next sync, and never synced, does not reach Todoist.

## Development

```bash
uv sync
uv run pre-commit install
uv run pytest
```

## License

[MIT](LICENSE)
