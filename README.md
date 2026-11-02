# Brevard Emergency road map

Source for https://brevardemergency.com, a live map of closed and flooded roads in Brevard County, Florida.

GitHub Actions (`.github/workflows/update-map.yml`) refreshes it on a schedule:

1. `news_sweep.py` asks Claude, with web search, for new official road reports (about every 45 minutes).
2. `update_flood_data.py` pulls FL511, FHP and National Weather Service data and merges the hand-entered reports.
3. `build_page.py` builds the page, and Wrangler deploys `site/` to Cloudflare.

Repository secrets: `CLOUDFLARE_API_TOKEN` (Workers deploy) and `ANTHROPIC_API_KEY` (optional; without it the news sweep is skipped).
