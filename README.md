# AI Usage for Home Assistant

[![Validate](https://github.com/nkcx/ha-ai-usage/actions/workflows/validate.yml/badge.svg)](https://github.com/nkcx/ha-ai-usage/actions/workflows/validate.yml)
[![HACS Custom](https://img.shields.io/badge/HACS-Custom-41BDF5.svg)](https://hacs.xyz/docs/faq/custom_repositories)

Shows your AI subscription usage in Home Assistant: session and weekly limits, how much
is used and when each limit resets.

> [!WARNING]
> Work in progress. Not ready for use yet.

## How it works

The integration only uses sanctioned access:

1. **Official APIs** (documented, key-based) where a provider offers them; the
   integration polls them directly.
2. **Official CLIs** where no API exposes the data (subscription limits). The companion
   **AI Usage app** installs the providers' own command-line tools, which you sign in to
   once, and reads usage from them. The integration never signs in to your AI accounts
   itself and never handles their session tokens.

Planned for the first release: Claude (Claude Code), Gemini (Antigravity CLI) and
Codex (ChatGPT plan limits, Codex CLI).

## License and trademarks

MIT, see [LICENSE](LICENSE). Independent community project, not affiliated with or
endorsed by Anthropic, Google or OpenAI. Product names are trademarks of their owners.
