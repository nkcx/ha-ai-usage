"""The ingress status page."""

from __future__ import annotations

from datetime import UTC, datetime
from html import escape
from typing import Any

NAMES = {"claude": "Claude", "gemini": "Gemini", "codex": "Codex"}
CLIS = {"claude": "Claude Code", "gemini": "Antigravity CLI", "codex": "Codex CLI"}
SIGN_IN_HELP = {
    "claude": "Opens Claude Code's sign-in. Open the link it shows, approve, then paste the code back.",
    "gemini": "Starts the Antigravity CLI. Follow its sign-in prompts, then type /exit.",
    "codex": (
        "Starts a device-code sign-in: open the link, enter the code. Device-code sign-in must "
        "be enabled in ChatGPT → Settings → Security."
    ),
}
STATUS_TEXT = {
    "ok": "Connected",
    "signed_out": "Not signed in",
    "not_installed": "Installing…",
    "rate_limited": "Rate limited",
    "error": "Error",
}

_STYLE = """
:root{--bg:#fafafa;--card:#fff;--text:#212121;--muted:#727272;--line:#e0e0e0;--track:#eceff1;
--accent:#03a9f4;--ok:#43a047;--warn:#ff9800;--bad:#db4437;--radius:12px}
@media (prefers-color-scheme:dark){:root{--bg:#111;--card:#1c1c1c;--text:#e1e1e1;--muted:#9b9b9b;
--line:#333;--track:#2c2c2c}}
*{box-sizing:border-box}body{margin:0;background:var(--bg);color:var(--text);
font:14px/1.45 Roboto,system-ui,-apple-system,"Segoe UI",sans-serif}
main{max-width:960px;margin:0 auto;padding:24px 16px}
header{display:flex;flex-wrap:wrap;align-items:baseline;gap:8px 16px;margin-bottom:20px}
h1{font-size:22px;font-weight:500;margin:0;flex:1}
.sub{color:var(--muted)}
.grid{display:grid;gap:16px;grid-template-columns:repeat(auto-fill,minmax(280px,1fr))}
.card{background:var(--card);border:1px solid var(--line);border-radius:var(--radius);padding:16px;
display:flex;flex-direction:column;gap:12px}
.card.off{opacity:.7}
.head{display:flex;align-items:center;gap:8px}
.head h2{font-size:16px;font-weight:500;margin:0;flex:1}
.chip{font-size:12px;padding:2px 8px;border-radius:999px;background:var(--track);color:var(--muted)}
.chip.ok{background:color-mix(in srgb,var(--ok) 15%,transparent);color:var(--ok)}
.chip.signed_out,.chip.rate_limited,.chip.not_installed{background:color-mix(in srgb,var(--warn) 18%,transparent);color:var(--warn)}
.chip.error{background:color-mix(in srgb,var(--bad) 15%,transparent);color:var(--bad)}
.meta{color:var(--muted);font-size:13px}
.meter .row{display:flex;justify-content:space-between;gap:8px;font-size:13px}
.meter .val{font-variant-numeric:tabular-nums}
.bar{height:6px;background:var(--track);border-radius:3px;overflow:hidden;margin:4px 0 2px}
.bar span{display:block;height:100%;background:var(--accent);border-radius:3px}
.bar span.hi{background:var(--warn)}.bar span.max{background:var(--bad)}
.reset{color:var(--muted);font-size:12px}
.err{font-size:13px;color:var(--bad);word-break:break-word}
.help{font-size:12px;color:var(--muted)}
.actions{display:flex;gap:8px;flex-wrap:wrap;margin-top:auto}
.btn{appearance:none;border:1px solid var(--line);background:transparent;color:var(--accent);
font:inherit;font-weight:500;padding:6px 14px;border-radius:999px;text-decoration:none;cursor:pointer}
.btn.primary{background:var(--accent);border-color:var(--accent);color:#fff}
.btn:hover{filter:brightness(1.08)}
.btn:focus-visible{outline:2px solid var(--accent);outline-offset:2px}
form{margin:0}
"""


def _relative(when: datetime, now: datetime) -> str:
    seconds = int((when - now).total_seconds())
    if seconds <= 0:
        return "now"
    days, rest = divmod(seconds, 86400)
    hours, rest = divmod(rest, 3600)
    minutes = rest // 60
    if days:
        return f"{days} d {hours} h"
    if hours:
        return f"{hours} h {minutes} min"
    return f"{max(minutes, 1)} min"


def _meter(meter: dict[str, Any], now: datetime) -> str:
    value = meter["value"]
    name = escape(meter["name"])
    if meter["unit"] != "%":
        shown = "—" if value is None else f"{value:g} {escape(meter['unit'])}"
        return f'<div class="meter"><div class="row"><span>{name}</span><span class="val">{shown}</span></div></div>'
    percent = 0.0 if value is None else max(0.0, min(100.0, float(value)))
    level = "max" if percent >= 90 else "hi" if percent >= 75 else ""
    reset = ""
    if meter.get("resets_at"):
        when = datetime.fromisoformat(meter["resets_at"])
        reset = f'<div class="reset">Resets in {_relative(when, now)}</div>'
    return (
        f'<div class="meter"><div class="row"><span>{name}</span>'
        f'<span class="val">{percent:.0f}% used</span></div>'
        f'<div class="bar"><span class="{level}" style="width:{percent:.1f}%"></span></div>'
        f"{reset}</div>"
    )


def _card(provider: str, info: dict[str, Any], now: datetime) -> str:
    name = NAMES.get(provider, provider)
    if not info["enabled"]:
        return (
            f'<section class="card off"><div class="head"><h2>{name}</h2>'
            '<span class="chip">Off</span></div>'
            f'<div class="meta">Turn on <b>{name.lower()}</b> in the app\'s Configuration tab to '
            f"install the {CLIS[provider]} and track its limits.</div></section>"
        )
    report = info.get("report")
    if info.get("install_error"):
        status, error = (
            "error",
            f"Installing the {CLIS[provider]} failed: {info['install_error']}",
        )
    elif report is None:
        status, error = "not_installed", None
    else:
        status, error = report["status"], report.get("error")
    parts = [
        f'<div class="head"><h2>{name}</h2>'
        f'<span class="chip {status}">{STATUS_TEXT.get(status, escape(status))}</span></div>'
    ]
    account = (report or {}).get("account") or {}
    meta = [escape(str(v)) for v in (account.get("name"), account.get("plan")) if v]
    meta.append(
        CLIS[provider]
        + (f" {escape(report['version'])}" if report and report.get("version") else "")
    )
    parts.append(f'<div class="meta">{" · ".join(meta)}</div>')
    if error:
        parts.append(f'<div class="err">{escape(error)}</div>')
    parts.extend(_meter(meter, now) for meter in (report or {}).get("meters", []))
    actions = []
    if info["installed"]:
        primary = " primary" if status == "signed_out" else ""
        label = "Sign in" if status == "signed_out" else "Sign in again"
        actions.append(
            f'<a class="btn{primary}" href="term/?arg={provider}">{label}</a>'
        )
        if status == "signed_out":
            parts.append(f'<div class="help">{escape(SIGN_IN_HELP[provider])}</div>')
    parts.append(f'<div class="actions">{"".join(actions)}</div>')
    return f'<section class="card">{"".join(parts)}</section>'


def render_page(
    providers: dict[str, dict[str, Any]],
    interval_minutes: int,
    now: datetime | None = None,
) -> str:
    """Render the status page for the provider snapshot."""
    now = now or datetime.now(UTC)
    cards = "".join(_card(name, info, now) for name, info in providers.items())
    return (
        '<!doctype html><html lang="en"><head><meta charset="utf-8">'
        '<meta name="viewport" content="width=device-width,initial-scale=1">'
        '<meta http-equiv="refresh" content="60">'
        f"<title>AI Usage</title><style>{_STYLE}</style></head><body><main>"
        "<header><h1>AI Usage</h1>"
        f'<span class="sub">Checked every {interval_minutes} min</span>'
        '<form method="post" action="refresh"><button class="btn" type="submit">Refresh now</button></form>'
        '<a class="btn" href="term/?arg=shell">Terminal</a></header>'
        f'<div class="grid">{cards}</div></main></body></html>'
    )
