"""Modal runner: sync Google Tasks and Todoist every minute.

Deploy with ``uv run modal deploy modal_app.py``. Credentials come from the Modal
Secret ``todoist-bridge``, created from the same variables as ``.env``.
"""

import json
import logging

import modal

app = modal.App("todoist-bridge")

image = (
    modal.Image.debian_slim(python_version="3.15")
    .uv_sync(extra_options="--no-dev")
    .add_local_python_source("todoist_bridge")
)


@app.function(
    image=image,
    secrets=[modal.Secret.from_name("todoist-bridge")],
    schedule=modal.Cron("* * * * *"),
    timeout=50,
    max_containers=1,
)
def sync_every_minute() -> None:
    from todoist_bridge.cli import run_sync

    logging.basicConfig(level=logging.INFO, format="%(levelname)s %(name)s: %(message)s")
    stats = run_sync()
    print(json.dumps(stats.as_dict(), ensure_ascii=False))
