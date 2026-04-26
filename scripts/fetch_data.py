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
        {"month": 1,  "label": "CES",                      "severity": "low",    "category": "trade_show",  "approx_date": "2026-01-06",
         "notes": "Consumer Electronics Show, Las Vegas — gaming-adjacent, can steal tech headlines"},
        {"month": 1,  "label": "Xbox Developer Direct",    "severity": "medium", "category": "showcase",    "approx_date": "2026-01-23",
         "notes": "Microsoft's focused deep-dive showcase — smaller than E3-season but pulls press for 24–48 hours"},
        {"month": 1,  "label": "Steam Winter Sale ends",   "severity": "low",    "category": "sale",        "approx_date": "2026-01-06",
         "notes": "Post-sale browsing traffic is high — good discovery window"},
        {"month": 1,  "label": "Taipei Game Show",         "severity": "low",    "category": "trade_show",  "approx_date": "2026-01-22",
         "notes": "Taiwan's largest game expo — relevant for APAC-facing titles and Japanese publishers"},

        # ══════════════ FEBRUARY ══════════════
        {"month": 2,  "label": "D.I.C.E. Summit",          "severity": "low",    "category": "trade_show",  "approx_date": "2026-02-10",
         "notes": "AIAS industry summit, Las Vegas — senior industry networking, minimal consumer impact"},
        {"month": 2,  "label": "DICE Awards",              "severity": "low",    "category": "award",       "approx_date": "2026-02-12",
         "notes": "Academy of Interactive Arts & Sciences awards — industry recognition, low consumer impact"},
        {"month": 2,  "label": "Nintendo Direct",          "severity": "medium", "category": "showcase",    "approx_date": "2026-02-18",
         "notes": "Nintendo typically runs a Feb Direct — pulls press attention for 48–72 hours"},
        {"month": 2,  "label": "Taipei Game Show (alt)",   "severity": "low",    "category": "trade_show",  "approx_date": "2026-02-05",
         "notes": "Some years TGS shifts to early February — check confirmed dates"},

        # ══════════════ MARCH ══════════════
        {"month": 3,  "label": "GDC",                      "severity": "high",   "category": "trade_show",  "approx_date": "2026-03-16",
         "notes": "Game Developers Conference, San Francisco — press concentrated for a full week, launch coverage squeezed"},
        {"month": 3,  "label": "IGF Awards",               "severity": "low",    "category": "award",       "approx_date": "2026-03-18",
         "notes": "Independent Games Festival awards at GDC — indie credibility moment"},
        {"month": 3,  "label": "GDC Awards",               "severity": "low",    "category": "award",       "approx_date": "2026-03-18",
         "notes": "Game Developers Choice Awards at GDC — industry peer recognition"},
        {"month": 3,  "label": "BAFTA Games Awards",       "severity": "low",    "category": "award",       "approx_date": "2026-03-26",
         "notes": "British Academy awards — strong UK/EU press coverage for 24–48 hours"},
        {"month": 3,  "label": "PAX East",                 "severity": "medium", "category": "trade_show",  "approx_date": "2026-03-05",
         "notes": "Major consumer expo in Boston — demos, influencer coverage, press splits attention"},
        {"month": 3,  "label": "SXSW Gaming",              "severity": "low",    "category": "festival",    "approx_date": "2026-03-13",
         "notes": "South by Southwest gaming track in Austin — cultural crossover press, indie visibility"},

        # ══════════════ APRIL ══════════════
        {"month": 4,  "label": "Steam Spring Sale",        "severity": "medium", "category": "sale",        "approx_date": "2026-03-30",
         "notes": "Typically late March/early April — price anchor effect on new releases"},
        {"month": 4,  "label": "DCP — Deutscher Computerspielpreis", "severity": "medium", "category": "award", "approx_date": "2026-04-23",
         "notes": "German Computer Game Award, Berlin — major DACH industry event, strong regional press coverage, networking"},
        {"month": 4,  "label": "Guerrilla Collective",     "severity": "low",    "category": "showcase",    "approx_date": "2026-04-15",
         "notes": "Indie showcase — sometimes runs in April, sometimes June. Moderate indie press coverage"},
        {"month": 4,  "label": "EGX Berlin",               "severity": "low",    "category": "trade_show",  "approx_date": "2026-04-17",
         "notes": "Consumer expo in Berlin — DACH-focused, good for German market visibility"},

        # ══════════════ MAY ══════════════
        {"month": 5,  "label": "Steam Next Fest",          "severity": "high",   "category": "festival",    "approx_date": "2026-05-04",
         "notes": "Week-long demo festival — hundreds of demos flood discovery, launch signal diluted"},
        {"month": 5,  "label": "PlayStation State of Play", "severity": "medium","category": "showcase",    "approx_date": "2026-05-14",
         "notes": "Sony showcase — pulls press attention and dominates news cycle for 2–3 days"},
        {"month": 5,  "label": "Nordic Game Conference",   "severity": "low",    "category": "trade_show",  "approx_date": "2026-05-20",
         "notes": "Malmö, Sweden — key Nordic/European indie and AA industry event, strong Nordic press"},
        {"month": 5,  "label": "Wholesome Direct",         "severity": "low",    "category": "showcase",    "approx_date": "2026-05-28",
         "notes": "Cozy/wholesome games showcase — niche but passionate audience, strong social media reach"},

        # ══════════════ JUNE ══════════════
        {"month": 6,  "label": "Summer Game Fest",         "severity": "high",   "category": "showcase",    "approx_date": "2026-06-07",
         "notes": "Geoff Keighley's showcase — announcement coverage dominates all gaming media for days"},
        {"month": 6,  "label": "Xbox Games Showcase",      "severity": "high",   "category": "showcase",    "approx_date": "2026-06-08",
         "notes": "Microsoft's annual showcase — major reveals, press fully focused on announcements"},
        {"month": 6,  "label": "Nintendo Direct",          "severity": "high",   "category": "showcase",    "approx_date": "2026-06-10",
         "notes": "E3-season Nintendo Direct — typically their biggest of the year, massive press impact"},
        {"month": 6,  "label": "PC Gaming Show",           "severity": "medium", "category": "showcase",    "approx_date": "2026-06-08",
         "notes": "PC-focused showcase — PC press diverted to coverage for 2–3 days"},
        {"month": 6,  "label": "Ubisoft Forward",          "severity": "medium", "category": "showcase",    "approx_date": "2026-06-09",
         "notes": "Ubisoft's showcase — AAA reveals pull media attention in the E3 window"},
        {"month": 6,  "label": "Future Games Show",        "severity": "medium", "category": "showcase",    "approx_date": "2026-06-08",
         "notes": "Future Publishing's showcase — broad coverage, indie and AA visibility affected"},
        {"month": 6,  "label": "Capcom Showcase",          "severity": "medium", "category": "showcase",    "approx_date": "2026-06-09",
         "notes": "Capcom's dedicated showcase — Monster Hunter, Resident Evil, Street Fighter news dominates"},
        {"month": 6,  "label": "Square Enix Presents",     "severity": "medium", "category": "showcase",    "approx_date": "2026-06-10",
         "notes": "Square Enix showcase — Final Fantasy, Dragon Quest news pulls JRPG audience attention"},
        {"month": 6,  "label": "Bandai Namco Next",        "severity": "low",    "category": "showcase",    "approx_date": "2026-06-10",
         "notes": "Bandai Namco showcase — anime games, Tekken, Elden Ring news when relevant"},
        {"month": 6,  "label": "Devolver Digital Showcase", "severity": "low",   "category": "showcase",    "approx_date": "2026-06-08",
         "notes": "Devolver's showcase — indie-focused, high social media engagement, 24-hour cycle"},
        {"month": 6,  "label": "Day of the Devs",          "severity": "low",    "category": "showcase",    "approx_date": "2026-06-09",
         "notes": "iam8bit's indie showcase — curated indie selection, press-friendly, good discovery moment"},
        {"month": 6,  "label": "Tribeca Games Spotlight",  "severity": "low",    "category": "festival",    "approx_date": "2026-06-12",
         "notes": "Tribeca Festival's gaming track — narrative/art games get cultural press crossover"},
        {"month": 6,  "label": "Annapurna Interactive Showcase","severity":"low", "category": "showcase",    "approx_date": "2026-06-11",
         "notes": "Annapurna's showcase — prestigious indie publisher, strong critical press attention"},
        {"month": 6,  "label": "Latin American Games Showcase","severity":"low",  "category": "showcase",    "approx_date": "2026-06-09",
         "notes": "LATAM-focused showcase — growing market, relevant for Spanish/Portuguese localisation"},

        # ══════════════ JULY ══════════════
        {"month": 7,  "label": "Steam Summer Sale",        "severity": "medium", "category": "sale",        "approx_date": "2026-06-25",
         "notes": "Price anchor effect — players wait for discounts, full-price launches face resistance"},
        {"month": 7,  "label": "Develop:Brighton",         "severity": "low",    "category": "trade_show",  "approx_date": "2026-07-14",
         "notes": "UK developer conference, Brighton — key UK industry networking, talks, and recruitment"},
        {"month": 7,  "label": "BitSummit",                "severity": "low",    "category": "trade_show",  "approx_date": "2026-07-18",
         "notes": "Japanese indie game expo in Kyoto — relevant for Japan-facing indie titles"},
        {"month": 7,  "label": "ChinaJoy",                 "severity": "low",    "category": "trade_show",  "approx_date": "2026-07-24",
         "notes": "China's largest gaming expo, Shanghai — relevant if targeting Chinese market"},
        {"month": 7,  "label": "EA Spotlight",             "severity": "medium", "category": "showcase",    "approx_date": "2026-07-15",
         "notes": "EA's showcase event — sports titles, Battlefield, and major EA IPs pull audience attention"},

        # ══════════════ AUGUST ══════════════
        {"month": 8,  "label": "Gamescom",                 "severity": "high",   "category": "trade_show",  "approx_date": "2026-08-19",
         "notes": "Europe's biggest games show in Cologne — avoid launch week, post-show slot is valuable"},
        {"month": 8,  "label": "Opening Night Live",       "severity": "high",   "category": "showcase",    "approx_date": "2026-08-18",
         "notes": "Keighley's Gamescom kick-off — major reveals, dominates the full week's coverage"},
        {"month": 8,  "label": "Future Games Show at Gamescom","severity":"medium","category":"showcase",   "approx_date": "2026-08-20",
         "notes": "Future's Gamescom showcase — additional coverage saturation during show week"},
        {"month": 8,  "label": "THQ Nordic Showcase",      "severity": "low",    "category": "showcase",    "approx_date": "2026-08-20",
         "notes": "THQ Nordic's Gamescom showcase — AA titles, smaller but pulls genre-specific audiences"},
        {"month": 8,  "label": "Nacon Connect",            "severity": "low",    "category": "showcase",    "approx_date": "2026-08-19",
         "notes": "Nacon's showcase — AA titles, racing, horror, and licensed games"},
        {"month": 8,  "label": "Focus Entertainment What's Next","severity":"low","category":"showcase",    "approx_date": "2026-08-20",
         "notes": "Focus' Gamescom showcase — relevant for Soulslike and AA action fans"},
        {"month": 8,  "label": "Awesome Indies Showcase",  "severity": "low",    "category": "showcase",    "approx_date": "2026-08-20",
         "notes": "Indie showcase at Gamescom — curated indie selection, moderate press coverage"},
        {"month": 8,  "label": "Gamescom Asia (Singapore)","severity": "low",    "category": "trade_show",  "approx_date": "2026-08-27",
         "notes": "Gamescom's Asian edition in Singapore — growing SEA market presence"},

        # ══════════════ SEPTEMBER ══════════════
        {"month": 9,  "label": "Tokyo Game Show",          "severity": "medium", "category": "trade_show",  "approx_date": "2026-09-24",
         "notes": "Japan's largest game show, Makuhari Messe — critical for JRPG/Japanese titles"},
        {"month": 9,  "label": "PlayStation State of Play", "severity": "medium","category": "showcase",    "approx_date": "2026-09-10",
         "notes": "Sony's September showcase — typically focuses on holiday lineup"},
        {"month": 9,  "label": "PlayStation Showcase",     "severity": "high",   "category": "showcase",    "approx_date": "2026-09-15",
         "notes": "Sony's major showcase (when it runs) — bigger than State of Play, huge reveals, rare but impactful"},
        {"month": 9,  "label": "Sega Showcase",            "severity": "low",    "category": "showcase",    "approx_date": "2026-09-20",
         "notes": "Sega's showcase — Yakuza, Sonic, Persona, and strategy franchises pull specific audiences"},

        # ══════════════ OCTOBER ══════════════
        {"month": 10, "label": "Steam Next Fest",          "severity": "medium", "category": "festival",    "approx_date": "2026-10-05",
         "notes": "Autumn demo festival — second occurrence, demo noise competes with launches"},
        {"month": 10, "label": "Steam Autumn Sale",        "severity": "medium", "category": "sale",        "approx_date": "2026-10-27",
         "notes": "Late October sale — price expectations drop as sale approaches"},
        {"month": 10, "label": "Twitch Galaxies",          "severity": "low",    "category": "showcase",    "approx_date": "2026-10-15",
         "notes": "Twitch's own showcase event — streaming-focused reveals, moderate impact"},
        {"month": 10, "label": "Brasil Game Show",         "severity": "low",    "category": "trade_show",  "approx_date": "2026-10-08",
         "notes": "Latin America's largest game show, São Paulo — key for LATAM market visibility"},
        {"month": 10, "label": "EGX London",               "severity": "low",    "category": "trade_show",  "approx_date": "2026-10-22",
         "notes": "UK's largest consumer gaming expo — strong UK press coverage and influencer presence"},
        {"month": 10, "label": "Paris Games Week",         "severity": "low",    "category": "trade_show",  "approx_date": "2026-10-29",
         "notes": "France's consumer gaming expo — relevant for French market, occasionally hosts Sony reveals"},
        {"month": 10, "label": "Milan Games Week",         "severity": "low",    "category": "trade_show",  "approx_date": "2026-10-23",
         "notes": "Italy's largest gaming event — strong Southern European market visibility"},

        # ══════════════ NOVEMBER ══════════════
        {"month": 11, "label": "The Game Awards",          "severity": "high",   "category": "award",       "approx_date": "2026-12-10",
         "notes": "TGA dominates media for 3 weeks — GOTY discourse plus major world premiere reveals"},
        {"month": 11, "label": "Black Friday",             "severity": "medium", "category": "sale",        "approx_date": "2026-11-27",
         "notes": "Consumer attention on deals, not new releases — gifting spike possible"},
        {"month": 11, "label": "G-Star Korea",             "severity": "low",    "category": "trade_show",  "approx_date": "2026-11-12",
         "notes": "South Korea's major gaming expo, Busan — critical for Korean MMO/F2P market"},
        {"month": 11, "label": "Golden Joystick Awards",   "severity": "low",    "category": "award",       "approx_date": "2026-11-20",
         "notes": "GamesRadar/Future's public-voted awards — consumer engagement, moderate press"},
        {"month": 11, "label": "Day of the Devs: Fall Edition","severity":"low",  "category": "showcase",    "approx_date": "2026-11-05",
         "notes": "iam8bit's autumn indie showcase — curated, press-friendly"},
        {"month": 11, "label": "BlizzCon / Xbox equivalent","severity":"medium", "category": "showcase",    "approx_date": "2026-11-07",
         "notes": "Major publisher community event (when it runs) — dominates news for the weekend"},
        {"month": 11, "label": "XO / Xbox Fan Fest",       "severity": "low",    "category": "showcase",    "approx_date": "2026-11-15",
         "notes": "Xbox community event — minor press impact, pulls Xbox-specific audience"},
        {"month": 11, "label": "Women in Games Awards",    "severity": "low",    "category": "award",       "approx_date": "2026-11-18",
         "notes": "Industry diversity awards — networking, visibility for inclusive studios"},
        {"month": 11, "label": "Dreamhack (multiple)",     "severity": "low",    "category": "festival",    "approx_date": "2026-11-20",
         "notes": "LAN/esports festival — multiple dates globally, strong community engagement"},

        # ══════════════ DECEMBER ══════════════
        {"month": 12, "label": "The Game Awards",          "severity": "high",   "category": "award",       "approx_date": "2026-12-10",
         "notes": "TGA show night — world premieres, massive live audience, dominates entire month's coverage"},
        {"month": 12, "label": "Steam Winter Sale",        "severity": "medium", "category": "sale",        "approx_date": "2026-12-18",
         "notes": "Year-end sale — launch by Dec 15 or hold until January"},
        {"month": 12, "label": "PlayStation Wrap-Up",      "severity": "low",    "category": "showcase",    "approx_date": "2026-12-15",
         "notes": "Sony's year-in-review — social media noise, minimal launch impact"},
        {"month": 12, "label": "Indie World / Nintendo",   "severity": "low",    "category": "showcase",    "approx_date": "2026-12-08",
         "notes": "Nintendo sometimes runs a Dec showcase — minor distraction"},
        {"month": 12, "label": "Epic Games Store Free Games","severity":"low",   "category": "sale",        "approx_date": "2026-12-20",
         "notes": "Epic's annual free games giveaway — player attention on free titles, not purchases"},
        {"month": 12, "label": "GOG Winter Sale",          "severity": "low",    "category": "sale",        "approx_date": "2026-12-15",
         "notes": "GOG's year-end sale — niche but loyal DRM-free audience"},
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
