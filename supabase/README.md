# live-quote Edge Function

CORS proxy for TWSE MIS, used by the dashboard's live overlay (own holdings / watchlist + indices).
Deployed to Supabase project `vibe-lab` (ref `ragektutcdvfgbtllxpk`) with `verify_jwt = false`
(public, read-only quotes; the function only forwards Taiwan-ticker channels).

Free projects pause after ~1 week without DB activity. `site.yml` reads the one-row
`public.keepalive` table once per trading day; it needs repo secrets `SUPABASE_URL`
(`https://ragektutcdvfgbtllxpk.supabase.co`) and `SUPABASE_KEY` (the publishable key).
If the project is paused anyway, the page silently falls back to the 5-minute snapshot.

`worker/` holds an equivalent Cloudflare Worker, should we ever move.
