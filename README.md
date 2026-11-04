# Brevard Emergency road map

Source for https://brevardemergency.com, a live map of closed and flooded roads in Brevard County, Florida.

GitHub Actions (`.github/workflows/update-map.yml`) refreshes it on a schedule:

1. `news_sweep.py` asks Claude, with web search, for new official road reports (about every 45 minutes).
2. `update_flood_data.py` pulls FL511, FHP and National Weather Service data and merges the hand-entered reports.
3. `build_page.py` builds the page, and Wrangler deploys `site/` to Cloudflare.

Repository secrets: `CLOUDFLARE_API_TOKEN` (Workers deploy) and `ANTHROPIC_API_KEY` (optional; without it the news sweep is skipped).

Nov 4 feed/cleanup policy: FHP and FL511 reports absent on two healthy, complete reads leave their feed's active contribution; independent page/NWS warnings remain. FL511 validates both full lists, paged totals, usable unique ids and malformed rows before absence can count. Mike's separate one-time cleanup removes only old non-feed flood/closure reports predating 2026-11-04T18:38:15Z with no current or uncertain nearby feed counterpart, on one fresh healthy batch. The administrative receipt in audit/owner_cleanup.json is permanent, including zero-removal batches; old seeds do not revive reports, later real reports can. Administrative removals never show an open/green pin. The original domain, branding and source URLs are preserved. Tests/test_owner_cleanup.py runs in Update map before release.
