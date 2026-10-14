# AI Usage for Home Assistant

[![Validate](https://github.com/nkcx/ha-ai-usage/actions/workflows/validate.yml/badge.svg)](https://github.com/nkcx/ha-ai-usage/actions/workflows/validate.yml)
[![HACS Custom](https://img.shields.io/badge/HACS-Custom-41BDF5.svg)](https://hacs.xyz/docs/faq/custom_repositories)

See your AI subscription limits in Home Assistant: how much of your session and
weekly limits you've used, when they reset, and your remaining credits, for
**Claude**, **Gemini** and **Codex**.

- One device per provider, with a sensor for every limit and its reset time
- Projections of where each limit is heading, learned from your own usage
  pattern ("at this pace, the session runs out at 15:40")
- Works on dashboards and in automations ("tell me when my weekly Claude limit
  passes 80 %", "warn me when I'm projected to run out")
- New limits (for example a new model's weekly limit) appear automatically

*Independent community project, not affiliated with or endorsed by Anthropic,
Google or OpenAI.*

## How it works

Your subscription limits are only visible to the providers' own tools. Rather
than imitating those tools, this project **runs them**:

```
 AI Usage app (Home Assistant app)                AI Usage integration
 ┌─────────────────────────────────────┐          ┌──────────────────────┐
 │ Claude Code · Antigravity CLI · Codex│ ──────▶ │ devices + sensors    │
 │ (official tools, signed in by you)   │  local  │ for every limit      │
 └─────────────────────────────────────┘   API    └──────────────────────┘
```

1. The **AI Usage app** installs the official command-line tool of each
   provider you turn on. You sign in to each one once, through the provider's
   own sign-in page.
2. Every few minutes the app runs each tool's built-in usage command. These
   commands don't send a prompt, so they use none of your limits.
3. The **AI Usage integration** reads the results from the app over Home
   Assistant's internal network.

The integration never signs in to your AI accounts and never handles their
passwords or session tokens; only the official tools do.

| Provider | Tool | Limits |
|---|---|---|
| Claude (Pro, Max, Team) | Claude Code | Session (5-hour), weekly, per-model weekly, extra usage |
| Gemini (Google AI Pro, Ultra) | Antigravity CLI | 5-hour and weekly per model group, AI credits |
| Codex (ChatGPT Plus, Pro, Business) | Codex CLI | 5-hour and weekly, credits |

## Requirements

- Home Assistant 2026.9 or newer with the app system (Home Assistant OS or
  Supervised). Without it, you can run the app's container yourself; see
  [Without the app system](#without-the-app-system).
- A subscription with at least one provider.
- About 50 MB per tool, except the Antigravity CLI (about 200 MB). Only the
  tools you turn on are downloaded.

## Installation

### 1. The app

1. **Settings → Apps → App store → ⋮ → Repositories**, add
   `https://github.com/nkcx/ha-ai-usage`.
2. Install **AI Usage**.
3. On its **Configuration** tab, turn on the providers you use and save.
4. Start the app. The first start downloads the tools, which can take a few
   minutes.

### 2. The integration

1. In HACS, **⋮ → Custom repositories**, add
   `https://github.com/nkcx/ha-ai-usage` as an **Integration**.
2. Download **AI Usage** and restart Home Assistant.
3. Home Assistant shows **AI Usage** as discovered under **Settings → Devices &
   services**: select **Add**.

### 3. Sign in

Open the app's page (**AI Usage** in the sidebar) and select **Sign in** for
each provider. A terminal opens with that tool's own sign-in:

- **Claude**: open the link, approve, and paste the code back.
- **Gemini**: follow the Antigravity CLI's prompts, then type `/exit`.
- **Codex**: open the link and enter the code. Turn on device-code sign-in
  first in ChatGPT → **Settings → Security**.

Until a provider is signed in, Home Assistant shows a repair for it.

## Entities

Each provider is a device, linked to the **AI Usage app** device.

| Entity | Example | Notes |
|---|---|---|
| Limit used | `sensor.claude_week` = 46 % | One per limit the provider reports |
| Limit resets | `sensor.claude_week_resets` | Timestamp of the next reset |
| Credits / spend | `sensor.gemini_ai_credits` | When the provider reports them |
| Projected at reset | `sensor.claude_week_projected_at_reset` = 71 % | Where the window is heading; attributes: confidence, current and typical rate |
| Runs out | `sensor.claude_session_runs_out` | When it's projected to reach 100 % (unknown if not before the reset) |
| Projected to run out | `binary_sensor.claude_session_projected_to_run_out` | On when it's projected to run out before the reset |
| Status | `sensor.claude_status` | Connected, Not signed in, Installing, Rate limited, Error (diagnostic) |
| Last checked | `sensor.claude_last_checked` | Disabled by default (diagnostic) |
| Refresh usage | `button.ai_usage_app_refresh_usage` | Reads every provider again now |

Limit names come from the provider ("Session", "Week (Fable)", "Gemini Models,
weekly"). When a provider adds a limit, a new entity appears; when it stops
reporting one, that entity becomes unavailable.

### Projections

Projections don't just divide usage by time: usage comes in bursts. The
integration learns your typical usage for every hour of the week (recent
weeks count most), then adds how much heavier or lighter than usual you're
using it right now, fading that out over the next hours. The **confidence**
attribute is *low* for the first week (only the current pace is used),
*medium* after one week and *high* after three. History is kept by the
integration itself (about 8 weeks), independently of the recorder.

### Data updates

The app reads the tools every 5 minutes (configurable, 5–60) and the
integration asks the app every minute. **Refresh usage** reads them right away.

## Examples

Notify when the weekly Claude limit passes 80 %:

```yaml
triggers:
  - trigger: numeric_state
    entity_id: sensor.claude_week
    above: 80
actions:
  - action: notify.mobile_app_phone
    data:
      message: "Claude weekly limit at {{ states('sensor.claude_week') }} %"
```

## Known limitations

- **The usage commands aren't public APIs.** The providers can change them in
  any tool update. When that happens the provider's status shows **Error** and
  its limits become unavailable (never wrong numbers) until this project is
  updated.
- **Gemini** shows no account name: the Antigravity CLI doesn't report one.
- **Codex** needs a ChatGPT sign-in; API-key sign-ins have no plan limits.
- One sign-in per provider per app.

## Troubleshooting

- **A provider stays "Not signed in"**: open the app's page and sign in again.
  The tools keep their sign-ins in the app's storage, which backups include.
- **"Installing" for a long time**: check the app's **Log** tab. The download
  is retried when the app restarts.
- **Status "Error"**: the message on the app's page says what the tool
  returned. If a tool update changed its output, please open an issue with the
  diagnostics (**⋮ → Download diagnostics** on the integration; account
  details are removed).

## Without the app system

The app is a normal container image built from [`ai_usage_app/`](ai_usage_app).
Run it with a volume on `/data`, put `{"claude": true, "gemini": false,
"codex": false, "interval_minutes": 5}` in `/data/options.json`, sign in with
`docker exec -it <container> ai-usage-login claude`, and add the integration
manually with the container's address, port 8080 and the token in
`/data/api_token`.

## Removal

1. **Settings → Devices & services → AI Usage → ⋮ → Delete**.
2. Remove the integration in HACS.
3. Uninstall the **AI Usage** app. This also deletes the tools' sign-ins.

## License and trademarks

MIT, see [LICENSE](LICENSE). Claude and Claude Code are trademarks of
Anthropic; Gemini and Antigravity of Google; Codex and ChatGPT of OpenAI. This
project isn't affiliated with or endorsed by any of them.
