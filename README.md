# todoist-bridge

Use Todoist hands-free from Android Auto, Gemini and Google Assistant, by syncing Google Tasks and Todoist.

Gemini (Android Auto, Google Assistant) creates, reads aloud and completes Google Tasks, but it has no Todoist integration. todoist-bridge runs every minute and keeps one Google Tasks list in sync with Todoist, so you can say "remind me to buy milk tomorrow at 4pm" in the car and find the task in Todoist.

## How it works

- **Voice capture:** a new task in Google Tasks goes to Todoist through [Quick Add](https://www.todoist.com/help/articles/use-task-quick-add-in-todoist-va4Lhpzz), so Todoist parses dates, `#project`, `@label` and `p1` in your account language. Spoken prefixes such as "remind me to" or "me lembra de" are removed first.
- **Today's list:** Todoist tasks due today or overdue appear in Google Tasks, so "what are my tasks for today?" reads your Todoist tasks.
- **Completion:** completing a task on either side completes it on the other.
- **Rescheduling:** when a task the bridge copied into Google Tasks moves to a future date in Todoist, the bridge deletes the copy. A task you created by voice stays in Google Tasks until it is done in Todoist, because Google may repeat it: deleting a recurring Google task makes Google create its next instance. While it waits, it takes its title and date from Todoist.
- **Renaming in Google Tasks:** a new title in Google Tasks goes to Todoist through Quick Add, so "Marcar PTO às 16h" moves the Todoist task to 16:00. This is the only way to change a time from Google: the Google Tasks API keeps the date of a task and drops its time.

Todoist is the source of truth. Google Tasks holds only what is due now. If you change a title in both apps between two syncs, the most recent change wins. The Todoist id of each mirrored task lives in the Google task notes (`todoist:<id>`), so the bridge needs no database.

## Setup

You need [uv](https://docs.astral.sh/uv/getting-started/installation/), a Todoist account, the Google account your phone uses and a [Modal](https://modal.com) account. Modal's free monthly credits cover the sync.

```bash
git clone https://github.com/rodbv/todoist-bridge && cd todoist-bridge
uv sync
uv run todoist-bridge setup
```

The wizard asks for each credential and opens the page where you find it. It checks each value, shows what the first sync will change, deploys to Modal and prints the text to paste into Gemini. Run it again at any time to fix a credential or deploy again.

To see every step first, run `uv run todoist-bridge setup --demo`. The demo accepts any value, writes nothing to your `.env` and never calls Todoist, Google or Modal.

### Set up with an AI agent

If you use an agent that runs commands, such as Claude Code or Gemini CLI, paste this prompt into it:

```text
Set up https://github.com/rodbv/todoist-bridge on this computer. Install uv if it is missing (https://docs.astral.sh/uv/getting-started/installation/), clone the repository and run "uv sync" in it. Then tell me to run "uv run todoist-bridge setup" myself, in a terminal inside the repository folder, because the wizard asks for API tokens and secrets. Never ask me to paste a token, a client secret or the .env file into this chat, and never read the .env file. If the wizard shows an error, help me fix it from the error message and the README.
```

## Manual setup

```bash
cp .env.example .env
```

### Todoist

Copy your API token from Todoist Settings, Integrations, Developer into `TODOIST_API_TOKEN`.

Quick Add parses dates in the language of your Todoist account. If you speak Portuguese to your assistant, set Todoist to Portuguese.

### Google OAuth client

In the [Google Cloud console](https://console.cloud.google.com/), signed in with the account your phone uses:

1. Create a project and enable the **Google Tasks API**.
2. Configure the OAuth consent screen as **External** and add your account as a test user.
3. **Publish** the app (status "In production"). A refresh token from an app in "Testing" status expires after 7 days. Publishing requires a home page URL and a privacy policy URL on the Branding page: use https://github.com/rodbv/todoist-bridge and its [PRIVACY.md](PRIVACY.md), and add `github.com` to the authorized domains. Leave the logo empty, because a logo requires brand verification.
4. Create an OAuth client of type **Desktop app**. Put its id and secret in `GOOGLE_CLIENT_ID` and `GOOGLE_CLIENT_SECRET`.

Then get a refresh token:

```bash
uv run --env-file .env todoist-bridge auth
```

The browser shows "Google hasn't verified this app". Choose Advanced, then continue: you are the developer of this app. Put the printed token in `GOOGLE_REFRESH_TOKEN`.

### Dry run

On the first run the bridge sends every open task in your Google Tasks list to Todoist. Check what it would do first:

```bash
uv run --env-file .env todoist-bridge sync --dry-run
```

Then run it for real once:

```bash
uv run --env-file .env todoist-bridge sync
```

### Deploy to Modal

```bash
uv run modal setup
uv run modal secret create todoist-bridge --from-dotenv .env
uv run modal deploy modal_app.py
```

The function runs every minute and prints its stats as JSON. Read them with `uv run modal app logs todoist-bridge`. A run takes a few seconds, so the monthly cost stays well inside Modal's free credits.

To run the sync somewhere else, call `todoist-bridge sync` from cron or a systemd timer with the same environment variables.

## Teach Gemini to save tasks with the time in the title

Google Tasks stores only the date of a task, not the time, and Gemini removes the time from the title unless told otherwise. The setup wizard prints this text in your Todoist language. Add it to [Saved Info](https://gemini.google.com/saved-info), so that any command with a date becomes a task whose title Todoist can parse:

```text
Quando eu disser uma frase no imperativo com uma data ou hora, absoluta ou relativa, crie uma tarefa no Google Tasks, na lista Minhas tarefas, sem pedir confirmação. Exemplos: "ligar pro dentista amanhã às 9h30", "pagar a conta sexta", "regar as plantas em 10 minutos", "tirar o lixo toda quinta às 22h". Use a frase inteira como título da tarefa, com a data, a hora e a repetição exatamente como eu falei. Nunca remova do título as expressões de data, hora ou repetição. Nunca crie tarefas recorrentes no Google Tasks: escreva a repetição no título, por exemplo "tomar remédio todo dia às 22h15", e o Todoist cuida da repetição. Não use o Google Keep nem lembretes do Assistente.
```

In English:

```text
When I say an imperative sentence with a date or time, absolute or relative, create a task in Google Tasks, in the My Tasks list, without asking for confirmation. Examples: "call the dentist tomorrow at 9:30am", "pay the bill on Friday", "water the plants in 10 minutes", "take out the trash every Thursday at 10pm". Use the whole sentence as the task title, with the date, time and recurrence exactly as I said them. Never remove date, time or recurrence expressions from the title. Never create recurring tasks in Google Tasks: write the recurrence in the title, for example "take my medicine every day at 10:15pm", and Todoist handles the repetition. Do not use Google Keep or Assistant reminders.
```

Gemini follows Saved Info most of the time, not always. If a task reaches Todoist without its time, Gemini removed the time before the bridge saw it.

## Limitations

- If Gemini creates a recurring Google task anyway, each new instance links to the same recurring Todoist task, as long as Todoist parsed the recurrence too.
- If you change only the date or time field of a task in the Google Tasks app, or Gemini does, Todoist keeps its own date and time. Rename the task with the new date and time instead.
- If you rename a recurring task in Google Tasks with a one-time date, such as "tomar remédio às 23h", Todoist takes the new title and keeps the recurrence.
- The `todoist:<id>` marker is visible in the task notes if you open the Google Tasks app.
- Gemini can complete only tasks that are in Google Tasks: Todoist tasks due today or overdue, plus the tasks you created by voice.
- A task completed in Google Tasks before the next sync, and never synced, does not reach Todoist.
- If you mark a completed Google task as not done, the bridge sends it to Todoist as a new task. Reopen it in Todoist instead.
- Quick Add treats some words as commands and removes them from the title: a date or time ("23h"), or "no date" in your account language ("sem data" in Portuguese).
- A Todoist task with a deadline but no due date does not appear in Google Tasks.

## Development

```bash
uv sync
uv run pre-commit install
uv run pytest
```

## License

[MIT](LICENSE)
