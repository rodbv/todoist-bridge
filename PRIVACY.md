# Privacy policy

todoist-bridge is software you run yourself, on your own Modal account or machine. Its author operates no server and receives none of your data.

- The bridge reads, creates, renames, completes and deletes tasks in one Google Tasks list. In your Todoist account it reads, creates, updates and closes tasks. It uses this access only to synchronize the two task lists.
- The bridge requests one Google scope: `https://www.googleapis.com/auth/tasks`.
- Your OAuth client secret, refresh token and Todoist API token stay in your own `.env` file and in your own Modal Secret.
- The bridge stores no task data outside Google Tasks and Todoist. When it copies a Todoist task into Google Tasks, it writes a `todoist:<id>` marker in the notes of that Google task.
- To revoke access:
  - remove the app with the name you gave your OAuth consent screen at https://myaccount.google.com/permissions
  - issue a new API token in Todoist settings, which invalidates the old one
  - stop the scheduled sync with `modal app stop todoist-bridge`
  - delete your Modal Secret and your `.env` file
