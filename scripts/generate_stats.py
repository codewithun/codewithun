#!/usr/bin/env python3
"""
Generate assets/stats.svg (astronaut-themed GitHub stats) from live GitHub data.

Usage:
    GH_TOKEN=... GH_USER=codewithun python scripts/generate_stats.py
    python scripts/generate_stats.py --placeholder   # no network, shows "-" values
    python scripts/generate_stats.py --mock          # fake data, for design preview only

Only the Python standard library is used.
"""
import datetime as dt
import html
import json
import os
import sys
import urllib.request
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
TEMPLATE = Path(__file__).with_name("stats.template.svg")
OUT = ROOT / "assets" / "stats.svg"
HERO_TEMPLATE = Path(__file__).with_name("hero.template.svg")
HERO_OUT = ROOT / "assets" / "hero.svg"
API = "https://api.github.com/graphql"

PROFILE_Q = """
query($login: String!, $after: String) {
  user(login: $login) {
    createdAt
    pullRequests { totalCount }
    issues { totalCount }
    repositoriesContributedTo(first: 1, contributionTypes: [COMMIT, ISSUE, PULL_REQUEST, REPOSITORY]) { totalCount }
    repositories(first: 100, after: $after, ownerAffiliations: OWNER, isFork: false) {
      pageInfo { hasNextPage endCursor }
      nodes {
        stargazerCount
        languages(first: 10, orderBy: {field: SIZE, direction: DESC}) {
          edges { size node { name color } }
        }
      }
    }
  }
}
"""

YEAR_Q = """
query($login: String!, $from: DateTime!, $to: DateTime!) {
  user(login: $login) {
    contributionsCollection(from: $from, to: $to) {
      totalCommitContributions
      contributionCalendar {
        weeks { contributionDays { date contributionCount } }
      }
    }
  }
}
"""


def gql(token, query, variables):
    req = urllib.request.Request(
        API,
        data=json.dumps({"query": query, "variables": variables}).encode(),
        headers={
            "Authorization": f"bearer {token}",
            "Content-Type": "application/json",
            "User-Agent": "astronaut-stats-generator",
        },
    )
    with urllib.request.urlopen(req, timeout=60) as r:
        data = json.load(r)
    if data.get("errors"):
        raise RuntimeError(json.dumps(data["errors"])[:500])
    return data["data"]


def fetch(user, token):
    stars, langs, after = 0, {}, None
    prs = issues = contrib = 0
    created = None
    while True:
        u = gql(token, PROFILE_Q, {"login": user, "after": after})["user"]
        created = u["createdAt"]
        prs, issues = u["pullRequests"]["totalCount"], u["issues"]["totalCount"]
        contrib = u["repositoriesContributedTo"]["totalCount"]
        for repo in u["repositories"]["nodes"]:
            stars += repo["stargazerCount"]
            for e in repo["languages"]["edges"]:
                n = e["node"]
                cur = langs.setdefault(n["name"], [0, n["color"]])
                cur[0] += e["size"]
        page = u["repositories"]["pageInfo"]
        if not page["hasNextPage"]:
            break
        after = page["endCursor"]

    # kontribusi per tahun (API membatasi rentang maksimal 1 tahun)
    start_year = int(created[:4])
    now = dt.datetime.now(dt.timezone.utc)
    days, commits = {}, 0
    for year in range(start_year, now.year + 1):
        frm = dt.datetime(year, 1, 1, tzinfo=dt.timezone.utc)
        to = min(dt.datetime(year, 12, 31, 23, 59, 59, tzinfo=dt.timezone.utc), now)
        cc = gql(token, YEAR_Q, {"login": user, "from": frm.isoformat(), "to": to.isoformat()})[
            "user"]["contributionsCollection"]
        commits += cc["totalCommitContributions"]
        for week in cc["contributionCalendar"]["weeks"]:
            for d in week["contributionDays"]:
                days[d["date"]] = d["contributionCount"]
    return dict(stars=stars, commits=commits, prs=prs, issues=issues, contrib=contrib,
                langs=langs, days=days, created=created[:10])


def streaks(days, today):
    """Return (longest, longest_start, longest_end, current, cur_start, cur_end)."""
    all_days = sorted(d for d in days if d <= today.isoformat())
    parse = dt.date.fromisoformat
    best = (0, None, None)
    run, start = 0, None
    for d in all_days:
        if days[d] > 0:
            if run == 0:
                start = d
            run += 1
            if run > best[0]:
                best = (run, start, d)
        else:
            run = 0
    # streak saat ini: boleh belum ada kontribusi hari ini
    cur, cur_end, cur_start = 0, None, None
    day = today
    if days.get(day.isoformat(), 0) == 0:
        day -= dt.timedelta(days=1)
    while days.get(day.isoformat(), 0) > 0:
        if cur_end is None:
            cur_end = day.isoformat()
        cur_start = day.isoformat()
        cur += 1
        day -= dt.timedelta(days=1)
    return best[0], best[1], best[2], cur, cur_start, cur_end


def fmt_date(iso, with_year=True):
    if not iso:
        return ""
    d = dt.date.fromisoformat(iso)
    return d.strftime("%b %-d, %Y" if with_year else "%b %-d")


def fmt_range(a, b, today_iso):
    if not a:
        return "No streak yet"
    if a == b:
        return fmt_date(a)
    end = "Present" if b >= (dt.date.fromisoformat(today_iso) - dt.timedelta(days=1)).isoformat() else fmt_date(b)
    same_year = a[:4] == (b or "")[:4] and end != "Present"
    return f"{fmt_date(a, not same_year)} – {end if end == 'Present' else fmt_date(b)}"


def lang_rows(langs):
    total = sum(v[0] for v in langs.values()) or 1
    top = sorted(langs.items(), key=lambda kv: kv[1][0], reverse=True)[:5]
    out = []
    for i, (name, (size, color)) in enumerate(top):
        y = 344 + i * 30
        pct = size / total * 100
        w = max(4.0, 196 * pct / 100)
        color = color or "#8b949e"
        out.append(
            f'<circle cx="498" cy="{y-5}" r="5" fill="{color}"/>'
            f'<text class="t" x="512" y="{y}" font-size="15">{html.escape(name)}</text>'
            f'<rect class="bar-bg" x="620" y="{y-9}" width="196" height="8" rx="4"/>'
            f'<rect x="620" y="{y-9}" width="{w:.1f}" height="8" rx="4" fill="{color}"/>'
            f'<text class="m mono" x="870" y="{y}" font-size="12" text-anchor="end">{pct:.1f}%</text>')
    return "".join(out) or '<text class="m" x="492" y="344" font-size="14">No language data yet</text>'


def render(values, template=None):
    svg = (template or TEMPLATE).read_text(encoding="utf-8")
    for k, v in values.items():
        svg = svg.replace("{{" + k + "}}", str(v))
    return svg


def main():
    args = set(sys.argv[1:])
    today = dt.datetime.now(dt.timezone.utc).date()
    updated = today.strftime("%-d %b %Y")

    if "--placeholder" in args:
        dash = "–"
        values = dict(TOTAL=dash, TOTAL_RANGE="waiting for first update", LONG=dash, LONG_RANGE="",
                      CUR=dash, CUR_RANGE="", RING=0, STARS=dash, COMMITS=dash, PRS=dash, ISSUES=dash,
                      CONTRIB=dash, UPDATED=updated, LANGS=lang_rows({}))
        hero_values = dict(CUR=dash, CUR_UNIT="days", TODAY=dash)
    else:
        if "--mock" in args:
            import random
            random.seed(3)
            days = {}
            d = dt.date(2023, 3, 1)
            while d <= today:
                days[d.isoformat()] = 0 if random.random() < 0.38 else random.randint(1, 9)
                d += dt.timedelta(days=1)
            for k in range(12):
                days[(today - dt.timedelta(days=k)).isoformat()] = random.randint(1, 5)
            data = dict(stars=17, commits=1243, prs=58, issues=9, contrib=14, days=days, created="2023-03-01",
                        langs={"PHP": [52000, "#4F5D95"], "JavaScript": [31000, "#f1e05a"],
                               "Python": [24000, "#3572A5"], "Blade": [17000, "#f7523f"], "TypeScript": [9000, "#3178c6"]})
        else:
            user = os.environ.get("GH_USER") or os.environ.get("GITHUB_REPOSITORY_OWNER")
            token = os.environ.get("GH_TOKEN") or os.environ.get("GITHUB_TOKEN")
            if not user or not token:
                sys.exit("Set GH_USER and GH_TOKEN (or GITHUB_TOKEN) first.")
            data = fetch(user, token)

        longest, l0, l1, cur, c0, c1 = streaks(data["days"], today)
        total = sum(data["days"].values())
        ring = 0 if longest == 0 else min(100, cur / longest * 100)
        values = dict(
            TOTAL=f"{total:,}", TOTAL_RANGE=f"{fmt_date(data['created'])} – Present",
            LONG=f"{longest:,}", LONG_RANGE=fmt_range(l0, l1, today.isoformat()) if l0 else "No streak yet",
            CUR=f"{cur:,}", CUR_RANGE=fmt_range(c0, c1, today.isoformat()) if c0 else "No active streak",
            RING=f"{ring:.1f}", STARS=f"{data['stars']:,}", COMMITS=f"{data['commits']:,}",
            PRS=f"{data['prs']:,}", ISSUES=f"{data['issues']:,}", CONTRIB=f"{data['contrib']:,}",
            UPDATED=updated, LANGS=lang_rows(data["langs"]))
        hero_values = dict(CUR=f"{cur:,}", CUR_UNIT="day" if cur == 1 else "days",
                           TODAY=f"{data['days'].get(today.isoformat(), 0):,}")

    out = Path(os.environ.get("STATS_OUT", OUT))
    out.parent.mkdir(parents=True, exist_ok=True)
    out.write_text(render(values), encoding="utf-8")
    print(f"wrote {out}")

    hero_out = Path(os.environ.get("HERO_OUT", HERO_OUT))
    hero_out.parent.mkdir(parents=True, exist_ok=True)
    hero_out.write_text(render(hero_values, HERO_TEMPLATE), encoding="utf-8")
    print(f"wrote {hero_out}")


if __name__ == "__main__":
    main()
