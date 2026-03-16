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
- "Remind me every Monday at 9am to review my goals"

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
| `/tools` | List available tools |
| `/memory` | See what the assistant remembers about you |
| `/forget <topic>` | Ask assistant to forget something |
| `/stats` | View usage statistics and costs |
| `/connect google` | Connect your Google Workspace |
| `/feedback` | Rate the last interaction |

### Approval Flows

When the assistant wants to do something important (like sending an email), it will ask you first:

```
"I've drafted this email to Sarah:
Subject: Project Update
Body: Hi Sarah, here's the latest...

[Approve] [Edit] [Cancel]"
```

Just tap a button to respond.

### Voice Messages

Send a voice message in Telegram — the assistant will transcribe it and respond as text.

### File Sharing

- **Send a file** to the bot → it can upload to Google Drive
- **Ask for a file** → it will find and send it from Google Drive

## Tips

- **Be specific:** "Email Sarah about the Q2 budget" works better than "Send an email"
- **Use natural time:** "Every weekday at 8am" and "Next Tuesday at 3pm" both work
- **Give feedback:** The assistant learns from your preferences over time
- **Say "undo"** if the assistant does something you didn't want

## Privacy & Safety

- Your data stays on your own server (self-hosted Docker)
- No data sent to third-party services (except OpenAI for AI responses and Google for Workspace)
- You can delete any memory with `/forget`
- All destructive actions require your approval first
- Usage is tracked — check with `/stats`
