# Live quote proxy (Cloudflare Worker)

1. `npm i -g wrangler && wrangler login`
2. `cd worker && wrangler deploy`  → prints `https://stock-live-proxy.<you>.workers.dev`
3. Put that URL in `docs/live-config.js` (`window.LIVE_URL`), commit, publish.
4. Optional: set `ALLOW_ORIGIN` in `wrangler.toml` to your Pages origin and redeploy.

Leave `LIVE_URL` empty and the dashboard behaves exactly as before (5-minute snapshots).
