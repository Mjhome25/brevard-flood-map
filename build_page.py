#!/usr/bin/env python3
"""Assemble brevard-flood-map.html from template.html + flood_data.json + tiles.json + leaflet.css.
Run after update_flood_data.py:  python3 build_page.py
"""
import json, os
HERE = os.path.dirname(os.path.abspath(__file__))
rd = lambda n: open(os.path.join(HERE, n), encoding="utf-8").read()
safe = lambda s: s.replace("</", "<\\/")  # keep JSON from closing the script tag
page = rd("template.html")
page = page.replace("__LEAFLET_CSS__", rd("leaflet.css").replace("</", "<\\/"))
page = page.replace("__DATA_JSON__", safe(json.dumps(json.load(open(os.path.join(HERE, "flood_data.json"))), separators=(",", ":"))))
page = page.replace("__TILES_JSON__", safe(json.dumps(json.load(open(os.path.join(HERE, "tiles.json"))), separators=(",", ":"))))
out = os.path.join(HERE, "brevard-flood-map.html")
open(out, "w", encoding="utf-8").write(page)  # artifact body (the Artifact tool adds the document skeleton)
# Standalone copy for brevardemergency.com needs its own skeleton with the charset declared.
site = ('<!doctype html><html lang="en"><head><meta charset="utf-8">'
        '<meta name="viewport" content="width=device-width,initial-scale=1,viewport-fit=cover">'
        '<meta name="description" content="Live map of closed and flooded roads in Brevard County, Florida, from FL511, Florida Highway Patrol, the National Weather Service and local agencies.">'
        '<meta name="theme-color" content="#1d4e89">'
        '<link rel="icon" href="/favicon.ico" sizes="any"><link rel="icon" href="/favicon.svg" type="image/svg+xml">'
        '<link rel="apple-touch-icon" href="/apple-touch-icon.png"><link rel="manifest" href="/site.webmanifest">'
        '<meta property="og:type" content="website"><meta property="og:site_name" content="Brevard Emergency">'
        '<meta property="og:title" content="Brevard Emergency: live road closures &amp; flooding">'
        '<meta property="og:description" content="Closed and flooded roads across Brevard County, updated through the day from FL511, FHP, NWS and local agencies.">'
        '<meta property="og:url" content="https://brevardemergency.com/">'
        '<meta property="og:image" content="https://brevardemergency.com/og.png"><meta property="og:image:width" content="1200"><meta property="og:image:height" content="630">'
        '<meta property="og:image:alt" content="Brevard Emergency logo over a map of Brevard County">'
        '<meta name="twitter:card" content="summary_large_image"><meta name="twitter:image" content="https://brevardemergency.com/og.png">'
        '<style>:root{padding-top:env(safe-area-inset-top,0px);padding-bottom:env(safe-area-inset-bottom,0px)}img{max-width:100%}[hidden]{display:none!important}</style>'
        '</head><body>' + page + '</body></html>')
os.makedirs(os.path.join(HERE, "site"), exist_ok=True)
open(os.path.join(HERE, "site", "index.html"), "w", encoding="utf-8").write(site)
# Small file the live page polls every 5 minutes to learn when newer road data exists.
json.dump(json.load(open(os.path.join(HERE, "flood_data.json"))), open(os.path.join(HERE, "site", "data.json"), "w"), separators=(",", ":"))
print("wrote", out, len(page.encode()) // 1024, "KB; site/index.html", len(site.encode()) // 1024, "KB")
