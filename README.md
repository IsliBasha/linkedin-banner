# LinkedIn Banner Auto-Generator

Automatically generates a **1584 × 396 px** LinkedIn cover image from live GitHub data
and uploads it as your profile background photo — refreshed every day by GitHub Actions.

```
┌──────────────────┬──────────────────────────┬─────────────────────────────────────────┐
│                  │                          │                                          │
│  PORTFOLIO       │  COMMITS  ·  12 MONTHS   │  CONTRIBUTIONS  ·  PAST 12 MONTHS       │
│                  │                          │  ░░▓▓████▓░░░▓▓███░░▓▓████▓░░ …         │
│  yoursite.dev    │  1,284                   │  ░░░░▓▓██░░░▓▓███░░░░▓▓████░░ …         │
│  ─────────────   │                          │  ░▓▓▓▓███▓░░░░▓░░░▓▓▓███▓░░░░ …         │
│                  │  TOP LANGUAGES           │                                          │
│                  │  Python    ████████ 68%  │                   Updated: May 25, 2026  │
│                  │  TypeScript ████  21%    │                                          │
│                  │  Go        ██    11%     │                                          │
└──────────────────┴──────────────────────────┴─────────────────────────────────────────┘
```

---

## Features

- **Left** — portfolio URL in clean typographic style
- **Center** — total commits (12-month window) + top-3 language progress bars
- **Right** — full-year GitHub contribution grid (52 × 7 cells, GitHub colour scale)
- **Auto-font**: downloads Inter from the official source if not present locally
- **Auto-upload**: three-step LinkedIn Assets API (register → binary PUT → profile PATCH)
- Saves `banner.png` locally before uploading — useful for debugging
- Daily GitHub Actions cron at `00:00 UTC` + manual trigger

---

## Prerequisites

| What | Version |
|------|---------|
| Python | ≥ 3.11 |
| Pillow | ≥ 10.0 |
| requests | ≥ 2.31 |

---

## Local setup

### 1 — Clone and install

```bash
git clone https://github.com/your-org/linkedin-banner.git
cd linkedin-banner
python -m venv .venv && source .venv/bin/activate
pip install -r requirements.txt
```

### 2 — Set environment variables

```bash
export GITHUB_USERNAME="your-github-handle"
export GH_TOKEN="ghp_xxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxx"
export PORTFOLIO_URL="yoursite.dev"          # optional; shown on the left panel

# Omit these two to generate-only without uploading
export LINKEDIN_ACCESS_TOKEN="AQV..."
export LINKEDIN_PERSON_URN="urn:li:person:XXXXXXXX"
```

### 3 — Run

```bash
python generate_banner.py
```

The generated image is saved as `banner.png` in the current directory.

---

## Getting a GitHub Token (`GH_TOKEN`)

1. Open **GitHub → Settings → Developer settings → Personal access tokens → Tokens (classic)**.
2. Click **Generate new token (classic)**.
3. Tick the **`read:user`** scope — this is the only scope needed.
4. Click **Generate token** and copy the result (starts with `ghp_`).

> **Why not `GITHUB_TOKEN`?**
> GitHub Actions' built-in `secrets.GITHUB_TOKEN` is scoped to the current
> repository; it cannot query a user's cross-repo contribution data via GraphQL.

---

## Getting LinkedIn credentials

### A — Create a LinkedIn Developer App

1. Visit <https://www.linkedin.com/developers/> and sign in.
2. Click **Create app** and fill in: App name, LinkedIn Page (your company page or profile), App Logo, Legal agreement.
3. Click **Create app**.

### B — Enable required products

In your app dashboard → **Products** tab, request:

| Product | Scope granted | Why needed |
|---|---|---|
| **Sign In with LinkedIn using OpenID Connect** | `openid profile email` | Identify yourself |
| **Share on LinkedIn** | `w_member_social` | Upload images |

Both are available to all developers; approval is typically instant.

### C — Get an Access Token

Use the **OAuth 2.0 Authorization Code** flow:

1. In your app → **Auth** → **OAuth 2.0 settings**, add a redirect URI:
   `http://localhost:8080/callback`
2. Note your **Client ID** and **Client Secret**.
3. Build and open this URL in your browser (fill in `YOUR_CLIENT_ID`):

```
https://www.linkedin.com/oauth/v2/authorization
  ?response_type=code
  &client_id=YOUR_CLIENT_ID
  &redirect_uri=http://localhost:8080/callback
  &scope=openid%20profile%20w_member_social
```

4. Authorise the app; copy the `code` value from the redirect URL.
5. Exchange it for a token:

```bash
curl -X POST https://www.linkedin.com/oauth/v2/accessToken \
  -H "Content-Type: application/x-www-form-urlencoded" \
  --data-urlencode "grant_type=authorization_code" \
  --data-urlencode "code=YOUR_CODE" \
  --data-urlencode "redirect_uri=http://localhost:8080/callback" \
  --data-urlencode "client_id=YOUR_CLIENT_ID" \
  --data-urlencode "client_secret=YOUR_CLIENT_SECRET"
```

Copy the `access_token` value from the JSON response.

> **Token lifetime**: LinkedIn access tokens expire after **60 days**.
> Regenerate and update the GitHub secret before then.

### D — Get your Person URN

```bash
curl -H "Authorization: Bearer YOUR_ACCESS_TOKEN" \
     https://api.linkedin.com/v2/userinfo
```

Find the `sub` field — your URN is `urn:li:person:{sub}`.

---

## GitHub Actions setup

### 1 — Push to GitHub

```bash
git remote add origin https://github.com/your-org/linkedin-banner.git
git push -u origin main
```

### 2 — Add repository secrets

**Settings → Secrets and variables → Actions → New repository secret**

| Secret name | Value |
|---|---|
| `LINKEDIN_ACCESS_TOKEN` | LinkedIn access token |
| `LINKEDIN_PERSON_URN` | `urn:li:person:XXXXXXXX` |
| `GH_TOKEN` | GitHub PAT with `read:user` scope |
| `GITHUB_USERNAME` | Your GitHub username |
| `PORTFOLIO_URL` | *(optional)* Your portfolio URL |

### 3 — Run a test

1. **Actions** → **Update LinkedIn Banner** → **Run workflow**.
2. Once complete, download the `linkedin-banner-*` artifact to inspect the image.

The workflow also runs automatically every day at 00:00 UTC.

---

## Daily upload on macOS (launchd)

The upload half runs on your own machine, from a dedicated Chrome profile that
stays logged in to LinkedIn. Cookies replayed into a fresh cloud browser get
soft-rejected regardless of how new they are, so `upload-banner` in the
workflow is a manual fallback and this is the path that actually runs daily.

```bash
brew install --cask google-chrome
git clone https://github.com/IsliBasha/linkedin-banner.git ~/src/linkedin-banner
cd ~/src/linkedin-banner
python3 -m venv .venv && .venv/bin/pip install -r requirements.txt
launchd/install.sh
```

Not `~/Documents` — macOS TCC blocks launchd agents there, and a scheduled run
dies on its first read with `Operation not permitted` (verified 2026-09-01).
`install.sh` refuses to install from `~/Documents`, `~/Desktop` or
`~/Downloads` for that reason.

`install.sh` copies `launchd/com.islibasha.linkedin-banner.plist` into
`~/Library/LaunchAgents`, bootstraps it into your GUI session, and drops a
double-clickable shortcut on the Desktop. It is idempotent — re-run it after
any pull that changes the plist.

Then log in **once**:

```bash
./launch_chrome_for_upload.sh     # opens the banner-Chrome window
```

Log in to LinkedIn in that window and close nothing else. That profile
(`~/.config/linkedin-banner-chrome`) is the persistent session from then on —
LinkedIn's `li_at` cookie lasts about 12 months, and no other Chrome is
involved. The profile is also what keeps remote debugging available at all:
Chrome ≥ 136 refuses `--remote-debugging-port` on the default profile.

Verify:

```bash
.venv/bin/python3 doctor.py       # plist parity, job state, last exit, session
./run_upload_now.sh               # or double-click run_upload_now.command
```

A failed scheduled run reports itself as a macOS notification — the only thing
that will tell you the upload stopped working. `install.sh` posts a test
notification for exactly that reason; if none appears, enable **Script Editor**
under System Settings → Notifications and re-run it.

### Why 21:00

The generation workflow's cron is `0 6 * * *`, but GitHub runs scheduled
workflows late under load — observed finishing at 10:54, 12:20, 10:59, 12:03,
17:57 and 17:11 UTC on 27 Aug – 1 Sep 2026, i.e. 5–12 h behind schedule. The
old 08:45 slot plus a 2 h poll budget expired before the day's banner existed
on most of those days, so the run no-opped. 21:00 local sits after every
observed landing time.

If the Mac is **asleep** at 21:00, launchd replays the missed slot on wake. If
it was **powered off**, that slot is skipped altogether — launchd does not
replay it, unlike `Persistent=true` on the retired systemd timer. The next
21:00 catches up, and re-running is safe: the uploader refuses to re-upload a
banner byte-identical to the last one it uploaded.

A run is capped at 8400 s of wall clock, the same budget systemd enforced with
`TimeoutStartSec=8400`. The cap is the wrapper's, not the uploader's: the
uploader measures its 2 h poll with `time.monotonic()`, which stops while the
Mac sleeps, so a run whose lid closes mid-poll would otherwise live for days
while launchd skips every later 21:00.

### Where things are

| Path | What |
|---|---|
| `launchd/com.islibasha.linkedin-banner.plist` | the agent: 21:00 daily, env, log paths |
| `launchd/run_scheduled.sh` | Chrome → wait → upload; 8400 s wall-clock cap; dated run markers; failure notification |
| `launchd/install.sh` | idempotent installer |
| `~/.linkedin_banner.log` | appended run log (`run start` / `run finish exit=N`) |
| `systemd/` | the retired Linux path — kept for reference, not installed since 2026-09-01 |

---

## Customisation

All visual constants live at the top of `generate_banner.py`:

| Constant | Default | What it controls |
|---|---|---|
| `PORTFOLIO_URL` | `"yourportfolio.dev"` | Overridden by env var |
| `DIV1_X` | `370` | Left/center column boundary (px) |
| `DIV2_X` | `930` | Center/right column boundary (px) |
| `GH_COLORS` | GitHub palette | 5-level heat-map colours |
| `LANG_HUE` | Per-language map | Add / override language colours here |

---

## Notes on the profile background update (Step 3)

LinkedIn's public API allows uploading images with the `w_member_social` scope,
but actually **setting** the background cover photo on your profile depends on
the API tier:

- The script tries `PATCH /rest/profiles/{id}` first.
- Falls back to `PATCH /v2/people/~` on 404.
- If both return **403**, your app may need additional LinkedIn partner access.

In that case the script prints the uploaded image URN and you can set the
background manually in about ten seconds:

> **LinkedIn → Me → View Profile → Edit background photo → upload `banner.png`**

Image generation and upload (Steps 1 & 2) always work with standard developer access.

---

## File structure

```
linkedin-banner/
├── generate_banner.py          # Main script
├── requirements.txt            # Python dependencies
├── banner.png                  # Generated output (add to .gitignore)
├── fonts/                      # Auto-downloaded Inter TTFs (add to .gitignore)
│   ├── Inter-Regular.ttf
│   ├── Inter-SemiBold.ttf
│   └── Inter-Bold.ttf
└── .github/
    └── workflows/
        └── update_banner.yml   # Daily cron + manual trigger
```

### Recommended `.gitignore`

```gitignore
banner.png
fonts/
__pycache__/
.venv/
*.pyc
```
