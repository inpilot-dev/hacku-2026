# Agent characters (concept)

Concept art for the Mandate agents, for Noah to use in the frontend as he sees
fit. Nothing here is wired into `apps/web` yet.

![All states](all-states.jpg)

Style: bold colours, flocked fur, simple shapes, faces drawn straight onto the
fur, made to sit on a dark background (they also work on light). The style
reference was Meta's Muse characters; these designs are original.

| Agent | Character | File | Use it when |
|---|---|---|---|
| Shopping agent | **Kumi**, a fuzzy shopping tote | `kumi-idle.png` | agent is shopping / building the basket |
| | | `kumi-happy.png` | order went through |
| | | `kumi-sad.png` | wallet refused the order |
| Mandate planner (drafts the mandate from plain words) | **Bean**, a speech bubble with glasses | `bean-thinking.png` | interpreting the request |
| | | `bean-idle.png` | draft ready for review |
| | | `bean-done.png` | caregiver approved the mandate |
| Wallet guardian | **Kip**, a kiss-lock coin purse | `kip-idle.png` | mandate active |
| | | `kip-approved.png` | payment within limits |
| | | `kip-refused.png` | policy refusal (over limit, banned item, shop not allowed) |
| | | `kip-revoked.png` | mandate revoked or expired |
| Checker (verifier / audit) | **Stella**, a star | `stella-idle.png` | checking a purchase |
| | | `stella-pass.png` | check passed |
| | | `stella-fail.png` | check failed / counterexample found |

PNGs are 512x512 with transparent backgrounds. 1024px versions are in the
project's shared files under `mascots/`.

Example use: copy the PNGs to `apps/web/public/agents/`, then
`<img src="/agents/kip-refused.png" alt="" width="120">`. A gentle idle bob:

```css
.agent-avatar { animation: bob 2.4s ease-in-out infinite; }
@keyframes bob { 50% { transform: translateY(-4px) scale(1.02, .98); } }
```

## Re-rendering

`source/agents.html` is a three.js scene (shell-textured fur) rendered headless
by `source/shoot.py` (Playwright + Chromium, Pillow). From `source/`:

```sh
npm i three@0.169.0
PAGE=agents.html python3 shoot.py out kip-refused stella-pass
python3 sheet.py out kip-refused stella-pass   # optional contact sheet
```
