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
# Comprehensive global games industry calendar.
# Categories: trade_show, showcase, sale, award, festival
# "approx_date" is the approximate 2026 date — update annually when confirmed.
# Used for ICS calendar export and chronological sorting.

def get_industry_events():
    return [
        # ══════════════ JANUARY ══════════════
        {"month": 1,  "label": "CES",                      "severity": "low",    "category": "trade_show",  "approx_date": "2026-01-06", "end_date": "2026-01-09",
         "notes": "Consumer Electronics Show, Las Vegas — gaming-adjacent, can steal tech headlines"},
        {"month": 1,  "label": "Xbox Developer Direct",    "severity": "medium", "category": "showcase",    "approx_date": "2026-01-23",
         "notes": "Microsoft's focused deep-dive showcase — pulls press for 24-48 hours"},
        {"month": 1,  "label": "Taipei Game Show",         "severity": "low",    "category": "trade_show",  "approx_date": "2026-01-29", "end_date": "2026-02-01",
         "notes": "Taiwan's largest game expo — relevant for APAC-facing titles"},
        # ══════════════ FEBRUARY ══════════════
        {"month": 2,  "label": "D.I.C.E. Summit",          "severity": "low",    "category": "trade_show",  "approx_date": "2026-02-10", "end_date": "2026-02-12",
         "notes": "AIAS industry summit, Las Vegas — senior networking, minimal consumer impact"},
        {"month": 2,  "label": "DICE Awards",              "severity": "low",    "category": "award",       "approx_date": "2026-02-12",
         "notes": "Academy of Interactive Arts and Sciences awards — industry recognition"},
        {"month": 2,  "label": "Nintendo Direct",          "severity": "medium", "category": "showcase",    "approx_date": "2026-02-18",
         "notes": "Nintendo typically runs a Feb Direct — pulls press for 48-72 hours"},
        {"month": 2,  "label": "Steam Next Fest",          "severity": "high",   "category": "festival",    "approx_date": "2026-02-23", "end_date": "2026-03-02",
         "notes": "Week-long demo festival — hundreds of demos flood discovery, launch signal diluted"},
        # ══════════════ MARCH ══════════════
        {"month": 3,  "label": "GDC",                      "severity": "high",   "category": "trade_show",  "approx_date": "2026-03-09", "end_date": "2026-03-13",
         "notes": "Game Developers Conference, San Francisco — press concentrated for a full week"},
        {"month": 3,  "label": "IGF Awards",               "severity": "low",    "category": "award",       "approx_date": "2026-03-11",
         "notes": "Independent Games Festival awards at GDC — indie credibility moment"},
        {"month": 3,  "label": "GDC Awards",               "severity": "low",    "category": "award",       "approx_date": "2026-03-11",
         "notes": "Game Developers Choice Awards at GDC — industry peer recognition"},
        {"month": 3,  "label": "SXSW Gaming",              "severity": "low",    "category": "festival",    "approx_date": "2026-03-12", "end_date": "2026-03-18",
         "notes": "South by Southwest gaming track, Austin — cultural crossover, indie visibility"},
        {"month": 3,  "label": "PAX East",                 "severity": "medium", "category": "trade_show",  "approx_date": "2026-03-26", "end_date": "2026-03-29",
         "notes": "Major consumer expo in Boston — demos, influencer coverage, press splits attention"},
        # ══════════════ APRIL ══════════════
        {"month": 4,  "label": "BAFTA Games Awards",       "severity": "low",    "category": "award",       "approx_date": "2026-04-17",
         "notes": "British Academy Games Awards — strong UK/EU press coverage for 24-48 hours"},
        {"month": 4,  "label": "DCP — Deutscher Computerspielpreis", "severity": "medium", "category": "award", "approx_date": "2026-04-29",
         "notes": "German Computer Game Award, Munich — major DACH event, strong regional press and networking"},
        {"month": 4,  "label": "iicon (ESA)",                "severity": "high",   "category": "trade_show",  "approx_date": "2026-04-27", "end_date": "2026-04-30",
         "notes": "Interactive Innovation Conference (E3 successor), Fontainebleau Las Vegas — invite-only, ESA-hosted, all major publishers present"},
        {"month": 4,  "label": "gamescom LatAm",           "severity": "low",    "category": "trade_show",  "approx_date": "2026-04-29", "end_date": "2026-05-03",
         "notes": "Gamescom's Latin American edition — growing LATAM market visibility"},
        # ══════════════ MAY ══════════════
        {"month": 5,  "label": "EGX at MCM London",        "severity": "low",    "category": "trade_show",  "approx_date": "2026-05-22", "end_date": "2026-05-24",
         "notes": "UK's biggest gaming expo, now co-located with MCM Comic Con at ExCeL London"},
        {"month": 5,  "label": "Unreal Fest",              "severity": "low",    "category": "trade_show",  "approx_date": "2026-05-06", "end_date": "2026-05-08",
         "notes": "Epic Games' Unreal Engine developer conference"},
        {"month": 5,  "label": "PlayStation State of Play", "severity": "medium","category": "showcase",    "approx_date": "2026-05-14",
         "notes": "Sony showcase — pulls press attention and dominates news cycle for 2-3 days"},
        {"month": 5,  "label": "Digital Dragons",          "severity": "low",    "category": "trade_show",  "approx_date": "2026-05-17", "end_date": "2026-05-19",
         "notes": "Central European game dev conference, Krakow — strong Polish/CEE industry presence"},
        {"month": 5,  "label": "BitSummit",                "severity": "low",    "category": "trade_show",  "approx_date": "2026-05-22", "end_date": "2026-05-24",
         "notes": "Japanese indie game expo, Kyoto — relevant for Japan-facing indie titles"},
        {"month": 5,  "label": "Nordic Game Conference",   "severity": "low",    "category": "trade_show",  "approx_date": "2026-05-25", "end_date": "2026-05-29",
         "notes": "Malmo, Sweden — key Nordic/European indie and AA industry event"},
        {"month": 5,  "label": "Wholesome Direct",         "severity": "low",    "category": "showcase",    "approx_date": "2026-05-28",
         "notes": "Cozy/wholesome games showcase — niche but passionate audience"},
        {"month": 5,  "label": "TwitchCon Europe",         "severity": "low",    "category": "festival",    "approx_date": "2026-05-30", "end_date": "2026-05-31",
         "notes": "Twitch's European convention — streaming and content creator community"},
        # ══════════════ JUNE ══════════════
        {"month": 6,  "label": "Summer Game Fest",         "severity": "high",   "category": "showcase",    "approx_date": "2026-06-05",
         "notes": "Geoff Keighley's showcase, Los Angeles — announcement coverage dominates all gaming media"},
        {"month": 6,  "label": "Xbox Games Showcase",      "severity": "high",   "category": "showcase",    "approx_date": "2026-06-08",
         "notes": "Microsoft's annual showcase — major reveals, press fully focused on announcements"},
        {"month": 6,  "label": "PC Gaming Show",           "severity": "medium", "category": "showcase",    "approx_date": "2026-06-08",
         "notes": "PC-focused showcase — PC press diverted to coverage for 2-3 days"},
        {"month": 6,  "label": "Future Games Show",        "severity": "medium", "category": "showcase",    "approx_date": "2026-06-08",
         "notes": "Future Publishing's showcase — broad coverage, indie and AA visibility affected"},
        {"month": 6,  "label": "Devolver Digital Showcase", "severity": "low",   "category": "showcase",    "approx_date": "2026-06-08",
         "notes": "Devolver's showcase — indie-focused, high social media engagement"},
        {"month": 6,  "label": "Ubisoft Forward",          "severity": "medium", "category": "showcase",    "approx_date": "2026-06-09",
         "notes": "Ubisoft's showcase — AAA reveals pull media attention"},
        {"month": 6,  "label": "Capcom Showcase",          "severity": "medium", "category": "showcase",    "approx_date": "2026-06-09",
         "notes": "Capcom's dedicated showcase — Monster Hunter, Resident Evil, Street Fighter news"},
        {"month": 6,  "label": "Day of the Devs",          "severity": "low",    "category": "showcase",    "approx_date": "2026-06-09",
         "notes": "iam8bit's indie showcase — curated indie selection, press-friendly"},
        {"month": 6,  "label": "Latin American Games Showcase","severity":"low",  "category": "showcase",    "approx_date": "2026-06-09",
         "notes": "LATAM-focused showcase — growing market, Spanish/Portuguese localisation relevant"},
        {"month": 6,  "label": "Nintendo Direct",          "severity": "high",   "category": "showcase",    "approx_date": "2026-06-10",
         "notes": "E3-season Nintendo Direct — typically their biggest of the year"},
        {"month": 6,  "label": "Square Enix Presents",     "severity": "medium", "category": "showcase",    "approx_date": "2026-06-10",
         "notes": "Square Enix showcase — Final Fantasy, Dragon Quest audience pull"},
        {"month": 6,  "label": "Bandai Namco Next",        "severity": "low",    "category": "showcase",    "approx_date": "2026-06-10",
         "notes": "Bandai Namco showcase — anime games, Tekken, Elden Ring news"},
        {"month": 6,  "label": "Annapurna Interactive Showcase","severity":"low", "category": "showcase",    "approx_date": "2026-06-11",
         "notes": "Annapurna's showcase — prestigious indie publisher, strong critical press"},
        {"month": 6,  "label": "Tribeca Games Spotlight",  "severity": "low",    "category": "festival",    "approx_date": "2026-06-12", "end_date": "2026-06-14",
         "notes": "Tribeca Festival's gaming track — narrative/art games get cultural press crossover"},
        # ══════════════ JULY ══════════════
        {"month": 7,  "label": "Steam Summer Sale",        "severity": "medium", "category": "sale",        "approx_date": "2026-06-25", "end_date": "2026-07-09",
         "notes": "Price anchor effect — players wait for discounts, full-price launches face resistance"},
        {"month": 7,  "label": "Develop:Brighton",         "severity": "low",    "category": "trade_show",  "approx_date": "2026-07-08", "end_date": "2026-07-10",
         "notes": "UK developer conference, Brighton — key UK industry networking and talks"},
        {"month": 7,  "label": "EA Spotlight",             "severity": "medium", "category": "showcase",    "approx_date": "2026-07-15",
         "notes": "EA's showcase event — sports titles, Battlefield, major EA IPs pull attention"},
        {"month": 7,  "label": "ChinaJoy",                 "severity": "low",    "category": "trade_show",  "approx_date": "2026-07-24", "end_date": "2026-07-27",
         "notes": "China's largest gaming expo, Shanghai — relevant if targeting Chinese market"},
        # ══════════════ AUGUST ══════════════
        {"month": 8,  "label": "gamescom dev",              "severity": "high",   "category": "trade_show",  "approx_date": "2026-08-23", "end_date": "2026-08-25",
         "notes": "Europe's largest developer conference (formerly devcom), Cologne — 2 days of talks, B2B matchmaking, indie expo, networking"},
        {"month": 8,  "label": "Opening Night Live",       "severity": "high",   "category": "showcase",    "approx_date": "2026-08-25",
         "notes": "Keighley's Gamescom kick-off — major reveals, dominates the full week's coverage"},
        {"month": 8,  "label": "Gamescom",                 "severity": "high",   "category": "trade_show",  "approx_date": "2026-08-26", "end_date": "2026-08-30",
         "notes": "Europe's biggest games show, Cologne — avoid launch week, post-show slot is valuable"},
        {"month": 8,  "label": "Future Games Show at Gamescom","severity":"medium","category":"showcase",   "approx_date": "2026-08-27",
         "notes": "Future's Gamescom showcase — additional coverage saturation during show week"},
        {"month": 8,  "label": "THQ Nordic Showcase",      "severity": "low",    "category": "showcase",    "approx_date": "2026-08-27",
         "notes": "THQ Nordic's Gamescom showcase — AA titles, genre-specific audiences"},
        {"month": 8,  "label": "Nacon Connect",            "severity": "low",    "category": "showcase",    "approx_date": "2026-08-26",
         "notes": "Nacon's showcase — AA titles, racing, horror, and licensed games"},
        {"month": 8,  "label": "Focus Entertainment What's Next","severity":"low","category":"showcase",    "approx_date": "2026-08-27",
         "notes": "Focus' Gamescom showcase — relevant for Soulslike and AA action fans"},
        {"month": 8,  "label": "Awesome Indies Showcase",  "severity": "low",    "category": "showcase",    "approx_date": "2026-08-27",
         "notes": "Indie showcase at Gamescom — curated indie selection"},
        # ══════════════ SEPTEMBER ══════════════
        {"month": 9,  "label": "D.I.C.E. Athens",             "severity": "low",    "category": "trade_show",  "approx_date": "2026-09-21", "end_date": "2026-09-23",
         "notes": "AIAS European networking event, Athens — intimate industry gathering for senior professionals"},
        {"month": 9,  "label": "PAX West",                 "severity": "medium", "category": "trade_show",  "approx_date": "2026-09-04", "end_date": "2026-09-07",
         "notes": "Fan-focused expo in Seattle — demos, community events, indie presence"},
        {"month": 9,  "label": "PlayStation State of Play", "severity": "medium","category": "showcase",    "approx_date": "2026-09-10",
         "notes": "Sony's September showcase — typically focuses on holiday lineup"},
        {"month": 9,  "label": "Tokyo Game Show",          "severity": "medium", "category": "trade_show",  "approx_date": "2026-09-17", "end_date": "2026-09-21",
         "notes": "Japan's largest game show, Makuhari Messe — 5 days for the first time, 300K expected"},
        {"month": 9,  "label": "Sega Showcase",            "severity": "low",    "category": "showcase",    "approx_date": "2026-09-20",
         "notes": "Sega's showcase — Yakuza, Sonic, Persona franchises"},
        # ══════════════ OCTOBER ══════════════
        {"month": 10, "label": "Steam Next Fest",          "severity": "medium", "category": "festival",    "approx_date": "2026-10-05", "end_date": "2026-10-12",
         "notes": "Autumn demo festival — second occurrence, demo noise competes with launches"},
        {"month": 10, "label": "Brasil Game Show",         "severity": "low",    "category": "trade_show",  "approx_date": "2026-10-08", "end_date": "2026-10-12",
         "notes": "Latin America's largest game show, Sao Paulo — key for LATAM visibility"},
        {"month": 10, "label": "Twitch Galaxies",          "severity": "low",    "category": "showcase",    "approx_date": "2026-10-15",
         "notes": "Twitch's showcase event — streaming-focused reveals"},
        {"month": 10, "label": "Milan Games Week",         "severity": "low",    "category": "trade_show",  "approx_date": "2026-10-23", "end_date": "2026-10-25",
         "notes": "Italy's largest gaming event — Southern European market visibility"},
        {"month": 10, "label": "Steam Autumn Sale",        "severity": "medium", "category": "sale",        "approx_date": "2026-10-27", "end_date": "2026-11-03",
         "notes": "Late October sale — price expectations drop as sale approaches"},
        {"month": 10, "label": "Paris Games Week",         "severity": "low",    "category": "trade_show",  "approx_date": "2026-10-29", "end_date": "2026-11-01",
         "notes": "France's consumer gaming expo — relevant for French market"},
        # ══════════════ NOVEMBER ══════════════
        {"month": 11, "label": "Day of the Devs: Fall Edition","severity":"low",  "category": "showcase",    "approx_date": "2026-11-05",
         "notes": "iam8bit's autumn indie showcase — curated, press-friendly"},
        {"month": 11, "label": "G-Star Korea",             "severity": "low",    "category": "trade_show",  "approx_date": "2026-11-12", "end_date": "2026-11-15",
         "notes": "South Korea's major gaming expo, Busan — critical for Korean MMO/F2P market"},
        {"month": 11, "label": "Women in Games Awards",    "severity": "low",    "category": "award",       "approx_date": "2026-11-18",
         "notes": "Industry diversity awards — networking, visibility for inclusive studios"},
        {"month": 11, "label": "Golden Joystick Awards",   "severity": "low",    "category": "award",       "approx_date": "2026-11-20",
         "notes": "GamesRadar/Future's public-voted awards — consumer engagement"},
        {"month": 11, "label": "Dreamhack",                "severity": "low",    "category": "festival",    "approx_date": "2026-11-20", "end_date": "2026-11-22",
         "notes": "LAN/esports festival — strong community engagement"},
        {"month": 11, "label": "Black Friday",             "severity": "medium", "category": "sale",        "approx_date": "2026-11-27", "end_date": "2026-11-30",
         "notes": "Consumer attention on deals, not new releases — gifting spike possible"},
        # ══════════════ DECEMBER ══════════════
        {"month": 12, "label": "Indie World / Nintendo",   "severity": "low",    "category": "showcase",    "approx_date": "2026-12-08",
         "notes": "Nintendo sometimes runs a Dec showcase — minor distraction"},
        {"month": 12, "label": "The Game Awards",          "severity": "high",   "category": "award",       "approx_date": "2026-12-10",
         "notes": "TGA show night — world premieres, massive live audience, dominates entire month"},
        {"month": 12, "label": "GOG Winter Sale",          "severity": "low",    "category": "sale",        "approx_date": "2026-12-15", "end_date": "2026-12-29",
         "notes": "GOG's year-end sale — niche but loyal DRM-free audience"},
        {"month": 12, "label": "PlayStation Wrap-Up",      "severity": "low",    "category": "showcase",    "approx_date": "2026-12-15",
         "notes": "Sony's year-in-review — social media noise, minimal launch impact"},
        {"month": 12, "label": "Steam Winter Sale",        "severity": "medium", "category": "sale",        "approx_date": "2026-12-18", "end_date": "2027-01-05",
         "notes": "Year-end sale — launch by Dec 15 or hold until January"},
        {"month": 12, "label": "Epic Games Store Free Games","severity":"low",   "category": "sale",        "approx_date": "2026-12-20", "end_date": "2026-12-31",
         "notes": "Epic's annual free games giveaway — player attention on free titles"},

        # ══════════════ 2027 — CONFIRMED & TBC ══════════════
        {"month": 1,  "label": "CES 2027",                 "severity": "low",    "category": "trade_show",  "approx_date": "2027-01-06", "end_date": "2027-01-09",
         "notes": "Consumer Electronics Show, Las Vegas — gaming-adjacent, confirmed dates"},
        {"month": 1,  "label": "Taipei Game Show 2027 [TBC]","severity": "low",  "category": "trade_show",  "approx_date": "2027-01-28", "end_date": "2027-01-31",
         "notes": "Taiwan's largest game expo — approximate dates based on 2026 pattern"},
        {"month": 2,  "label": "D.I.C.E. Summit 2027",     "severity": "low",    "category": "trade_show",  "approx_date": "2027-02-16", "end_date": "2027-02-18",
         "notes": "AIAS industry summit, ARIA Las Vegas — confirmed dates"},
        {"month": 2,  "label": "DICE Awards 2027",         "severity": "low",    "category": "award",       "approx_date": "2027-02-18",
         "notes": "30th Annual DICE Awards — closes the D.I.C.E. Summit"},
        {"month": 2,  "label": "Nintendo Direct 2027 [TBC]","severity":"medium", "category": "showcase",    "approx_date": "2027-02-17",
         "notes": "Nintendo typically runs a Feb Direct — approximate date"},
        {"month": 2,  "label": "Steam Next Fest 2027 [TBC]","severity":"high",   "category": "festival",    "approx_date": "2027-02-22", "end_date": "2027-03-01",
         "notes": "Demo festival — approximate dates based on 2026 pattern"},
        {"month": 3,  "label": "GDC 2027",                 "severity": "high",   "category": "trade_show",  "approx_date": "2027-03-01", "end_date": "2027-03-05",
         "notes": "GDC Festival of Gaming, Moscone Center SF — confirmed dates, now rebranded"},
        {"month": 3,  "label": "IGF Awards 2027 [TBC]",    "severity": "low",    "category": "award",       "approx_date": "2027-03-03",
         "notes": "Independent Games Festival awards at GDC — approximate date"},
        {"month": 3,  "label": "GDC Awards 2027 [TBC]",    "severity": "low",    "category": "award",       "approx_date": "2027-03-03",
         "notes": "Game Developers Choice Awards at GDC — approximate date"},
        {"month": 3,  "label": "PAX East 2027 [TBC]",      "severity": "medium", "category": "trade_show",  "approx_date": "2027-03-25", "end_date": "2027-03-28",
         "notes": "Major consumer expo, Boston — approximate dates based on 2026 pattern"},
        {"month": 4,  "label": "BAFTA Games Awards 2027 [TBC]","severity":"low", "category": "award",       "approx_date": "2027-04-15",
         "notes": "British Academy Games Awards — approximate date"},
        {"month": 4,  "label": "DCP 2027 [TBC]",           "severity": "medium", "category": "award",       "approx_date": "2027-04-29",
         "notes": "Deutscher Computerspielpreis — approximate date based on 2026 pattern"},
    ]




# ── Notable Major Releases (curated) ────────────────────────────────────────
# Big titles that dominate the release window. Updated manually.
# These get merged into the month_index alongside RAWG data.
# Add [TBC] suffix to title if date is unconfirmed.

def get_notable_releases():
    return [
        # ══════════════ 2026 — CONFIRMED ══════════════
        {"title": "Marvel's Wolverine",          "date": "2026-09-15", "genres": ["Action", "Adventure", "Superhero"],       "platforms": ["PS5"],
         "notes": "Insomniac Games — PS5 exclusive, confirmed Sept 15"},
        {"title": "007: First Light",            "date": "2026-10-13", "genres": ["Action", "Stealth", "FPS"],              "platforms": ["PS5", "Xbox", "PC"],
         "notes": "IO Interactive — James Bond origin story"},
        {"title": "Grand Theft Auto VI",         "date": "2026-11-19", "genres": ["Action", "Open World", "Crime"],         "platforms": ["PS5", "Xbox"],
         "notes": "Rockstar Games — confirmed Nov 19, console only at launch. THE event of the year. Every publisher is clearing the runway."},
        {"title": "Forza Horizon 6",             "date": "2026-10-01", "genres": ["Racing", "Open World"],                  "platforms": ["Xbox", "PC"],
         "notes": "Playground Games — expected Q4, date approximate",         "tbc": True},
        {"title": "Metal Gear Solid Collection Vol. 2","date":"2026-09-01","genres":["Action","Stealth","Remaster"],         "platforms": ["PS5", "Xbox", "PC"],
         "notes": "Konami — confirmed 2026, date approximate",               "tbc": True},
        {"title": "Ace Combat 8",                "date": "2026-10-01", "genres": ["Flight", "Action", "Simulation"],        "platforms": ["PS5", "Xbox", "PC"],
         "notes": "Bandai Namco — confirmed 2026, date approximate",         "tbc": True},
        {"title": "Control: Resonant",           "date": "2026-08-01", "genres": ["Action", "Shooter", "Supernatural"],     "platforms": ["PS5", "Xbox", "PC"],
         "notes": "Remedy Entertainment — confirmed 2026",                   "tbc": True},
        {"title": "Metro 2039",                  "date": "2026-10-01", "genres": ["FPS", "Horror", "Post-Apocalyptic"],     "platforms": ["PS5", "Xbox", "PC"],
         "notes": "4A Games — announced April 2026, date approximate",       "tbc": True},
        {"title": "Assassin's Creed Black Flag Remake","date":"2026-11-01","genres":["Action","Adventure","Open World"],     "platforms": ["PS5", "Xbox", "PC"],
         "notes": "Ubisoft — announced April 2026, date approximate",        "tbc": True},
        {"title": "Halo: Campaign Evolved",      "date": "2026-11-01", "genres": ["FPS", "Sci-Fi", "Remaster"],            "platforms": ["Xbox", "PC"],
         "notes": "343 Industries — confirmed 2026, date approximate",       "tbc": True},

        # ══════════════ 2027 — TBC ══════════════
        {"title": "GTA VI (PC) [TBC]",           "date": "2027-02-01", "genres": ["Action", "Open World", "Crime"],         "platforms": ["PC"],
         "notes": "Rockstar Games — PC release widely expected early 2027 based on GTA V pattern, NOT confirmed", "tbc": True},
        {"title": "Gears of War: E-Day [TBC]",   "date": "2027-06-01", "genres": ["Shooter", "Action", "Co-op"],           "platforms": ["Xbox", "PC"],
         "notes": "The Coalition — confirmed in development, 2027 release widely expected",                       "tbc": True},
    ]

# ── Month index ───────────────────────────────────────────────────────────────

def build_month_index(upcoming, historical_by_month):
    """Build per-month lookup combining upcoming releases + historical comps.
    Uses year-month keys (e.g. '2026-05') for multi-year support."""
    index = {}

    # Standard 2026 months 1-12 (always present for the checker tool)
    for m in range(1, 13):
        index[str(m)] = {
            "upcoming_releases": [],
            "top_performers": historical_by_month.get(str(m), []),
        }

    # Populate from upcoming releases using year-aware keys
    for r in upcoming:
        year = r.get("year", 2026)
        m = r["month"]

        # Year-month key for calendar (e.g. "2026-05", "2027-01")
        ym_key = f"{year}-{m:02d}"
        if ym_key not in index:
            index[ym_key] = {"upcoming_releases": [], "top_performers": []}

        entry = {
            "title": r["title"], "date": r["date_iso"], "genres": r.get("genres", [])[:3],
            "hype": r.get("hype_score", 0), "metacritic": r.get("metacritic", 0),
            "rating": r.get("rating", 0), "source": r.get("source", ""),
        }

        index[ym_key]["upcoming_releases"].append(entry)

        # Also add to simple month key (1-12) for the checker tool (2026 only)
        if year == 2026:
            index[str(m)]["upcoming_releases"].append(entry)

    # Sort each month's releases by hype descending, cap at 15
    for key in index:
        releases = index[key]["upcoming_releases"]
        releases.sort(key=lambda x: x.get("hype", 0), reverse=True)
        index[key]["upcoming_releases"] = releases[:15]

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

    # 5. Notable curated releases
    print("🎮 Adding notable curated releases…")
    notable = get_notable_releases()
    # Merge into upcoming, avoiding duplicates
    existing_titles_lower = {r["title"].lower() for r in all_upcoming}
    for nr in notable:
        if nr["title"].lower().replace(" [tbc]", "") not in existing_titles_lower:
            try:
                dt = __import__("datetime").date.fromisoformat(nr["date"])
            except Exception:
                continue
            item = {
                "title": nr["title"] if not nr.get("tbc") else (nr["title"] if "[TBC]" in nr["title"] else nr["title"] + " [TBC]"),
                "date_iso": nr["date"],
                "month": dt.month,
                "year": dt.year,
                "genres": nr.get("genres", []),
                "platforms": nr.get("platforms", []),
                "hype_score": 99999 if "Grand Theft Auto" in nr["title"] else 9999,
                "metacritic": 0,
                "rating": 0,
                "source": "curated",
            }
            all_upcoming.append(item)
    all_upcoming.sort(key=lambda x: (x.get("date_iso", ""), -x.get("hype_score", 0)))
    print(f"  ✓ {len(notable)} notable releases merged")

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

    # 8. Generate ICS calendar file
    print("📅 Generating ICS calendar file…")
    ics_path = os.path.join(os.path.dirname(OUTPUT_PATH), "games-industry.ics")
    generate_ics(all_upcoming, get_industry_events(), ics_path)

    kb = os.path.getsize(OUTPUT_PATH) / 1024
    print(f"\n✅ Done → {OUTPUT_PATH} ({kb:.1f} KB)")
    print(f"   Upcoming: {len(all_upcoming)} releases")
    for m in range(1, 13):
        u = len(month_index[str(m)]["upcoming_releases"])
        h = len(month_index[str(m)]["top_performers"])
        if u or h: print(f"   Month {m:2d}: {u} upcoming · {h} historical comps")


# ── ICS Calendar Generation ──────────────────────────────────────────────────

def ics_escape(text):
    """Escape text for ICS format."""
    return (text or "").replace("\\", "\\\\").replace(";", "\\;").replace(",", "\\,").replace("\n", "\\n")


def generate_ics(upcoming_releases, events, output_path):
    """
    Generate a subscribable .ics calendar file containing all events
    and upcoming game releases.
    """
    branding = "Powered by ZR Consulting — www.zrconsulting.de"

    lines = [
        "BEGIN:VCALENDAR",
        "VERSION:2.0",
        "PRODID:-//ZR Consulting//Games Industry Calendar//EN",
        "CALSCALE:GREGORIAN",
        "METHOD:PUBLISH",
        "X-WR-CALNAME:Games Industry Calendar — ZR Consulting",
        "X-WR-TIMEZONE:Europe/Berlin",
        "REFRESH-INTERVAL;VALUE=DURATION:P1D",
        "X-PUBLISHED-TTL:P1D",
    ]

    uid_counter = 0

    # Industry events
    CAT_LABELS = {
        "trade_show": "Trade Show / Expo",
        "showcase":   "Showcase / Direct",
        "sale":       "Sale / Promotion",
        "award":      "Awards Ceremony",
        "festival":   "Festival / Event",
    }

    for e in events:
        date_str = e.get("approx_date", "")
        if not date_str:
            continue
        clean_date = date_str.replace("-", "")
        # For multi-day events, use end_date; ICS DTEND is exclusive so add 1 day
        end_str = e.get("end_date", "")
        if end_str:
            # ICS DTEND for all-day events is exclusive — add 1 day
            end_dt = datetime.datetime.strptime(end_str, "%Y-%m-%d") + datetime.timedelta(days=1)
            clean_end = end_dt.strftime("%Y%m%d")
        else:
            # Single-day event — end = start + 1 day (ICS convention)
            start_dt = datetime.datetime.strptime(date_str, "%Y-%m-%d") + datetime.timedelta(days=1)
            clean_end = start_dt.strftime("%Y%m%d")

        cat_label = CAT_LABELS.get(e.get("category", ""), "Industry Event")
        uid = f"{clean_date}-evt-{uid_counter}@zrconsulting.de"
        uid_counter += 1

        sev = e.get("severity", "medium").upper()
        title = e["label"]
        duration_note = ""
        if end_str:
            duration_note = f"\\nDates: {date_str} to {end_str}\\n"
        desc = ics_escape(
            f"{e.get('notes', '')}\\n{duration_note}\\n"
            f"Category: {cat_label}\\n"
            f"Impact: {sev}\\n\\n"
            f"{branding}"
        )

        lines.append("BEGIN:VEVENT")
        lines.append(f"DTSTART;VALUE=DATE:{clean_date}")
        lines.append(f"DTEND;VALUE=DATE:{clean_end}")
        lines.append(f"SUMMARY:{ics_escape(title)}")
        lines.append(f"DESCRIPTION:{desc}")
        lines.append(f"CATEGORIES:{cat_label}")
        lines.append(f"UID:{uid}")
        lines.append("STATUS:CONFIRMED")
        lines.append("TRANSP:TRANSPARENT")
        lines.append("END:VEVENT")

    # Upcoming releases
    for r in upcoming_releases:
        date_str = r.get("date_iso", "")
        if not date_str:
            continue
        clean_date = date_str.replace("-", "")
        uid = f"{clean_date}-rel-{uid_counter}@zrconsulting.de"
        uid_counter += 1

        genres = ", ".join(r.get("genres", [])[:4]) or "Game"
        title = r["title"]
        desc = ics_escape(
            f"Game Release: {title}\\n"
            f"Genre: {genres}\\n"
            f"Platforms: {', '.join(r.get('platforms', [])[:4])}\\n\\n"
            f"{branding}"
        )

        lines.append("BEGIN:VEVENT")
        lines.append(f"DTSTART;VALUE=DATE:{clean_date}")
        lines.append(f"DTEND;VALUE=DATE:{clean_date}")
        lines.append(f"SUMMARY:🎮 {ics_escape(title)}")
        lines.append(f"DESCRIPTION:{desc}")
        lines.append("CATEGORIES:Game Release")
        lines.append(f"UID:{uid}")
        lines.append("STATUS:CONFIRMED")
        lines.append("TRANSP:TRANSPARENT")
        lines.append("END:VEVENT")

    lines.append("END:VCALENDAR")

    with open(output_path, "w", encoding="utf-8") as f:
        f.write("\r\n".join(lines))

    count = lines.count("BEGIN:VEVENT")
    print(f"  ✓ ICS: {count} events written to {output_path}")


if __name__ == "__main__":
    main()
