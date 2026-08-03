# crosstalk.dev — marketing site

Static single-page landing site for Crosstalk. No build step — plain HTML/CSS/JS in `index.html`.

## Deploy (Cloudflare Pages)

Connected to this GitHub repo:

- **Production branch:** `main`
- **Framework preset:** None
- **Build command:** *(empty)*
- **Build output directory:** `site`

Every push to `main` redeploys. Custom domain `crosstalk.dev` is attached in the Pages project's
**Custom domains** tab (DNS is automatic when the domain is registered in the same Cloudflare account).

## Analytics (PostHog)

`index.html` includes the PostHog loader with a placeholder key. To turn analytics on, replace
`__POSTHOG_PROJECT_KEY__` with your **Project API key** (from PostHog → Settings → Project) and set
`HOST` to `https://eu.i.posthog.com` if your project is in the EU. The key is a public, write-only
key — safe to commit. Until it's set, the block no-ops (nothing is tracked).

Events captured: autocaptured pageviews/clicks, plus a `waitlist_signup` event (with the email) on
form submit — so PostHog doubles as the waitlist store until a dedicated backend is added.

## Local preview

```bash
cd site && python3 -m http.server 8080   # then open http://localhost:8080
```
