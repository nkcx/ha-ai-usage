# AI Usage for Home Assistant

[![Validate](https://github.com/nkcx/ha-ai-usage/actions/workflows/validate.yml/badge.svg)](https://github.com/nkcx/ha-ai-usage/actions/workflows/validate.yml)
[![HACS Custom](https://img.shields.io/badge/HACS-Custom-41BDF5.svg)](https://hacs.xyz/docs/faq/custom_repositories)
![Quality scale: Platinum](https://img.shields.io/badge/quality%20scale-platinum-e5e4e2)

See your AI subscription limits in Home Assistant: how much of your session and
weekly limits you've used, when they reset, and when you're on track to run
out. Works with **Claude**, **Gemini** and **Codex** plans.

- A device per provider, with a sensor for every limit and its reset time
- Projections that learn your usual usage pattern: "at this pace, the session
  runs out at 15:40"
- Usage history in Home Assistant's normal history graphs and statistics
- Dashboards and automations: "warn me when I'm projected to run out", "tell
  me when my weekly limit resets"

*Independent community project, not affiliated with or endorsed by Anthropic,
Google or OpenAI.*

## Contents

- [Overview](#overview)
- [Requirements](#requirements)
- [Installation](#installation)
- [Using it](#using-it)
- [Troubleshooting](#troubleshooting)
- [Known limitations](#known-limitations)
- [Removal](#removal)
- [Detailed architecture](#detailed-architecture)
- [Running the app without the app system](#running-the-app-without-the-app-system)
- [Quality](#quality)

## Overview

AI Usage comes in **two pieces that work together**. You install both.

| Piece | What it is | What it does |
|---|---|---|
| **AI Usage app** | A Home Assistant app (formerly called add-on), installed from this repository in **Settings → Apps** | Runs the providers' own command-line tools, which you sign in to once, and reads your usage from them every few minutes |
| **AI Usage integration** | A custom integration, installed with **HACS** | Turns what the app reads into Home Assistant devices, sensors and projections |

```
 AI Usage app                                  AI Usage integration
 ┌────────────────────────────────────┐        ┌──────────────────────────────┐
 │ Claude Code · Antigravity CLI ·    │ usage  │ devices, sensors, reset      │
 │ Codex CLI (signed in by you)       │ ─────▶ │ times, projections           │
 └────────────────────────────────────┘        └──────────────────────────────┘
```

**Why two pieces?** Subscription limits are only visible to each provider's own
tools: there's no public API for them. Rather than imitating those tools, which
the providers' terms forbid, the app installs and runs the real ones. An
integration can't do that: it's Python code inside Home Assistant itself and
can't install programs or host a sign-in. An app is a separate container that
can.

**What the integration never does:** sign in to your AI accounts, or see your
passwords or session tokens. Only the official tools, inside the app, do. Your
usage is read with each tool's built-in usage command, which doesn't send a
prompt and doesn't count against your limits.

| Provider | Plans | Tool in the app | Limits |
|---|---|---|---|
| Claude | Pro, Max, Team | Claude Code | Session (5-hour), weekly, per-model weekly, extra usage |
| Gemini | Google AI Pro, Ultra | Antigravity CLI | 5-hour and weekly per model group, AI credits |
| Codex | ChatGPT Plus, Pro, Business | Codex CLI | 5-hour and weekly, per-model limits, credits |

## Requirements

- Home Assistant **2026.9** or newer, with the app system (Home Assistant OS or
  Supervised). Other installations can run the app's container by hand; see
  [Running the app without the app system](#running-the-app-without-the-app-system).
- A subscription with at least one provider.
- Disk space for the tools you turn on: about 700 MB for all three. Only the
  tools you turn on are downloaded, and backups leave them out.
- The recorder (on by default) for projections and history.

## Installation

### 1. Install the app

1. **Settings → Apps → App store → ⋮ → Repositories**, add
   `https://github.com/nkcx/ha-ai-usage`.
2. Install **AI Usage**.
3. On its **Configuration** tab, turn on the providers you use and save.
4. Start the app. The first start downloads the tools, which can take a few
   minutes.

### 2. Install the integration

1. In HACS: **⋮ → Custom repositories**, add `https://github.com/nkcx/ha-ai-usage`
   as an **Integration**.
2. Download **AI Usage** and restart Home Assistant.
3. **Settings → Devices & services** shows **AI Usage** as discovered: select
   **Add**. (The app announces itself; there's nothing to type.)

### 3. Sign in to each provider

Open **AI Usage** in the sidebar (the app's page). Each provider has a card
with a **Sign in** button, which opens a terminal running that tool's own
sign-in:

- **Claude**: open the link (it opens in a new tab), approve, copy the code,
  paste it into the terminal and press Enter. The pasted code isn't shown on
  screen.
- **Gemini**: open the link, approve, paste the code into the terminal and
  press Enter. Once the Antigravity CLI is running, type `/exit`.
- **Codex**: first turn on device-code sign-in in ChatGPT → **Settings →
  Security**. Then open the link and enter the code shown in the terminal.

Until a provider is signed in, Home Assistant shows a repair for it. After
signing in, its sensors appear within a minute.

## Using it

### Entities

Each provider is a device (**Claude**, **Gemini**, **Codex**), linked to an
**AI Usage app** device. The limits come from the provider, so the exact set
depends on your plan; these are typical.

**Limits** (one set per limit the provider reports):

| Entity ID | Name | Example |
|---|---|---|
| `sensor.claude_session` | Claude Session | `46` % |
| `sensor.claude_week` | Claude Week | `38` % |
| `sensor.claude_week_fable` | Claude Week (Fable) | `0` % |
| `sensor.claude_extra_usage` | Claude Extra usage (only when extra usage is on) | `4.50` USD |
| `sensor.gemini_gemini_models_5_hour` | Gemini Gemini Models, 5-hour | `2.8` % |
| `sensor.gemini_gemini_models_weekly` | Gemini Gemini Models, weekly | `2.1` % |
| `sensor.gemini_claude_and_gpt_models_5_hour` | Gemini Claude and GPT models, 5-hour | `0` % |
| `sensor.gemini_claude_and_gpt_models_weekly` | Gemini Claude and GPT models, weekly | `0` % |
| `sensor.gemini_ai_credits` | Gemini AI credits | `120` credits |
| `sensor.codex_5_hour` | Codex 5-hour | `42` % |
| `sensor.codex_week` | Codex Week | `5` % |
| `sensor.codex_credits` | Codex Credits | `12.5` credits |

**For each limit with a reset time** (shown for `sensor.claude_session`; every
limit above with a % value gets the same four):

| Entity ID | Name | Example |
|---|---|---|
| `sensor.claude_session_resets` | Claude Session resets | `2026-10-15T01:30:00+00:00` (shown as "in 2 hours") |
| `sensor.claude_session_projected_at_reset` | Claude Session projected at reset | `71` % (attributes: `confidence` = `medium`, `current_rate` = `9.5`, `typical_rate` = `6.2` %/h) |
| `sensor.claude_session_runs_out` | Claude Session runs out | `2026-10-14T23:10:00+00:00`, or unknown when not projected to run out |
| `binary_sensor.claude_session_projected_to_run_out` | Claude Session projected to run out | `on` / `off` |

**Per provider, and the app:**

| Entity ID | Name | Example |
|---|---|---|
| `sensor.claude_status` | Claude Status | `Connected`, `Not signed in`, `Installing`, `Rate limited`, `Error` |
| `sensor.claude_last_checked` | Claude Last checked (disabled by default) | `2026-10-14T20:00:00+00:00` |
| `button.ai_usage_app_refresh_usage` | AI Usage app Refresh usage | Press to read every provider again now |

When a provider adds a limit (for example a new model's weekly limit), its
entities appear automatically. A limit the provider stops reporting becomes
unavailable. Turning a provider off in the app removes its device.

### Projections

A projection answers "where will this limit be when it resets, and will I run
out first?". It doesn't just divide usage by time, because usage comes in
bursts. Instead it learns how much you usually use in each hour of the week,
from the sensor's history in the recorder, with recent weeks counting most.
It then adds how much heavier or lighter than usual you're using it right
now, and assumes that difference fades over the next hours.

The **confidence** attribute says how much history it had to go on: *low* for
the first week (only the current pace counts), *medium* after one week and
*high* after three.

### Updates

The app reads the tools every 5 minutes (configurable from 5 to 60). The
integration asks the app every minute and recalculates projections whenever
the app has read new values. **Refresh usage** reads everything right away.

### Examples

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

Warn when the session is projected to run out:

```yaml
triggers:
  - trigger: state
    entity_id: binary_sensor.claude_session_projected_to_run_out
    to: "on"
actions:
  - action: notify.mobile_app_phone
    data:
      message: >
        At this pace your Claude session runs out
        {{ as_timestamp(states('sensor.claude_session_runs_out')) | timestamp_custom('%H:%M') }}.
```

A dashboard tile for a limit:

```yaml
type: tile
entity: sensor.claude_week
name: Claude week
state_content:
  - state
  - last_changed
```

## Troubleshooting

- **A provider stays "Not signed in"**: open the app's page and select **Sign
  in** again. Sign-ins are kept in the app's storage, including in backups.
- **Nothing appears when I paste the Claude code**: that's normal; Claude
  Code doesn't show the code. Paste it and press Enter.
- **Status "Installing" for a long time**: check the app's **Log** tab.
  Downloads are retried when the app restarts.
- **Status "Error"**: the app's page shows the message the tool returned. If a
  tool update changed its output, please open an issue with the integration's
  diagnostics (**⋮ → Download diagnostics**; account details are removed).
- **No projections**: projections need the recorder. If you exclude these
  sensors from the recorder, projections only use the current value.

## Known limitations

- **The usage commands aren't public APIs.** A provider can change them in any
  tool update. The provider's status then shows **Error** and its limits become
  unavailable (never wrong numbers) until this project is updated.
- **Gemini** shows no account name: the Antigravity CLI doesn't report one.
- **Codex** needs a ChatGPT sign-in. A sign-in with an API key has no plan limits.
- One sign-in per provider per app.
- Projections need some history: for the first week they only extend your
  current pace.

## Removal

1. **Settings → Devices & services → AI Usage → ⋮ → Delete**.
2. Remove the integration in HACS and restart Home Assistant.
3. Uninstall the **AI Usage** app. This also deletes the tools' sign-ins.

---

## Detailed architecture

```
 ┌──────────────────────── AI Usage app (container) ────────────────────────┐
 │ installer   official installers → /data/home/.local (enabled tools only)  │
 │ collector   every N min, per enabled provider: run the tool's usage       │
 │             command → parse → UsageReport                                 │
 │ API :8080   GET /api/v1/reports · GET /api/v1/health · POST /api/v1/refresh│
 │             (bearer token; Home Assistant's internal network only)        │
 │ ingress     status page + terminal (ttyd on localhost, proxied)           │
 │ discovery   → Supervisor → "AI Usage discovered" in Home Assistant         │
 └──────────────────────────────────────────▲────────────────────────────────┘
                                            │ poll every 60 s
 ┌─────────────────── AI Usage integration (custom_components/ai_usage) ─────┐
 │ coordinator → devices + sensors per meter · repairs · diagnostics         │
 │ projections ← recorder: recorded states (last day) + hourly statistics     │
 └───────────────────────────────────────────────────────────────────────────┘
```

### Reading usage

| Provider | Command | Output used |
|---|---|---|
| Claude | `claude auth status --json` and `claude -p /usage --output-format stream-json --verbose` | Account and plan; the `usage_report.rate_limits` object (limits with percent and reset time, extra usage) |
| Gemini | `agy -p /usage --output-format json` and `agy -p /credits --output-format json` | `command.data`: model groups with 5-hour and weekly `remaining_fraction` and reset times; remaining credits |
| Codex | `codex app-server` (JSON-RPC over stdin/stdout): `initialize`, `account/read`, `account/rateLimits/read` | Account and plan; primary and secondary windows per limit, credits |

None of these send a prompt. A signed-out Antigravity CLI waits for a sign-in
code instead of failing, so the app stops it as soon as it asks.

Every provider's output becomes the same **usage report**: an account (ID, name,
plan) and a list of **meters**. A meter has a kind (`window_used`, `balance`,
`spend`, `quota_remaining`), a value and unit, and optionally a reset time,
window length, group and model. The integration builds entities from meters
alone, so a new provider is a new parser, not new entities. The shared code
(`custom_components/ai_usage/core/`) has no Home Assistant imports; the app
ships an identical copy, checked in CI.

### The app

- **Image**: Home Assistant's `base-python` (Python 3.14, Alpine). No AI tools
  are in the image. The enabled ones are installed at startup with their
  vendors' official installers and updated daily.
- **Storage** (`/data`): `home/` holds the tools' sign-ins and settings;
  `home/.local/` holds the tools themselves and is excluded from backups.
  `api_token` and `instance_id` identify the app to the integration.
- **Security**: the ingress server accepts only Home Assistant's ingress
  proxy, and the terminal listens on localhost behind it. The usage API has no
  host port and needs the app's token.
- **Discovery**: on start, the app sends its address, port and token to the
  Supervisor, which offers the integration's setup.

### The integration

- One config entry per app installation, identified by the app's instance ID
  (set up from discovery or manually), with reauthentication and
  reconfiguration.
- A coordinator polls the app every 60 seconds. Errors raise repairs (provider
  needs signing in, tool failed to install), and devices of providers turned
  off are removed.
- **Projections** (`core/forecast.py`): consumption per hour, from each limit
  sensor's recorded states over the last day (each value at the time it was
  reported), the hourly maxima of its long-term statistics before that (up to
  8 weeks), and the latest value. An unchanged value counts as no use (the
  recorder only stores changes); an unavailable stretch stays unknown. An
  hour-of-week profile is averaged with weights halving every 2 weeks and
  pulled toward the hour-of-day average when there's little data. Hours
  without statistics are unknown, not zero. The current rate's deviation from
  the profile decays with a 1.5-hour half-life. Stepping hour by hour to the
  reset time gives the projected value and the time it reaches 100 %.

## Running the app without the app system

The app is a normal container built from [`ai_usage_app/`](ai_usage_app), but
this isn't a supported setup. Run it with a persistent volume on `/data` and
create `/data/options.json`:

```json
{"claude": true, "gemini": true, "codex": false, "interval_minutes": 5}
```

| Key | Type | Meaning |
|---|---|---|
| `claude`, `gemini`, `codex` | boolean | Install that provider's tool and read its usage |
| `interval_minutes` | integer, 5–60 | How often to read usage |

Sign in with `docker exec -it <container> ai-usage-login claude` (or `gemini`,
`codex`). Then add the integration manually with the container's address,
port 8080 and the token in `/data/api_token`.

## Quality

The integration meets every rule of Home Assistant's
[integration quality scale](https://developers.home-assistant.io/docs/core/integration-quality-scale/)
up to **Platinum**. Rules that don't apply are exempt with a reason in
[`quality_scale.yaml`](custom_components/ai_usage/quality_scale.yaml).

| Tier | Highlights |
|---|---|
| Bronze | Config flow (discovery and manual), unique IDs, entity names, runtime data, tested setup |
| Silver | Reauthentication, unloading, unavailable handling, parallel-update limits, test coverage above 95 % |
| Gold | Devices, diagnostics (redacted), discovery, dynamic and stale devices, translations for entities, icons and errors, repairs, reconfiguration |
| Platinum | Fully async, shared HTTP session, strict typing |

Continuous integration runs HACS and hassfest validation, ruff, strict mypy
(integration and app), the test suite (coverage above 95 %), and builds the app
image and checks that every tool installs and reports correctly.

## License and trademarks

MIT, see [LICENSE](LICENSE). Claude and Claude Code are trademarks of Anthropic;
Gemini and Antigravity of Google; Codex and ChatGPT of OpenAI. This project
isn't affiliated with or endorsed by any of them.
