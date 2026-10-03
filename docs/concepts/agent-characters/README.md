# Agent characters (concept)

Character artwork used in the Mandate frontend. `ReactiveCharacter` adds hover
greetings and eye-only idle expressions to both the simple and classic views.

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

Use `apps/web/src/components/ReactiveCharacter.tsx` to display a character.
Idle characters blink and glance around; hovering shows a smile. Status
expressions remain unchanged. Backgrounds are transparent, with no body motion.

## Re-rendering

`source/agents.html` is a three.js scene (shell-textured fur) rendered headless
by `source/shoot.py` (Playwright + Chromium, Pillow). From `source/`:

```sh
npm i three@0.169.0
PAGE=agents.html python3 shoot.py out kip-refused stella-pass
python3 sheet.py out kip-refused stella-pass   # optional contact sheet
```

## Hover greetings

Each character also has a `smile` state rendered from the same scene, without
sparkles, coins, or approval/check badges. The web UI uses `public/agents/*-smile.png`
on hover only for idle characters. No breathing or pointer-follow motion is used;
working, refusal, failure, and revoked states keep their original expressions.

Idle characters also use `blink`, `look-left`, and `look-right` frames from the
same scene. Only the eyes change. Independent random timers pause on hover,
in hidden tabs, and with reduced motion enabled. Hover smiles take priority.

Idle actions wait 1.4–3.2 seconds between sequences; 62% are quick blinks and
38% are glances in both directions. Sticker wrappers have transparent backgrounds.
