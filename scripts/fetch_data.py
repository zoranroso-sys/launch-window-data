#!/usr/bin/env python3
"""
Launch Window Conflict Checker — Data Fetcher
ZR Consulting · zrconsulting.de

Sources:
  - RAWG.io API     → upcoming confirmed releases (primary, free key)
  - IGDB via Twitch → upcoming releases (secondary, fills gaps)
  - SteamSpy        → live CCU enrichment for historical titles (no key)

Writes: data/releases.json

Secrets needed in GitHub Actions:
  RAWG_API_KEY       — from rawg.io/apidocs (free, instant)
  TWITCH_CLIENT_ID   — from dev.twitch.tv (free, for IGDB)
  TWITCH_CLIENT_SECRET

Run manually:  python scripts/fetch_data.py
"""

import os, json, time, datetime, requests

RAWG_API_KEY         = os.environ.get("RAWG_API_KEY", "")
TWITCH_CLIENT_ID     = os.environ.get("TWITCH_CLIENT_ID", "")
TWITCH_CLIENT_SECRET = os.environ.get("TWITCH_CLIENT_SECRET", "")
OUTPUT_PATH          = os.path.join(os.path.dirname(__file__), "..", "data", "releases.json")
UPCOMING_MONTHS      = 6

# ── RAWG (primary) ───────────────────────────────────────────────────────────

def fetch_rawg_upcoming():
    if not RAWG_API_KEY:
        print("  ⚠️  No RAWG_API_KEY — skipping RAWG fetch.")
        return []

    now    = datetime.date.today()
    future = now + datetime.timedelta(days=UPCOMING_MONTHS * 30)
    results, page = [], 1

    while page <= 5:
        try:
            r = requests.get("https://api.rawg.io/api/games", params={
                "key": RAWG_API_KEY, "dates": f"{now},{future}",
                "ordering": "-added", "page_size": 100, "page": page,
                "platforms": "4,18,1,186,187",
            }, timeout=20)
            r.raise_for_status()
            data = r.json()
        except Exception as e:
            print(f"  RAWG error (page {page}): {e}"); break

        games = data.get("results", [])
        if not games: break

        for g in games:
            rd = g.get("released")
            if not rd: continue
            try: dt = datetime.date.fromisoformat(rd)
            except ValueError: continue
            results.append({
                "title":      g.get("name", "Unknown"),
                "date_iso":   rd, "month": dt.month, "year": dt.year,
                "genres":     [x["name"] for x in g.get("genres", [])][:4],
                "platforms":  [p["platform"]["name"] for p in g.get("platforms", [])][:4],
                "hype_score": g.get("added", 0),
                "metacritic": g.get("metacritic") or 0,
                "rating":     round(g.get("rating") or 0, 1),
                "source":     "rawg",
            })

        if not data.get("next"): break
        page += 1
        time.sleep(0.3)

    results = [r for r in results if r["hype_score"] > 5]
    results.sort(key=lambda x: x["hype_score"], reverse=True)
    print(f"  ✓ RAWG: {len(results)} upcoming releases")
    return results


# ── IGDB (secondary) ─────────────────────────────────────────────────────────

def get_twitch_token():
    if not TWITCH_CLIENT_ID or not TWITCH_CLIENT_SECRET:
        print("  ⚠️  No Twitch credentials — skipping IGDB."); return None
    try:
        r = requests.post("https://id.twitch.tv/oauth2/token", params={
            "client_id": TWITCH_CLIENT_ID, "client_secret": TWITCH_CLIENT_SECRET,
            "grant_type": "client_credentials"}, timeout=10)
        r.raise_for_status(); return r.json()["access_token"]
    except Exception as e:
        print(f"  Twitch auth error: {e}"); return None


def fetch_igdb_upcoming(token, existing_titles):
    if not token: return []
    now    = int(time.time())
    future = int((datetime.datetime.now() + datetime.timedelta(days=UPCOMING_MONTHS * 30)).timestamp())
    body   = f"""
        fields name, first_release_date, genres.name, platforms.name, hypes;
        where first_release_date >= {now}
          & first_release_date <= {future}
          & category = 0
          & version_parent = null;
        sort hypes desc;
        limit 100;
    """
    try:
        r = requests.post("https://api.igdb.com/v4/games",
            headers={"Client-ID": TWITCH_CLIENT_ID, "Authorization": f"Bearer {token}", "Content-Type": "text/plain"},
            data=body, timeout=15)
        r.raise_for_status(); raw = r.json()
    except Exception as e:
        print(f"  IGDB error: {e}"); return []

    results = []
    for g in raw:
        if "first_release_date" not in g: continue
        title = g.get("name", "Unknown")
        if title in existing_titles: continue
        dt = datetime.datetime.utcfromtimestamp(g["first_release_date"])
        results.append({
            "title": title, "date_iso": dt.strftime("%Y-%m-%d"),
            "month": dt.month, "year": dt.year,
            "genres": [x["name"] for x in g.get("genres", [])][:4],
            "platforms": [x["name"] for x in g.get("platforms", [])][:4],
            "hype_score": g.get("hypes", 0), "metacritic": 0, "rating": 0, "source": "igdb",
        })
    print(f"  ✓ IGDB: {len(results)} additional releases")
    return results


# ── SteamSpy enrichment ───────────────────────────────────────────────────────

def fetch_steamspy_enrichment():
    tags = ["Action","RPG","Strategy","Indie","Horror","Survival","Multiplayer","Casual","Adventure"]
    enrichment = {}
    for tag in tags:
        print(f"    SteamSpy: {tag}…")
        try:
            r = requests.get("https://steamspy.com/api.php", params={"request": "tag", "tag": tag}, timeout=15)
            r.raise_for_status(); raw = r.json()
        except Exception as e:
            print(f"  SteamSpy error ({tag}): {e}"); time.sleep(1); continue
        for appid, data in list(raw.items())[:30]:
            title = data.get("name", "")
            if not title: continue
            key = title.lower().strip()
            if key in enrichment: continue
            owners_raw = data.get("owners", "0 .. 0")
            try:
                parts  = owners_raw.replace(",", "").split("..")
                owners = (int(parts[0].strip()) + int(parts[1].strip())) // 2
            except Exception:
                owners = 0
            enrichment[key] = {
                "peak_ccu": data.get("ccu", 0), "owners": owners,
                "tags": list((data.get("tags") or {}).keys())[:6],
            }
        time.sleep(0.7)
    print(f"  ✓ SteamSpy: {len(enrichment)} games indexed")
    return enrichment


# ── Curated historical dataset ────────────────────────────────────────────────
# Real games organised by launch month. SteamSpy enriches with live CCU.
# Update annually.

HISTORICAL_BY_MONTH = {
    1:  [{"title":"Palworld","tags":["Survival","Open World","Co-op","Crafting"]},
         {"title":"The Finals","tags":["FPS","Multiplayer","Free to Play"]},
         {"title":"Enshrouded","tags":["Survival","Open World","Co-op","RPG"]},
         {"title":"Tekken 8","tags":["Fighting","Multiplayer","Action"]}],
    2:  [{"title":"Elden Ring","tags":["Soulslike","Action RPG","Open World"]},
         {"title":"Hogwarts Legacy","tags":["Action RPG","Open World","Adventure"]},
         {"title":"Sons of the Forest","tags":["Survival","Horror","Open World","Co-op"]},
         {"title":"Helldivers 2","tags":["Co-op","Shooter","Action","Multiplayer"]}],
    3:  [{"title":"Wo Long: Fallen Dynasty","tags":["Soulslike","Action","Co-op"]},
         {"title":"Resident Evil 4 Remake","tags":["Horror","Action","Survival Horror"]},
         {"title":"Hi-Fi Rush","tags":["Action","Rhythm","Indie"]},
         {"title":"Returnal","tags":["Roguelike","Action","Shooter"]}],
    4:  [{"title":"Dead Island 2","tags":["Action","Co-op","Zombie","FPS"]},
         {"title":"Star Wars Jedi: Survivor","tags":["Action","Adventure","Soulslike"]},
         {"title":"Minecraft Legends","tags":["Strategy","Action","Co-op"]}],
    5:  [{"title":"System Shock Remake","tags":["FPS","Horror","Sci-Fi","Immersive Sim"]},
         {"title":"Redfall","tags":["Co-op","FPS","Open World"]},
         {"title":"Dead by Daylight","tags":["Horror","Survival","Multiplayer","Asymmetric"]}],
    6:  [{"title":"Diablo IV","tags":["Action RPG","Co-op","Dark Fantasy","Loot"]},
         {"title":"Street Fighter 6","tags":["Fighting","Multiplayer","Esports"]},
         {"title":"Final Fantasy XVI","tags":["Action RPG","Story Rich","Fantasy"]}],
    7:  [{"title":"Remnant II","tags":["Soulslike","Shooter","Co-op","Action"]},
         {"title":"Exoprimal","tags":["Action","Co-op","Multiplayer"]},
         {"title":"Pikmin 4","tags":["Strategy","Puzzle","Adventure"]}],
    8:  [{"title":"Baldur's Gate 3","tags":["RPG","Turn-Based","Co-op","Fantasy","Story Rich"]},
         {"title":"Armored Core VI","tags":["Action","Mecha","Soulslike"]},
         {"title":"Sea of Stars","tags":["RPG","Turn-Based","Indie","Pixel Art"]}],
    9:  [{"title":"Starfield","tags":["Open World","RPG","Sci-Fi","Space"]},
         {"title":"Lies of P","tags":["Soulslike","Action RPG","Steampunk"]},
         {"title":"Mortal Kombat 1","tags":["Fighting","Multiplayer","Action"]},
         {"title":"Payday 3","tags":["Co-op","FPS","Heist","Multiplayer"]}],
    10: [{"title":"Alan Wake 2","tags":["Horror","Action","Thriller","Story Rich"]},
         {"title":"Cities: Skylines II","tags":["City Builder","Strategy","Simulation"]},
         {"title":"Assassin's Creed Mirage","tags":["Action","Adventure","Stealth","Open World"]},
         {"title":"Super Mario Bros. Wonder","tags":["Platformer","Co-op","Action"]}],
    11: [{"title":"Call of Duty: Modern Warfare III","tags":["FPS","Multiplayer","Shooter"]},
         {"title":"Lethal Company","tags":["Co-op","Horror","Indie","Survival"]},
         {"title":"Like a Dragon: Ishin!","tags":["RPG","Action","JRPG","Brawler"]}],
    12: [{"title":"Avatar: Frontiers of Pandora","tags":["Open World","Action","Adventure"]},
         {"title":"The Game Awards Reveals","tags":["Industry Event"],
          "note":"TGA dominates December media cycle — launch before Dec 15"},
         {"title":"It Takes Two","tags":["Co-op","Platformer","Adventure","Puzzle"]}],
}


def enrich_historical(enrichment):
    output = {}
    for month, games in HISTORICAL_BY_MONTH.items():
        enriched = []
        for g in games:
            entry = {"title": g["title"], "tags": g["tags"], "peak_ccu": 0, "owners": 0}
            if g.get("note"): entry["note"] = g["note"]
            key   = g["title"].lower().strip()
            match = enrichment.get(key)
            if not match:
                for sp_key, sp_data in enrichment.items():
                    if key in sp_key or sp_key in key:
                        match = sp_data; break
            if match:
                entry["peak_ccu"] = match.get("peak_ccu", 0)
                entry["owners"]   = match.get("owners", 0)
                sp_tags = match.get("tags", [])
                if sp_tags:
                    entry["tags"] = list(dict.fromkeys(g["tags"] + sp_tags))[:8]
            enriched.append(entry)
        output[str(month)] = enriched
    return output


# ── Industry events ───────────────────────────────────────────────────────────

def get_industry_events():
    return [
        {"month": 3,  "label": "GDC",              "severity": "high",   "notes": "Game Developers Conference — press unavailable, media cycle disrupted"},
        {"month": 5,  "label": "Steam Next Fest",  "severity": "high",   "notes": "Demo festival — launch signal diluted, wishlists spike instead"},
        {"month": 6,  "label": "Summer Game Fest", "severity": "high",   "notes": "E3-replacement showcase — announcement coverage dominates all media"},
        {"month": 8,  "label": "Gamescom",         "severity": "high",   "notes": "Europe's biggest show — avoid launch week, post-show slot is valuable"},
        {"month": 10, "label": "Steam Next Fest",  "severity": "medium", "notes": "Autumn Next Fest — demo event, second occurrence of the year"},
        {"month": 11, "label": "The Game Awards",  "severity": "high",   "notes": "TGA dominates media for 3 weeks — black hole for new release coverage"},
        {"month": 11, "label": "Black Friday",     "severity": "medium", "notes": "Consumer attention on sales, not new releases — gifting spike possible"},
        {"month": 7,  "label": "Steam Summer Sale","severity": "medium", "notes": "Price anchor effect — players wait for discounts on full-price new releases"},
        {"month": 12, "label": "Steam Winter Sale","severity": "medium", "notes": "Year-end sale — launch by Dec 15 or hold until January"},
    ]


# ── Month index ───────────────────────────────────────────────────────────────

def build_month_index(upcoming, historical_by_month):
    index = {}
    for m in range(1, 13):
        month_upcoming = sorted(
            [r for r in upcoming if r["month"] == m],
            key=lambda x: x.get("hype_score", 0), reverse=True
        )
        index[str(m)] = {
            "upcoming_releases": [
                {"title": r["title"], "date": r["date_iso"], "genres": r["genres"][:3],
                 "hype": r["hype_score"], "metacritic": r.get("metacritic", 0),
                 "rating": r.get("rating", 0), "source": r.get("source", "")}
                for r in month_upcoming[:12]
            ],
            "top_performers": historical_by_month.get(str(m), []),
        }
    return index


# ── Main ──────────────────────────────────────────────────────────────────────

def main():
    print("🚀 Launch Window Data Fetcher — ZR Consulting")
    print(f"   {datetime.datetime.utcnow().strftime('%Y-%m-%d %H:%M UTC')}\n")

    print("📡 RAWG upcoming releases…")
    rawg = fetch_rawg_upcoming()
    existing_titles = {r["title"] for r in rawg}

    print("\n📡 IGDB upcoming releases (secondary)…")
    igdb = fetch_igdb_upcoming(get_twitch_token(), existing_titles)

    all_upcoming = rawg + igdb
    all_upcoming.sort(key=lambda x: (x["month"], -x.get("hype_score", 0)))

    print("\n📊 SteamSpy enrichment…")
    enrichment = fetch_steamspy_enrichment()

    print("\n🗂  Enriching historical data…")
    historical = enrich_historical(enrichment)

    print("🗂  Building month index…")
    month_index = build_month_index(all_upcoming, historical)

    output = {
        "meta": {
            "generated_at":      datetime.datetime.utcnow().isoformat() + "Z",
            "upcoming_count":    len(all_upcoming),
            "next_update":       (datetime.datetime.utcnow() + datetime.timedelta(days=7)).strftime("%Y-%m-%d"),
            "sources":           ["rawg", "igdb", "steamspy"],
        },
        "industry_events": get_industry_events(),
        "month_index":     month_index,
    }

    os.makedirs(os.path.dirname(OUTPUT_PATH), exist_ok=True)
    with open(OUTPUT_PATH, "w", encoding="utf-8") as f:
        json.dump(output, f, indent=2, ensure_ascii=False)

    kb = os.path.getsize(OUTPUT_PATH) / 1024
    print(f"\n✅ Done → {OUTPUT_PATH} ({kb:.1f} KB)")
    print(f"   Upcoming: {len(all_upcoming)} releases")
    for m in range(1, 13):
        u = len(month_index[str(m)]["upcoming_releases"])
        h = len(month_index[str(m)]["top_performers"])
        if u or h: print(f"   Month {m:2d}: {u} upcoming · {h} historical comps")


if __name__ == "__main__":
    main()
