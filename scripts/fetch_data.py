#!/usr/bin/env python3
"""
Launch Window Conflict Checker — Data Fetcher
ZR Consulting · zrconsulting.de

Fetches:
  - Confirmed upcoming game releases (IGDB via Twitch API)
  - Historical peak CCU / owner estimates (SteamSpy)
  - Recent notable releases (last 90 days, SteamSpy top lists)

Writes: data/releases.json

Run manually:  python scripts/fetch_data.py
Run via CI:    GitHub Actions triggers this every Monday at 06:00 UTC
"""

import os
import json
import time
import datetime
import requests

# ─────────────────────────────────────────────
# CONFIG  (set as GitHub Actions secrets)
# ─────────────────────────────────────────────
TWITCH_CLIENT_ID     = os.environ.get("TWITCH_CLIENT_ID", "")
TWITCH_CLIENT_SECRET = os.environ.get("TWITCH_CLIENT_SECRET", "")

OUTPUT_PATH = os.path.join(os.path.dirname(__file__), "..", "data", "releases.json")

# How many months ahead to look for upcoming releases
UPCOMING_MONTHS = 6

# SteamSpy genres/tags to pull historical top performers from
STEAMSPY_TAGS = [
    "Action", "RPG", "Strategy", "Indie", "Horror",
    "Survival", "Adventure", "Multiplayer", "Casual"
]

# ─────────────────────────────────────────────
# IGDB  (upcoming confirmed releases)
# ─────────────────────────────────────────────

def get_twitch_token():
    """Exchange client credentials for a Twitch OAuth token."""
    if not TWITCH_CLIENT_ID or not TWITCH_CLIENT_SECRET:
        print("⚠️  No Twitch credentials found — skipping IGDB fetch.")
        return None
    r = requests.post(
        "https://id.twitch.tv/oauth2/token",
        params={
            "client_id": TWITCH_CLIENT_ID,
            "client_secret": TWITCH_CLIENT_SECRET,
            "grant_type": "client_credentials",
        },
        timeout=10,
    )
    r.raise_for_status()
    return r.json()["access_token"]


def igdb_query(token, endpoint, body):
    """Send a POST query to IGDB."""
    headers = {
        "Client-ID": TWITCH_CLIENT_ID,
        "Authorization": f"Bearer {token}",
        "Content-Type": "text/plain",
    }
    r = requests.post(
        f"https://api.igdb.com/v4/{endpoint}",
        headers=headers,
        data=body,
        timeout=15,
    )
    r.raise_for_status()
    return r.json()


def fetch_upcoming_releases(token):
    """
    Fetch confirmed releases for the next UPCOMING_MONTHS months.
    Returns a list of dicts: {title, date_iso, month, year, genres, platforms, igdb_id}
    """
    if not token:
        return []

    now = int(time.time())
    future = int((datetime.datetime.now() + datetime.timedelta(days=UPCOMING_MONTHS * 30)).timestamp())

    # IGDB category 0 = main game, status 0 = released, 6 = full_release
    body = f"""
        fields name, first_release_date, genres.name, platforms.name, hypes, rating_count;
        where first_release_date >= {now}
          & first_release_date <= {future}
          & category = 0
          & platforms = (6, 48, 49, 167, 169)  -- PC, PS4, PS5, Xbox Series X/S, Xbox One
          & version_parent = null;
        sort hypes desc;
        limit 100;
    """

    try:
        raw = igdb_query(token, "games", body)
    except Exception as e:
        print(f"  IGDB error: {e}")
        return []

    results = []
    for g in raw:
        if "first_release_date" not in g:
            continue
        dt = datetime.datetime.utcfromtimestamp(g["first_release_date"])
        results.append({
            "title": g.get("name", "Unknown"),
            "date_iso": dt.strftime("%Y-%m-%d"),
            "month": dt.month,
            "year": dt.year,
            "genres": [x["name"] for x in g.get("genres", [])],
            "platforms": [x["name"] for x in g.get("platforms", [])],
            "hype_score": g.get("hypes", 0),
            "source": "igdb",
        })

    print(f"  ✓ IGDB: {len(results)} upcoming releases fetched")
    return results


# ─────────────────────────────────────────────
# STEAMSPY  (historical peak data)
# ─────────────────────────────────────────────

def fetch_steamspy_top(tag, page=0):
    """Fetch top games by tag from SteamSpy (free, no key needed)."""
    try:
        r = requests.get(
            "https://steamspy.com/api.php",
            params={"request": "tag", "tag": tag, "page": page},
            timeout=15,
        )
        r.raise_for_status()
        return r.json()
    except Exception as e:
        print(f"  SteamSpy error ({tag}): {e}")
        return {}


def fetch_steamspy_recent():
    """Fetch top sellers from the last 2 weeks (SteamSpy 'top2weeks')."""
    try:
        r = requests.get(
            "https://steamspy.com/api.php",
            params={"request": "top2weeks"},
            timeout=15,
        )
        r.raise_for_status()
        return r.json()
    except Exception as e:
        print(f"  SteamSpy recent error: {e}")
        return {}


def parse_steamspy_entry(appid, data):
    """Normalise a SteamSpy entry into our schema."""
    owners_raw = data.get("owners", "0 .. 0")
    # SteamSpy returns owners as "20,000,000 .. 50,000,000" — take midpoint
    try:
        parts = owners_raw.replace(",", "").split("..")
        lo = int(parts[0].strip())
        hi = int(parts[1].strip())
        owners_mid = (lo + hi) // 2
    except Exception:
        owners_mid = 0

    return {
        "appid": str(appid),
        "title": data.get("name", "Unknown"),
        "peak_ccu": data.get("ccu", 0),
        "owners_estimate": owners_mid,
        "positive_reviews": data.get("positive", 0),
        "negative_reviews": data.get("negative", 0),
        "tags": list((data.get("tags") or {}).keys())[:8],
        "price_usd": data.get("initialprice", 0) / 100,
        "release_date": data.get("release_date", ""),
        "source": "steamspy",
    }


def fetch_historical_data():
    """
    Pull historical peak CCU + owners data for notable games per genre tag.
    Returns a list of normalised entries.
    """
    seen = set()
    results = []

    for tag in STEAMSPY_TAGS:
        print(f"  Fetching SteamSpy tag: {tag}…")
        raw = fetch_steamspy_top(tag)
        for appid, data in list(raw.items())[:20]:  # top 20 per tag
            if appid in seen:
                continue
            seen.add(appid)
            entry = parse_steamspy_entry(appid, data)
            if entry["peak_ccu"] > 500 or entry["owners_estimate"] > 10000:
                results.append(entry)
        time.sleep(0.6)  # SteamSpy rate limit: ~1 req/sec

    print(f"  ✓ SteamSpy historical: {len(results)} games fetched")
    return results


def fetch_recent_performers():
    """Top sellers from the last 2 weeks — shows what's dominating right now."""
    raw = fetch_steamspy_recent()
    results = []
    seen_appids = set()
    for appid, data in list(raw.items())[:30]:
        if appid in seen_appids:
            continue
        seen_appids.add(appid)
        entry = parse_steamspy_entry(appid, data)
        entry["recent_top_seller"] = True
        results.append(entry)
    print(f"  ✓ SteamSpy recent: {len(results)} top sellers fetched")
    return results


# ─────────────────────────────────────────────
# RELEASE CALENDAR  (curated anchor events)
# ─────────────────────────────────────────────

def get_industry_events():
    """
    Hard-coded recurring industry events that affect launch timing.
    Updated manually when schedules are confirmed.
    These are structural calendar facts, not fetched from an API.
    """
    return [
        # Format: month (1-12), label, severity (high/medium/low), notes
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


# ─────────────────────────────────────────────
# ASSEMBLE + WRITE
# ─────────────────────────────────────────────

def build_month_index(upcoming, historical, recent):
    """
    Build a per-month summary for fast lookup in the HTML tool.
    month_index[month_number] = {
        upcoming_releases: [...],
        top_performers: [...],  # historical reference games
        recent_top_sellers: [...]
    }
    """
    index = {str(m): {"upcoming_releases": [], "top_performers": [], "recent_top_sellers": []} for m in range(1, 13)}

    for r in upcoming:
        m = str(r["month"])
        if m in index:
            index[m]["upcoming_releases"].append({
                "title": r["title"],
                "date": r["date_iso"],
                "genres": r["genres"][:3],
                "hype": r["hype_score"],
            })

    # Sort upcoming by hype descending
    for m in index:
        index[m]["upcoming_releases"].sort(key=lambda x: x.get("hype", 0), reverse=True)

    # Tag historical games to months by release_date string
    for g in historical:
        rd = g.get("release_date", "")
        # SteamSpy date format: "4 Feb, 2022" or "Feb 2022"
        try:
            for fmt in ["%d %b, %Y", "%b %Y", "%B %Y"]:
                try:
                    dt = datetime.datetime.strptime(rd.strip(), fmt)
                    m = str(dt.month)
                    if m in index:
                        index[m]["top_performers"].append({
                            "title": g["title"],
                            "peak_ccu": g["peak_ccu"],
                            "owners": g["owners_estimate"],
                            "tags": g["tags"][:5],
                        })
                    break
                except ValueError:
                    continue
        except Exception:
            pass

    # Deduplicate and cap top performers per month
    for m in index:
        seen_titles = set()
        deduped = []
        for g in sorted(index[m]["top_performers"], key=lambda x: x.get("peak_ccu", 0), reverse=True):
            if g["title"] not in seen_titles:
                seen_titles.add(g["title"])
                deduped.append(g)
            if len(deduped) >= 10:
                break
        index[m]["top_performers"] = deduped

    # Recent top sellers don't have reliable month data — attach to current month
    current_month = str(datetime.datetime.now().month)
    index[current_month]["recent_top_sellers"] = [
        {"title": g["title"], "peak_ccu": g["peak_ccu"], "tags": g["tags"][:5]}
        for g in recent[:10]
    ]

    return index


def main():
    print("🚀 Launch Window Data Fetcher — ZR Consulting")
    print(f"   {datetime.datetime.utcnow().strftime('%Y-%m-%d %H:%M UTC')}")
    print()

    # 1. IGDB upcoming releases
    print("📡 Fetching upcoming releases from IGDB…")
    token = get_twitch_token()
    upcoming = fetch_upcoming_releases(token)

    # 2. SteamSpy historical
    print("\n📊 Fetching historical data from SteamSpy…")
    historical = fetch_historical_data()

    # 3. SteamSpy recent
    print("\n🔥 Fetching recent top sellers from SteamSpy…")
    recent = fetch_recent_performers()

    # 4. Industry events (static)
    events = get_industry_events()

    # 5. Build month index
    print("\n🗂  Building month index…")
    month_index = build_month_index(upcoming, historical, recent)

    # 6. Assemble output
    output = {
        "meta": {
            "generated_at": datetime.datetime.utcnow().isoformat() + "Z",
            "upcoming_count": len(upcoming),
            "historical_count": len(historical),
            "recent_count": len(recent),
            "next_update": (datetime.datetime.utcnow() + datetime.timedelta(days=7)).strftime("%Y-%m-%d"),
        },
        "industry_events": events,
        "month_index": month_index,
        "upcoming_releases": upcoming,
        "historical_games": historical[:200],  # cap to keep JSON size manageable
    }

    # 7. Write
    os.makedirs(os.path.dirname(OUTPUT_PATH), exist_ok=True)
    with open(OUTPUT_PATH, "w", encoding="utf-8") as f:
        json.dump(output, f, indent=2, ensure_ascii=False)

    size_kb = os.path.getsize(OUTPUT_PATH) / 1024
    print(f"\n✅ Written to {OUTPUT_PATH} ({size_kb:.1f} KB)")
    print(f"   Upcoming:   {len(upcoming)} releases")
    print(f"   Historical: {len(historical)} games")
    print(f"   Recent:     {len(recent)} top sellers")


if __name__ == "__main__":
    main()
