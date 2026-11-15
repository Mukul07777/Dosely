# Deploy Dosely on Vercel + Render (free, no card)

| Part | Host | Why |
|---|---|---|
| Website (Next.js, `web/`) | **Vercel** | Free, built for Next.js |
| API (FastAPI, `api/`) | **Render** | Vercel cannot run a long-lived API with a database and a reminder loop |

The website forwards `/api/*` to the API, so the browser only ever talks to the Vercel address and the session
cookie stays first-party. For one always-on service on Google Cloud instead, see [CLOUD_RUN.md](CLOUD_RUN.md).

## 1. API on Render (about 5 minutes)

1. Sign in at <https://dashboard.render.com> with GitHub.
2. **New > Blueprint**, pick the `Dosely` repository. Render reads [`render.yaml`](../../render.yaml).
3. It asks for a few values (`SMTP_HOST`, `GEMINI_API_KEY`, ...). **Leave them all empty** for now and click
   **Apply**. Without them emails are kept in an outbox and the weekly note uses built-in text.
4. Wait for the build to finish, then copy the service address, for example `https://dosely-api.onrender.com`.
5. Check it: open `https://dosely-api.onrender.com/health`. It should show `{"ok": true, "alerts": 0}`.

## 2. Website on Vercel (about 3 minutes)

1. Sign in at <https://vercel.com/new> with GitHub and import the `Dosely` repository.
2. Set **Root Directory** to `web`.
3. Under **Environment Variables** add:

   | Name | Value |
   |---|---|
   | `KINCARE_API_URL` | your Render address, e.g. `https://dosely-api.onrender.com` (no trailing slash) |

4. Click **Deploy**. You get an address like `https://dosely.vercel.app`.

## 3. Connect the two

In Render > dosely-api > **Environment**, set `KINCARE_ORIGINS` to your Vercel address
(`https://dosely.vercel.app`) and save. Render restarts the API.

## 4. Check it

- Open the Vercel address: the landing page loads.
- Open `/circle`, create a profile, add a medicine and tick a dose.
- `https://YOUR-SITE.vercel.app/api/circle/email/status` shows the email provider and whether Gemini is set.

## Optional: real emails and the Gemini note

In Render > dosely-api > **Environment** add:

| Name | Value |
|---|---|
| `SMTP_HOST` | `smtp.gmail.com` |
| `SMTP_USER` | your Gmail address |
| `SMTP_PASSWORD` | a Gmail [app password](https://myaccount.google.com/apppasswords) |
| `EMAIL_FROM` | `Dosely <you@gmail.com>` |
| `GEMINI_API_KEY` | from <https://aistudio.google.com/apikey> |

## Limits of the free plan

- The API **sleeps after 15 minutes without traffic**; the first request after that takes 30 to 60 seconds, and
  reminder emails pause while it sleeps.
- The database is **reset when the API restarts**. Fine for a demo, not for real use.

Cloud Run with `--min-instances 1` has neither limit.
