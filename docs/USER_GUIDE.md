# User Guide — PersonalAsst

## Getting Started

Your Personal Assistant lives in Telegram. Just open your chat with the bot and start talking.

### First Time Setup

1. Open Telegram and find your bot (the name you gave it in @BotFather).
2. Send `/start` — the bot will guide you through setup:
   - What's your name?
   - What should the assistant be called?
   - How should it communicate? (Professional / Casual / Friendly / Brief)
   - Connect Google Workspace? (optional)

## What Can It Do?

### Chat Naturally
Just type what you need. No special commands required.
- "What's on my calendar today?"
- "Send an email to Sarah about the project update"
- "Find the budget spreadsheet on my Drive"
- "Create a spreadsheet with my monthly budget"
- "Remind me every Monday at 9am to review my goals"
- "Remind me today at 3pm to pick up Betty"
- "Remember that I prefer morning meetings"
- "Search for the best Italian restaurants near me"

### Google Workspace (8 services, 45 tools)

| Service | What You Can Do |
|---------|----------------|
| **Gmail** | Read, search, draft, send, reply to emails; manage filters |
| **Calendar** | View today's events, create/update/delete events |
| **Tasks** | List task lists, create/update/complete/delete tasks |
| **Drive** | Search, list, upload, download, share, trash, manage files |
| **Docs** | Search, create, read, edit, find-replace, export documents |
| **Sheets** | Create, read, update, append, clear spreadsheet data |
| **Slides** | Create, view, batch update, get page thumbnails |
| **Contacts** | List, search, view, manage contacts |

### Scheduling & Reminders

The assistant can create reminders and recurring tasks:
- **One-shot:** "Remind me today at 3pm to call the dentist"
- **Recurring:** "Remind me every Monday at 9am to review my goals"
- **Morning brief:** "Set up a daily morning brief at 8am"
- **Interval:** "Check my email every 2 hours"

Reminders persist across container restarts and are delivered via Telegram.

### Memory

The assistant remembers your preferences and important information:
- "Remember that I prefer decaf coffee"
- "What do you remember about my team?"
- "Forget everything about Project X"

It also maintains conversation context — it remembers what you discussed in recent messages, including tool results.

### Telegram Commands

| Command | What It Does |
|---------|-------------|
| `/start` | Initial setup wizard |
| `/help` | Show all available commands |
| `/persona` | View or change assistant personality |
| `/persona name Luna` | Change assistant's name |
| `/persona style casual` | Change communication style |
| `/schedules` | List all scheduled tasks |
| `/cancel <id>` | Cancel a scheduled task |
| `/orgs` | Manage organizations (list/create/info/pause/resume/delete) |
| `/tools` | List available tools |
| `/memory` | See what the assistant remembers about you |
| `/forget <topic>` | Ask assistant to forget something |
| `/stats` | View usage statistics and costs |
| `/connect google` | Connect your Google Workspace |

### Organization Management

Use `/orgs` to manage organizations from Telegram:

- `/orgs` — list organizations
- `/orgs create` — start a guided 3-step creation wizard
- `/orgs info <id>` — show organization details
- `/orgs pause <id>` — pause/deactivate an organization
- `/orgs resume <id>` — reactivate an organization
- `/orgs delete <id>` — delete an organization

### Approval Flows

When the assistant wants to do something important (like sending an email), it will ask you first:

```
"I've drafted this email to Sarah:
Subject: Project Update
Body: Hi Sarah, here's the latest...

Would you like to send it?"
```

Just say "send it", "yes", or "cancel".

### Voice Messages

Send a voice message in Telegram — the assistant will transcribe it and respond as text.

### File Sharing

- **Send a file** to the bot → it can upload to Google Drive
- **Ask for a file** → it will find and send it from Google Drive

### Error Diagnostics

If something goes wrong, the assistant can help diagnose:
- It will explain what happened in plain language
- It can analyze error logs and identify likely root causes
- It will suggest what to investigate (but won't fabricate fixes)

## Tips

- **Be specific:** "Email Sarah about the Q2 budget" works better than "Send an email"
- **Use natural time:** "Every weekday at 8am", "today at 3pm", and "Next Tuesday" all work
- **Chain requests:** "Check my calendar for tomorrow and email me a summary"
- **Give feedback:** The assistant learns from your preferences over time
- **Say "send it"** after reviewing a draft — no need to repeat the full request
- **Say "retry"** if something fails — the assistant will try again

## Privacy & Safety

- Your data stays on your own server (self-hosted Docker)
- No data sent to third-party services (except OpenAI for AI responses and Google for Workspace)
- You can delete any memory with `/forget`
- All destructive actions require your approval first
- Email addresses in responses are only allowed when you explicitly asked for email operations
- Usage is tracked — check with `/stats`
