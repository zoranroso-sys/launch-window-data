#!/usr/bin/env python3
"""
Launch Window Conflict Checker + Games Industry Calendar — Data Fetcher
ZR Consulting · zrconsulting.de

Sources:
  - RAWG.io API        → upcoming releases (platforms, genres, description)
  - IGDB via Twitch    → upcoming releases (secondary, fills gaps)
  - SteamSpy           → live CCU enrichment for historical titles
  - Supabase           → admin-approved events & releases (with description, url, location)
  - Curated hardcoded  → notable upcoming releases, 70+ industry events

Writes:
  data/releases.json
  data/games-industry.ics
  data/games-industry-events-aaa.ics

Secrets (GitHub Actions):
  RAWG_API_KEY, TWITCH_CLIENT_ID, TWITCH_CLIENT_SECRET, SUPABASE_ANON_KEY
"""

import os, json, time, datetime, requests, traceback

# ─── CONFIG ──────────────────────────────────────────────────
RAWG_API_KEY         = os.environ.get("RAWG_API_KEY", "")
TWITCH_CLIENT_ID     = os.environ.get("TWITCH_CLIENT_ID", "")
TWITCH_CLIENT_SECRET = os.environ.get("TWITCH_CLIENT_SECRET", "")
SUPABASE_URL         = "https://qrqikdqroupwselefmyu.supabase.co"
SUPABASE_ANON_KEY    = os.environ.get("SUPABASE_ANON_KEY", "")

OUTPUT_PATH = os.path.join(os.path.dirname(__file__), "..", "data", "releases.json")
ICS_FULL    = os.path.join(os.path.dirname(__file__), "..", "data", "games-industry.ics")
ICS_AAA     = os.path.join(os.path.dirname(__file__), "..", "data", "games-industry-events-aaa.ics")
UPCOMING_MONTHS = 6


# ─── RAWG (primary) ─────────────────────────────────────────

def fetch_rawg_upcoming():
    if not RAWG_API_KEY:
        print("  ⚠️  No RAWG_API_KEY — skipping RAWG.")
        return []
    today = datetime.date.today()
    end = today + datetime.timedelta(days=UPCOMING_MONTHS * 30)
    releases = []
    page = 1
    while page <= 5:
        try:
            r = requests.get("https://api.rawg.io/api/games", params={
                "key": RAWG_API_KEY,
                "dates": f"{today},{end}",
                "ordering": "-added",
                "page_size": 40,
                "page": page,
            }, timeout=15)
            data = r.json()
            results = data.get("results", [])
            if not results:
                break
            for g in results:
                platforms = [p["platform"]["name"] for p in (g.get("platforms") or []) if p.get("platform")]
                genres = [gen["name"] for gen in (g.get("genres") or [])]
                releases.append({
                    "title": g["name"],
                    "date_iso": g.get("released") or g.get("tba") and f"{end.year}-12-31" or "",
                    "platforms": platforms,
                    "genres": genres[:4],
                    "description": (g.get("description_raw") or "")[:300],
                    "metacritic": g.get("metacritic") or 0,
                    "rating": round(g.get("rating") or 0, 1),
                    "hype_score": g.get("added") or 0,
                    "source": "rawg",
                    "slug": g.get("slug", ""),
                    "background_image": g.get("background_image", ""),
                })
            if not data.get("next"):
                break
            page += 1
            time.sleep(0.5)
        except Exception as e:
            print(f"  RAWG page {page} error: {e}")
            break
    print(f"  RAWG: {len(releases)} upcoming releases")
    return releases


# ─── IGDB (secondary) ───────────────────────────────────────

def get_igdb_token():
    if not TWITCH_CLIENT_ID or not TWITCH_CLIENT_SECRET:
        return None
    try:
        r = requests.post("https://id.twitch.tv/oauth2/token", params={
            "client_id": TWITCH_CLIENT_ID,
            "client_secret": TWITCH_CLIENT_SECRET,
            "grant_type": "client_credentials"
        }, timeout=10)
        return r.json().get("access_token")
    except:
        return None

IGDB_PLATFORMS = {
    6: "PC", 48: "PS4", 167: "PS5", 49: "Xbox One", 169: "Xbox Series X|S",
    130: "Switch", 170: "Stadia", 34: "Android", 39: "iOS"
}
IGDB_GENRES = {
    2: "Point-and-click", 4: "Fighting", 5: "Shooter", 7: "Music", 8: "Platform",
    9: "Puzzle", 10: "Racing", 11: "RTS", 12: "RPG", 13: "Simulator",
    14: "Sport", 15: "Strategy", 16: "TBS", 24: "Tactical", 25: "Hack and Slash",
    26: "Quiz", 30: "Pinball", 31: "Adventure", 32: "Indie", 33: "Arcade",
    34: "Visual Novel", 35: "Card Game", 36: "MOBA",
}

def fetch_igdb_upcoming(token):
    if not token:
        print("  ⚠️  No IGDB token — skipping.")
        return []
    now_ts = int(time.time())
    end_ts = now_ts + UPCOMING_MONTHS * 30 * 86400
    releases = []
    try:
        r = requests.post("https://api.igdb.com/v4/games", headers={
            "Client-ID": TWITCH_CLIENT_ID,
            "Authorization": f"Bearer {token}",
        }, data=f"""
            fields name, first_release_date, platforms, genres, summary, hypes, follows;
            where first_release_date > {now_ts} & first_release_date < {end_ts} & hypes > 5;
            sort hypes desc;
            limit 100;
        """, timeout=15)
        for g in r.json():
            platforms = [IGDB_PLATFORMS.get(p, f"Platform {p}") for p in (g.get("platforms") or [])]
            genres = [IGDB_GENRES.get(gid, "Unknown") for gid in (g.get("genres") or [])]
            rd = g.get("first_release_date")
            date_iso = datetime.datetime.utcfromtimestamp(rd).strftime("%Y-%m-%d") if rd else ""
            releases.append({
                "title": g["name"],
                "date_iso": date_iso,
                "platforms": platforms,
                "genres": genres[:4],
                "description": (g.get("summary") or "")[:300],
                "metacritic": 0,
                "rating": 0,
                "hype_score": (g.get("hypes") or 0) + (g.get("follows") or 0),
                "source": "igdb",
            })
    except Exception as e:
        print(f"  IGDB error: {e}")
    print(f"  IGDB: {len(releases)} upcoming releases")
    return releases


# ─── SUPABASE APPROVED EVENTS ──────────────────────────────

def fetch_supabase_events():
    if not SUPABASE_ANON_KEY:
        print("  ⚠️  No SUPABASE_ANON_KEY — skipping.")
        return []
    items = []
    try:
        r = requests.get(
            f"{SUPABASE_URL}/rest/v1/approved_events?published=eq.true&select=*",
            headers={
                "apikey": SUPABASE_ANON_KEY,
                "Authorization": f"Bearer {SUPABASE_ANON_KEY}",
            },
            timeout=15,
        )
        rows = r.json()
        for row in rows:
            items.append({
                "label": row["label"],
                "category": row["category"],
                "severity": row.get("severity", "low"),
                "approx_date": row["approx_date"],
                "end_date": row.get("end_date"),
                "notes": row.get("notes", ""),
                "description": row.get("description") or row.get("notes") or "",
                "tbc": row.get("tbc", False),
                "is_release": row.get("is_release", False),
                "tier": row.get("tier", "indie"),
                "genres": row.get("genres") or [],
                "platforms": row.get("platforms") or [],
                "url": row.get("url", ""),
                "location": row.get("location", ""),
                "developer": row.get("developer", ""),
                "publisher": row.get("publisher", ""),
            })
        print(f"  Supabase: {len(items)} approved events/releases")
    except Exception as e:
        print(f"  Supabase error: {e}")
    return items


# ─── STEAMSPY ENRICHMENT ────────────────────────────────────

def fetch_steamspy_enrichment():
    enrichment = {}
    try:
        r = requests.get("https://steamspy.com/api.php?request=top100in2weeks", timeout=15)
        for appid, data in r.json().items():
            enrichment[data.get("name", "").lower()] = {
                "ccu": data.get("ccu", 0),
                "owners": data.get("owners", ""),
                "price": data.get("price", 0),
            }
        print(f"  SteamSpy: {len(enrichment)} titles enriched")
    except Exception as e:
        print(f"  SteamSpy error: {e}")
    return enrichment


# ─── CURATED NOTABLE RELEASES ──────────────────────────────

CURATED_RELEASES = [
    {"title":"Grand Theft Auto VI","date_iso":"2026-05-26","platforms":["PS5","Xbox Series X|S"],"genres":["Action","Open World"],"description":"Rockstar Games' next open-world crime epic set in Leonida (Vice City). Most anticipated game of the decade.","hype_score":99999,"tier":"aaa","developer":"Rockstar Games","publisher":"Take-Two Interactive"},
    {"title":"Metroid Prime 4: Beyond","date_iso":"2025-09-05","platforms":["Switch 2"],"genres":["Action","Adventure","Metroidvania"],"description":"Long-awaited continuation of Samus Aran's first-person adventure saga from Retro Studios.","hype_score":99999,"tier":"aaa","developer":"Retro Studios","publisher":"Nintendo"},
    {"title":"Death Stranding 2","date_iso":"2025-06-26","platforms":["PS5"],"genres":["Action","Open World"],"description":"Hideo Kojima's sequel to the strand game phenomenon. New traversal mechanics and expanded world.","hype_score":99999,"tier":"aaa","developer":"Kojima Productions","publisher":"Sony"},
    {"title":"Ghost of Yotei","date_iso":"2025-10-01","platforms":["PS5"],"genres":["Action","Open World"],"description":"Sucker Punch sequel set in 1600s Hokkaido with a new female protagonist wielding katana and rifle.","hype_score":99999,"tier":"aaa","developer":"Sucker Punch","publisher":"Sony"},
    {"title":"Doom: The Dark Ages","date_iso":"2025-05-15","platforms":["PC","PS5","Xbox Series X|S"],"genres":["FPS","Action"],"description":"id Software prequel taking Doom Slayer to a medieval dark fantasy setting with mech combat.","hype_score":9999,"tier":"aaa","developer":"id Software","publisher":"Bethesda"},
    {"title":"Fable","date_iso":"2025-10-01","platforms":["PC","Xbox Series X|S"],"genres":["Action RPG","Open World"],"description":"Playground Games reboot of the beloved RPG franchise with humor, choices, and British fantasy setting.","hype_score":9999,"tier":"aaa","developer":"Playground Games","publisher":"Xbox Game Studios"},
    {"title":"Borderlands 4","date_iso":"2025-09-01","platforms":["PC","PS5","Xbox Series X|S"],"genres":["Looter Shooter","Co-op"],"description":"Gearbox's next entry in the cel-shaded co-op looter shooter franchise. New vault hunters and planets.","hype_score":9999,"tier":"aaa","developer":"Gearbox","publisher":"2K Games"},
    {"title":"Assassin's Creed Shadows","date_iso":"2025-03-20","platforms":["PC","PS5","Xbox Series X|S"],"genres":["Action RPG","Stealth"],"description":"Feudal Japan setting with dual protagonists — an African samurai and a Japanese shinobi.","hype_score":9999,"tier":"aaa","developer":"Ubisoft Quebec","publisher":"Ubisoft"},
    {"title":"Civilization VII","date_iso":"2025-02-11","platforms":["PC","PS5","Xbox Series X|S","Switch"],"genres":["4X","Strategy"],"description":"Next entry in the landmark strategy series. New age-progression system splitting eras into distinct civilizations.","hype_score":9999,"tier":"aaa","developer":"Firaxis Games","publisher":"2K Games"},
    {"title":"Monster Hunter Wilds","date_iso":"2025-02-28","platforms":["PC","PS5","Xbox Series X|S"],"genres":["Action RPG","Co-op"],"description":"Next-gen entry with seamless open zones, living ecosystems, and mount-based traversal across biomes.","hype_score":9999,"tier":"aaa","developer":"Capcom","publisher":"Capcom"},
    {"title":"Judas","date_iso":"2026-03-01","platforms":["PC","PS5","Xbox Series X|S"],"genres":["FPS","Immersive Sim"],"description":"Ken Levine's spiritual successor to BioShock. Narrative FPS aboard a generation starship.","hype_score":9999,"tier":"aaa","developer":"Ghost Story Games","publisher":"Take-Two"},
    {"title":"Marvel's Wolverine","date_iso":"2026-09-01","platforms":["PS5"],"genres":["Action","Adventure"],"description":"Insomniac Games' Wolverine title in the Marvel's Spider-Man universe. Mature-rated action.","hype_score":9999,"tier":"aaa","developer":"Insomniac Games","publisher":"Sony"},
]


# ─── INDUSTRY EVENTS (70+) ──────────────────────────────────

INDUSTRY_EVENTS = [
    {"label":"CES 2026","category":"trade_show","approx_date":"2026-01-06","end_date":"2026-01-09","location":"Las Vegas, USA","description":"Consumer Electronics Show — major tech and gaming hardware reveals","url":"https://www.ces.tech"},
    {"label":"DICE Summit 2026","category":"showcase","approx_date":"2026-02-10","end_date":"2026-02-12","location":"Las Vegas, USA","description":"Academy of Interactive Arts & Sciences leadership summit"},
    {"label":"Nintendo Direct (Feb)","category":"showcase","approx_date":"2026-02-15","tbc":True,"description":"Nintendo's direct-to-consumer game announcement showcase"},
    {"label":"PAX East 2026","category":"festival","approx_date":"2026-02-26","end_date":"2026-03-01","location":"Boston, USA","description":"Major consumer gaming convention with hands-on demos and panels","url":"https://east.paxsite.com"},
    {"label":"GDC 2026","category":"trade_show","approx_date":"2026-03-16","end_date":"2026-03-20","location":"San Francisco, USA","description":"Game Developers Conference — the largest professional game dev event","url":"https://gdconf.com"},
    {"label":"SXSW Gaming 2026","category":"festival","approx_date":"2026-03-13","end_date":"2026-03-22","location":"Austin, USA","description":"South by Southwest gaming track with panels, esports, and indie showcases"},
    {"label":"Deutsche Computerspielpreis 2026","category":"award","approx_date":"2026-04-29","location":"Munich, Germany","description":"German Computer Game Award — national game industry ceremony","url":"https://www.deutscher-computerspielpreis.de"},
    {"label":"Tribeca Games 2026","category":"festival","approx_date":"2026-06-10","end_date":"2026-06-22","location":"New York, USA","description":"Tribeca Film Festival gaming track with world premiere demos"},
    {"label":"Summer Game Fest 2026","category":"showcase","approx_date":"2026-06-07","location":"Los Angeles, USA","description":"Geoff Keighley's flagship summer showcase kicking off reveal season","url":"https://www.summergamefest.com"},
    {"label":"Xbox Showcase 2026","category":"showcase","approx_date":"2026-06-08","tbc":True,"description":"Microsoft/Xbox annual game reveal showcase"},
    {"label":"Nintendo Direct (Jun)","category":"showcase","approx_date":"2026-06-10","tbc":True,"description":"Nintendo's E3-adjacent game announcement showcase"},
    {"label":"PC Gaming Show 2026","category":"showcase","approx_date":"2026-06-08","tbc":True,"description":"Annual PC-focused game reveal showcase"},
    {"label":"Devolver Direct 2026","category":"showcase","approx_date":"2026-06-12","tbc":True,"description":"Devolver Digital's irreverent showcase of indie titles"},
    {"label":"Future Games Show 2026","category":"showcase","approx_date":"2026-06-09","tbc":True,"description":"GamesRadar's multi-platform game showcase"},
    {"label":"Wholesome Direct 2026","category":"showcase","approx_date":"2026-06-07","tbc":True,"description":"Showcase of cozy, wholesome, and feel-good indie games"},
    {"label":"Annapurna Showcase 2026","category":"showcase","approx_date":"2026-06-11","tbc":True,"description":"Annapurna Interactive's annual showcase of artful indie games"},
    {"label":"Latin America Games Showcase","category":"showcase","approx_date":"2026-06-09","tbc":True,"description":"Showcase highlighting Latin American game development"},
    {"label":"BitSummit 2026","category":"festival","approx_date":"2026-07-18","end_date":"2026-07-20","location":"Kyoto, Japan","description":"Japan's premier indie game festival with playable demos and talks"},
    {"label":"EVO 2026","category":"festival","approx_date":"2026-07-18","end_date":"2026-07-20","location":"Las Vegas, USA","description":"World's largest fighting game tournament and community event","url":"https://www.evo.gg"},
    {"label":"ChinaJoy 2026","category":"trade_show","approx_date":"2026-07-31","end_date":"2026-08-03","location":"Shanghai, China","description":"China's largest gaming expo with B2B and consumer zones"},
    {"label":"gamescom Opening Night Live","category":"showcase","approx_date":"2026-08-18","location":"Cologne, Germany","description":"Geoff Keighley's gamescom kickoff showcase with world premieres"},
    {"label":"gamescom 2026","category":"trade_show","approx_date":"2026-08-19","end_date":"2026-08-23","location":"Cologne, Germany","description":"Europe's largest gaming trade fair with B2B and consumer halls","url":"https://www.gamescom.global"},
    {"label":"PAX West 2026","category":"festival","approx_date":"2026-08-28","end_date":"2026-08-31","location":"Seattle, USA","description":"Major consumer gaming convention with hands-on demos and panels"},
    {"label":"Tokyo Game Show 2026","category":"trade_show","approx_date":"2026-09-24","end_date":"2026-09-27","location":"Chiba, Japan","description":"Japan's premier game industry expo showcasing domestic and international titles","url":"https://tgs.nikkeibp.co.jp/tgs/2026/en/"},
    {"label":"PlayStation Showcase 2026","category":"showcase","approx_date":"2026-09-15","tbc":True,"description":"Sony's annual PlayStation game reveal showcase"},
    {"label":"devcom 2026","category":"trade_show","approx_date":"2026-08-17","end_date":"2026-08-18","location":"Cologne, Germany","description":"European game developer conference held alongside gamescom"},
    {"label":"Indie Arena Booth 2026","category":"festival","approx_date":"2026-08-19","end_date":"2026-08-23","location":"Cologne, Germany","description":"Curated indie game showcase at gamescom with playable demos"},
    {"label":"Day of the Devs 2026","category":"festival","approx_date":"2026-11-01","tbc":True,"description":"Double Fine + iam8bit curated showcase of standout indie games"},
    {"label":"Steam Next Fest (Feb)","category":"sale","approx_date":"2026-02-24","end_date":"2026-03-03","description":"Week-long Steam event with game demos and developer livestreams"},
    {"label":"Steam Next Fest (Jun)","category":"sale","approx_date":"2026-06-16","end_date":"2026-06-23","description":"Week-long Steam event with game demos and developer livestreams"},
    {"label":"Steam Next Fest (Oct)","category":"sale","approx_date":"2026-10-13","end_date":"2026-10-20","description":"Week-long Steam event with game demos and developer livestreams"},
    {"label":"Steam Summer Sale","category":"sale","approx_date":"2026-06-25","end_date":"2026-07-09","description":"Steam's annual summer-wide discount event across the entire store"},
    {"label":"Steam Autumn Sale","category":"sale","approx_date":"2026-11-25","end_date":"2026-12-01","description":"Steam's autumn discount event coinciding with US Thanksgiving weekend"},
    {"label":"Steam Winter Sale","category":"sale","approx_date":"2026-12-22","end_date":"2027-01-05","description":"Steam's year-end holiday discount event — largest sale of the year"},
    {"label":"PlayStation Days of Play","category":"sale","approx_date":"2026-05-25","end_date":"2026-06-08","tbc":True,"description":"Sony's annual PlayStation sale with discounts on hardware, games, and PS Plus"},
    {"label":"Xbox Black Friday Sale","category":"sale","approx_date":"2026-11-20","end_date":"2026-12-02","tbc":True,"description":"Microsoft's annual Black Friday sale across Xbox Store and Game Pass"},
    {"label":"The Game Awards 2026","category":"award","approx_date":"2026-12-10","location":"Los Angeles, USA","description":"Geoff Keighley's annual ceremony honoring the best games alongside major world premieres","url":"https://thegameawards.com"},
    {"label":"BAFTA Games Awards 2026","category":"award","approx_date":"2026-04-08","location":"London, UK","description":"British Academy Games Awards recognizing creative excellence in games"},
    {"label":"GDC Awards / IGDA 2026","category":"award","approx_date":"2026-03-20","location":"San Francisco, USA","description":"Game Developers Choice Awards presented at GDC"},
    {"label":"Golden Joystick Awards 2026","category":"award","approx_date":"2026-11-20","tbc":True,"description":"Public-voted gaming awards run by GamesRadar — one of the longest-running ceremonies"},
    {"label":"DICE Awards 2026","category":"award","approx_date":"2026-02-12","location":"Las Vegas, USA","description":"Academy of Interactive Arts & Sciences annual game industry awards"},
    {"label":"New York Game Awards 2026","category":"award","approx_date":"2026-01-21","location":"New York, USA","description":"Annual awards ceremony presented by the NY Videogame Critics Circle"},
    {"label":"Gamescom Award 2026","category":"award","approx_date":"2026-08-22","location":"Cologne, Germany","description":"Awards presented at gamescom recognizing the best games shown at the event"},
    {"label":"Paris Games Week 2026","category":"trade_show","approx_date":"2026-10-22","end_date":"2026-10-26","location":"Paris, France","description":"France's largest gaming consumer event with playable demos and esports","tbc":True},
    {"label":"Brasil Game Show 2026","category":"trade_show","approx_date":"2026-10-08","end_date":"2026-10-12","location":"Sao Paulo, Brazil","description":"Latin America's largest gaming event","tbc":True},
    {"label":"G-Star 2026","category":"trade_show","approx_date":"2026-11-12","end_date":"2026-11-15","location":"Busan, South Korea","description":"South Korea's premier game expo with B2B and consumer areas","tbc":True},
    {"label":"MomoCon 2026","category":"festival","approx_date":"2026-05-21","end_date":"2026-05-24","location":"Atlanta, USA","description":"Gaming, anime, and cosplay convention with indie game showcases"},
    {"label":"IGN Live 2026","category":"festival","approx_date":"2026-06-06","end_date":"2026-06-08","location":"Los Angeles, USA","description":"IGN's in-person fan event with playable demos and panels","tbc":True},
    {"label":"DreamHack Summer 2026","category":"festival","approx_date":"2026-06-13","end_date":"2026-06-16","location":"Jonkoping, Sweden","description":"Major LAN and gaming festival with esports, indie, and cosplay","tbc":True},
    {"label":"Pocket Gamer Connects","category":"trade_show","approx_date":"2026-01-20","end_date":"2026-01-21","location":"London, UK","description":"Mobile and portable gaming B2B conference and expo"},
    {"label":"Reboot Develop Blue 2026","category":"trade_show","approx_date":"2026-04-22","end_date":"2026-04-24","location":"Dubrovnik, Croatia","description":"European game dev conference with talks from AAA and indie studios"},
    {"label":"Nordic Game 2026","category":"trade_show","approx_date":"2026-05-20","end_date":"2026-05-22","location":"Malmo, Sweden","description":"Nordic game industry conference with talks and matchmaking"},
    {"label":"Develop:Brighton 2026","category":"trade_show","approx_date":"2026-07-14","end_date":"2026-07-16","location":"Brighton, UK","description":"UK's leading game developer conference with sessions and expos"},
    {"label":"Game Developer Summit (Cologne)","category":"trade_show","approx_date":"2026-08-17","location":"Cologne, Germany","description":"Pre-gamescom developer summit focused on industry insights"},
    {"label":"Games Industry Gathering","category":"trade_show","approx_date":"2026-09-01","tbc":True,"description":"Invite-only networking event for game industry leadership"},
]


# ─── MERGE & DEDUPLICATE ────────────────────────────────────

def merge_releases(rawg, igdb, supabase_items, curated):
    """Merge all sources, deduplicate by title (RAWG wins, then curated, then IGDB)."""
    seen = {}
    all_items = []

    # Priority 1: curated notable releases
    for r in curated:
        key = r["title"].lower().strip()
        entry = {
            "label": r["title"],
            "date": r.get("date_iso", ""),
            "approx_date": r.get("date_iso", ""),
            "is_release": True,
            "category": "release",
            "tier": r.get("tier", "aaa"),
            "platforms": r.get("platforms", []),
            "genres": r.get("genres", []),
            "description": r.get("description", ""),
            "developer": r.get("developer", ""),
            "publisher": r.get("publisher", ""),
            "url": r.get("url", ""),
            "tbc": r.get("tbc", False),
            "hype_score": r.get("hype_score", 9999),
            "source": "curated",
        }
        seen[key] = entry
        all_items.append(entry)

    # Priority 2: Supabase approved events & releases
    for item in supabase_items:
        key = item["label"].lower().strip()
        if key in seen:
            continue
        entry = {
            "label": item["label"],
            "date": item.get("approx_date", ""),
            "approx_date": item.get("approx_date", ""),
            "end_date": item.get("end_date", ""),
            "is_release": item.get("is_release", False),
            "category": item.get("category", "event"),
            "tier": item.get("tier", "indie"),
            "platforms": item.get("platforms", []),
            "genres": item.get("genres", []),
            "description": item.get("description", ""),
            "developer": item.get("developer", ""),
            "publisher": item.get("publisher", ""),
            "url": item.get("url", ""),
            "location": item.get("location", ""),
            "tbc": item.get("tbc", False),
            "source": "supabase",
        }
        seen[key] = entry
        all_items.append(entry)

    # Priority 3: RAWG releases
    for r in rawg:
        key = r["title"].lower().strip()
        if key in seen:
            # Enrich existing with RAWG data if missing
            existing = seen[key]
            if not existing.get("platforms"):
                existing["platforms"] = r.get("platforms", [])
            if not existing.get("genres"):
                existing["genres"] = r.get("genres", [])
            if not existing.get("description"):
                existing["description"] = r.get("description", "")
            continue
        hype = r.get("hype_score", 0)
        tier = "aaa" if hype >= 9000 else "aa" if hype >= 500 else "indie"
        entry = {
            "label": r["title"],
            "date": r.get("date_iso", ""),
            "approx_date": r.get("date_iso", ""),
            "is_release": True,
            "category": "release",
            "tier": tier,
            "platforms": r.get("platforms", []),
            "genres": r.get("genres", []),
            "description": r.get("description", ""),
            "url": "",
            "tbc": False,
            "hype_score": hype,
            "metacritic": r.get("metacritic", 0),
            "source": "rawg",
        }
        seen[key] = entry
        all_items.append(entry)

    # Priority 4: IGDB releases (fill gaps)
    for r in igdb:
        key = r["title"].lower().strip()
        if key in seen:
            existing = seen[key]
            if not existing.get("platforms"):
                existing["platforms"] = r.get("platforms", [])
            if not existing.get("genres"):
                existing["genres"] = r.get("genres", [])
            if not existing.get("description"):
                existing["description"] = r.get("description", "")
            continue
        hype = r.get("hype_score", 0)
        tier = "aaa" if hype >= 9000 else "aa" if hype >= 500 else "indie"
        entry = {
            "label": r["title"],
            "date": r.get("date_iso", ""),
            "approx_date": r.get("date_iso", ""),
            "is_release": True,
            "category": "release",
            "tier": tier,
            "platforms": r.get("platforms", []),
            "genres": r.get("genres", []),
            "description": r.get("description", ""),
            "url": "",
            "tbc": False,
            "hype_score": hype,
            "source": "igdb",
        }
        seen[key] = entry
        all_items.append(entry)

    # Add industry events
    for evt in INDUSTRY_EVENTS:
        entry = {
            "label": evt["label"],
            "date": evt.get("approx_date", ""),
            "approx_date": evt.get("approx_date", ""),
            "end_date": evt.get("end_date", ""),
            "is_release": False,
            "category": evt.get("category", "event"),
            "location": evt.get("location", ""),
            "description": evt.get("description", ""),
            "url": evt.get("url", ""),
            "tbc": evt.get("tbc", False),
            "source": "curated_event",
        }
        all_items.append(entry)

    return all_items


# ─── ICS GENERATION ─────────────────────────────────────────

def generate_ics(items, filename, filter_fn=None):
    lines = [
        "BEGIN:VCALENDAR",
        "VERSION:2.0",
        "PRODID:-//ZR Consulting//Games Industry Calendar//EN",
        "CALSCALE:GREGORIAN",
        "X-WR-CALNAME:Games Industry Calendar",
        "X-WR-TIMEZONE:Europe/Berlin",
    ]
    for item in items:
        if filter_fn and not filter_fn(item):
            continue
        d = item.get("date") or item.get("approx_date", "")
        if not d:
            continue
        try:
            start = d.replace("-", "")
            end_d = item.get("end_date", "")
            if end_d:
                end = end_d.replace("-", "")
            else:
                dt = datetime.datetime.strptime(d, "%Y-%m-%d") + datetime.timedelta(days=1)
                end = dt.strftime("%Y%m%d")
        except:
            continue

        summary = item["label"].replace(",", "\\,").replace(";", "\\;")
        desc_parts = []
        if item.get("description"):
            desc_parts.append(item["description"])
        if item.get("genres"):
            g = item["genres"] if isinstance(item["genres"], list) else [item["genres"]]
            desc_parts.append("Genres: " + ", ".join(g))
        if item.get("platforms"):
            p = item["platforms"] if isinstance(item["platforms"], list) else [item["platforms"]]
            desc_parts.append("Platforms: " + ", ".join(p))
        desc = " | ".join(desc_parts).replace(",", "\\,").replace(";", "\\;").replace("\n", "\\n")

        lines.append("BEGIN:VEVENT")
        lines.append(f"DTSTART;VALUE=DATE:{start}")
        lines.append(f"DTEND;VALUE=DATE:{end}")
        lines.append(f"SUMMARY:{summary}")
        if desc:
            lines.append(f"DESCRIPTION:{desc}")
        loc = item.get("location", "")
        if loc:
            lines.append(f"LOCATION:{loc.replace(',', chr(92) + ',')}")
        url = item.get("url", "")
        if url:
            lines.append(f"URL:{url}")
        cat = (item.get("category") or "event").upper()
        lines.append(f"CATEGORIES:{cat}")
        lines.append("END:VEVENT")

    lines.append("END:VCALENDAR")

    os.makedirs(os.path.dirname(filename), exist_ok=True)
    with open(filename, "w", encoding="utf-8", newline="\r\n") as f:
        f.write("\r\n".join(lines) + "\r\n")
    print(f"  ICS: {filename} ({len(lines)} lines)")


# ─── MAIN ───────────────────────────────────────────────────

def main():
    print("🚀 ZR Consulting — Data Fetcher")
    print(f"   {datetime.datetime.utcnow().isoformat()}Z\n")

    # 1. Fetch from all sources
    print("📡 Fetching RAWG upcoming…")
    rawg = fetch_rawg_upcoming()

    print("📡 Fetching IGDB upcoming…")
    igdb_token = get_igdb_token()
    igdb = fetch_igdb_upcoming(igdb_token)

    print("📡 Fetching Supabase approved events…")
    supabase_items = fetch_supabase_events()

    print("📡 Fetching SteamSpy enrichment…")
    enrichment = fetch_steamspy_enrichment()

    # 2. Merge all sources
    print("\n🗂  Merging & deduplicating…")
    all_items = merge_releases(rawg, igdb, supabase_items, CURATED_RELEASES)

    # 3. Enrich with SteamSpy CCU data
    for item in all_items:
        key = item["label"].lower().strip()
        if key in enrichment:
            item["ccu"] = enrichment[key].get("ccu", 0)
            item["owners"] = enrichment[key].get("owners", "")

    # 4. Sort by date
    def sort_key(item):
        d = item.get("date") or item.get("approx_date") or "9999-12-31"
        return d
    all_items.sort(key=sort_key)

    # 5. Build output JSON
    output = {
        "meta": {
            "generated_at": datetime.datetime.utcnow().isoformat() + "Z",
            "total_count": len(all_items),
            "release_count": len([i for i in all_items if i.get("is_release")]),
            "event_count": len([i for i in all_items if not i.get("is_release")]),
            "sources": ["rawg", "igdb", "steamspy", "supabase", "curated"],
            "next_update": (datetime.datetime.utcnow() + datetime.timedelta(days=7)).strftime("%Y-%m-%d"),
        },
        "items": all_items,
    }

    os.makedirs(os.path.dirname(OUTPUT_PATH), exist_ok=True)
    with open(OUTPUT_PATH, "w", encoding="utf-8") as f:
        json.dump(output, f, indent=2, ensure_ascii=False, default=str)

    size_kb = os.path.getsize(OUTPUT_PATH) / 1024
    releases = len([i for i in all_items if i.get("is_release")])
    events = len(all_items) - releases
    print(f"\n✅ JSON → {OUTPUT_PATH} ({size_kb:.1f} KB)")
    print(f"   {releases} releases, {events} events, {len(all_items)} total")

    # 6. Generate ICS files
    print("\n📅 Generating ICS feeds…")
    generate_ics(all_items, ICS_FULL)
    generate_ics(all_items, ICS_AAA, filter_fn=lambda i: not i.get("is_release") or i.get("tier") == "aaa")

    print("\n✅ Done!")


if __name__ == "__main__":
    main()
