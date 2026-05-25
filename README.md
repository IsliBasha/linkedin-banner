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
