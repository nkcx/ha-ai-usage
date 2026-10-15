# AI Usage app

Reads your AI subscription limits from the providers' **official command-line
tools** and hands them to the **AI Usage** integration:

| Provider | Tool | What you get |
|---|---|---|
| Claude | Claude Code | Session (5-hour) and weekly limits, per-model weekly limits, extra usage |
| Gemini | Antigravity CLI | 5-hour and weekly limits per model group, AI credits |
| Codex | Codex CLI | ChatGPT plan's 5-hour and weekly Codex limits, credits |

The app never handles your passwords or session tokens itself: each tool signs
in through its own provider's sign-in page, exactly as on a laptop, and keeps
its sign-in in the app's storage.

## Setup

1. On the **Configuration** tab, turn on the providers you use and save.
   Only those tools are downloaded, with each vendor's official installer.
   The first start can take a few minutes: all three tools together take
   about 700 MB.
2. Start the app and open its page (**Open web UI**, or **AI Usage** in the
   sidebar).
3. For each provider, select **Sign in** and follow the tool's own steps:
   - **Claude**: open the link, approve, and paste the code back.
   - **Gemini**: follow the Antigravity CLI's sign-in prompts, then type `/exit`.
   - **Codex**: open the link and enter the code shown. Device-code sign-in
     must be enabled first in ChatGPT → Settings → Security.
4. Home Assistant shows the **AI Usage** integration as discovered: add it.

## Options

| Option | Description |
|---|---|
| Claude / Gemini / Codex | Install that provider's tool and track its limits. Turning one off removes its tool but keeps its sign-in. |
| Check every (minutes) | How often to read usage (5–60, default 5). |

## How it works

Every few minutes the app runs each tool's built-in usage command:
`claude -p /usage`, `agy -p /usage` and `/credits`, and the Codex
app-server's `account/rateLimits/read`. These show your limits without
sending a prompt, so they use no tokens. The app also keeps the tools up to
date (checked daily).

The integration reads the results from the app over Home Assistant's internal
network, authenticated with a token the app creates.

## Backups

Backups include the tools' sign-ins and settings, so a restore doesn't need
signing in again. The tools themselves are left out (they're reinstalled
automatically).

## Security

- The page and its terminal are only reachable through Home Assistant (ingress)
  and only for administrators.
- The usage API is only reachable on Home Assistant's internal network and
  needs the app's token.
- The terminal is a root shell inside this app's container, with access to the
  tools' sign-ins. Use it only to sign in.
