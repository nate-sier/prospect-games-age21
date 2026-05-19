from __future__ import annotations

import argparse
import re
from io import StringIO
from pathlib import Path

import pandas as pd
from bs4 import BeautifulSoup, Comment

from common import OUTPUT_COLS, ensure_dir, get_cached_url, project_path, safe_int

COLLEGE_TERMS = [
    "ncaa", "sec", "acc", "big ten", "big 10", "big 12", "pac-12", "pac 12", "college", "university",
    "sun belt", "american athletic", "atlantic coast", "southeastern", "big east", "west coast", "missouri valley",
    "ivy", "atlantic 10", "big west", "conference usa", "western athletic", "wac", "swac", "meac",
    "naia", "njcaa", "juco", "junior college", "division i", "division ii", "division iii",
]
SUMMER_TERMS = [
    "cape cod", "cap cod", "northwoods", "appalachian", "prospect league", "new england collegiate",
    "cal ripken", "valley league", "coastal plain", "west coast league", "expedition", "summer",
    "alaska baseball", "mlb draft league", "perfect game collegiate", "texas collegiate", "necbl",
    "cape", "cotuit", "yarmouth", "orleans", "wareham", "bourne", "falmouth", "harwich", "hyannis",
    "chatham", "usa collegiate", "team usa", "collegiate national",
]
NON_INTERNATIONAL_COUNTRIES = {
    "", "USA", "US", "UNITED STATES", "UNITED STATES OF AMERICA", "CANADA", "PR", "PRI", "PUERTO RICO"
}


def clean_cell(x) -> str:
    if x is None or pd.isna(x):
        return ""
    return re.sub(r"\s+", " ", str(x)).strip()


def flatten_columns(df: pd.DataFrame) -> pd.DataFrame:
    out = df.copy()
    if isinstance(out.columns, pd.MultiIndex):
        cols = []
        for c in out.columns:
            parts = [str(x).strip() for x in c if str(x).strip() and "Unnamed" not in str(x)]
            cols.append(parts[-1] if parts else str(c[-1]).strip())
        out.columns = cols
    else:
        out.columns = [str(c).strip() for c in out.columns]
    return out


def get_all_soups(html: str) -> list[BeautifulSoup]:
    """Baseball-Reference sometimes stores extra tables inside HTML comments."""
    base = BeautifulSoup(html, "lxml")
    soups = [base]
    for c in base.find_all(string=lambda text: isinstance(text, Comment)):
        txt = str(c)
        if "<table" in txt:
            soups.append(BeautifulSoup(txt, "lxml"))
    return soups


def read_standard_batting_tables(html: str, verbose: bool = False) -> tuple[list[pd.DataFrame], list[dict]]:
    """Return only the Register Batting table(s), never Fielding/Roster/rankings.

    Your diagnostic showed the key table is id='standard_batting', caption='Register Batting'.
    This function intentionally ignores every generic table and targets that exact table.
    """
    dfs: list[pd.DataFrame] = []
    audit: list[dict] = []
    seen = set()

    for soup_idx, soup in enumerate(get_all_soups(html)):
        candidates = []
        t = soup.find("table", id="standard_batting")
        if t is not None:
            candidates.append(t)
        # Fallback: caption says Register Batting.
        for table in soup.find_all("table"):
            cap = table.find("caption")
            cap_txt = cap.get_text(" ", strip=True).lower() if cap else ""
            if "register batting" in cap_txt and table not in candidates:
                candidates.append(table)

        for table in candidates:
            tid = table.get("id", "")
            cap = table.find("caption")
            cap_txt = cap.get_text(" ", strip=True) if cap else ""
            key = (tid, cap_txt, str(table)[:500])
            if key in seen:
                continue
            seen.add(key)
            try:
                df = pd.read_html(StringIO(str(table)))[0]
                df = flatten_columns(df)
                # Drop repeated header rows inside BRef tables.
                if "Year" in df.columns:
                    df = df[df["Year"].astype(str).str.lower().str.strip().ne("year")].copy()
                audit.append({
                    "Table_ID": tid,
                    "Caption": cap_txt,
                    "Soup_Index": soup_idx,
                    "Rows_Raw": len(df),
                    "Columns": " | ".join(map(str, df.columns[:50])),
                    "Accepted": True,
                })
                dfs.append(df)
                if verbose:
                    print(f"  standard_batting table accepted: rows={len(df)} cols={list(df.columns)[:12]}")
            except Exception as e:
                audit.append({
                    "Table_ID": tid,
                    "Caption": cap_txt,
                    "Soup_Index": soup_idx,
                    "Rows_Raw": 0,
                    "Columns": "",
                    "Accepted": False,
                    "Reason": str(e),
                })
    if verbose and not dfs:
        print("  no standard_batting/Register Batting table found")
    return dfs, audit


def classify_source_from_values(team: str, league: str, level: str) -> str:
    text = f"{team} {league} {level}".lower()
    if any(term in text for term in SUMMER_TERMS):
        return "Summer"
    lev = str(level).strip().upper().replace(" ", "")
    if lev in {"NCAA", "NCAA-1", "NCAA-2", "NCAA-3", "NCAA1", "NCAA2", "NCAA3", "NAIA", "JC", "NJCAA"}:
        return "College"
    if any(term in text for term in COLLEGE_TERMS):
        return "College"
    return "Pro"


def batting_df_to_candidate_rows(player_row: pd.Series, df: pd.DataFrame) -> list[dict]:
    rows: list[dict] = []
    required = {"Year", "Age", "Tm", "Lg", "Lev", "G"}
    missing = required - set(df.columns)
    if missing:
        return rows

    birth_year = safe_int(player_row.get("Birth_Year"))

    for _, r in df.iterrows():
        year = safe_int(r.get("Year"))
        games = safe_int(r.get("G"))
        if year is None or games is None or games <= 0:
            continue
        age = safe_int(r.get("Age"))
        if age is None and birth_year is not None:
            age = year - birth_year
        if age is None or age < 14 or age > 21:
            continue

        team = clean_cell(r.get("Tm"))
        league = clean_cell(r.get("Lg"))
        level = clean_cell(r.get("Lev"))
        if team.lower() in {"", "total", "totals", "all"}:
            continue

        source = classify_source_from_values(team, league, level)
        is_multi_team_total = bool(re.match(r"^\d+\s+Teams?$", team, flags=re.I))
        is_multi_lg = bool(re.match(r"^\d+\s+Lgs?$", league, flags=re.I))

        rows.append({
            "Player": player_row.get("Player"),
            "Best_Rank": player_row.get("Best_Rank"),
            "Best_Rank_Year": player_row.get("Best_Rank_Year"),
            "Latest_Pipeline_Year": player_row.get("Latest_Pipeline_Year"),
            "Latest_Position": player_row.get("Latest_Position"),
            "Latest_Team": player_row.get("Latest_Team"),
            "BRef_Register_ID": player_row.get("BRef_Register_ID"),
            "BRef_Register_URL": player_row.get("BRef_Register_URL"),
            "Birth_Year": player_row.get("Birth_Year"),
            "Birth_Country": player_row.get("Birth_Country"),
            "Birth_State": player_row.get("Birth_State"),
            "Year": int(year),
            "Age": int(age),
            "Source": source,
            "Games": int(games),
            "Teams": team,
            "Leagues": league,
            "Levels": level,
            "Table_Index": 0,
            "_is_multi_team_total": is_multi_team_total,
            "_is_multi_lg": is_multi_lg,
        })
    return rows


def de_duplicate_bref_batting_rows(rows: list[dict]) -> list[dict]:
    """Avoid double counting BRef split-team rows.

    BRef often includes an aggregate row such as '2 Teams / 2 Lgs / A--A' and then the
    individual team stints. For the research question, the aggregate row is the correct
    total for that player-year-source. If an aggregate exists, keep only the aggregate.
    If no aggregate exists, sum/keep individual rows.
    """
    if not rows:
        return []
    df = pd.DataFrame(rows)
    keep_parts = []
    group_cols = ["Player", "Year", "Age", "Source"]
    for _, g in df.groupby(group_cols, dropna=False):
        aggregate = g[g["_is_multi_team_total"] | g["_is_multi_lg"]].copy()
        if not aggregate.empty:
            # If multiple aggregate-style rows somehow exist, keep the largest G as the total.
            keep_parts.append(aggregate.sort_values("Games", ascending=False).head(1))
        else:
            # If there are duplicate exact rows from visible/comment tables, collapse them.
            keep_parts.append(g.drop_duplicates(subset=["Player", "Year", "Age", "Source", "Games", "Teams", "Leagues", "Levels"]))
    out = pd.concat(keep_parts, ignore_index=True) if keep_parts else pd.DataFrame()
    for c in ["_is_multi_team_total", "_is_multi_lg"]:
        if c in out.columns:
            out = out.drop(columns=[c])
    return out.to_dict("records")


def parse_register_rows(player_row: pd.Series, html: str, verbose: bool = False) -> tuple[list[dict], list[dict]]:
    dfs, table_audit = read_standard_batting_tables(html, verbose=verbose)
    raw_rows: list[dict] = []
    for table_idx, df in enumerate(dfs):
        candidates = batting_df_to_candidate_rows(player_row, df)
        for r in candidates:
            r["Table_Index"] = table_idx
        raw_rows.extend(candidates)
    return de_duplicate_bref_batting_rows(raw_rows), table_audit



DRAFT_HIGH_SCHOOL_TERMS = [
    " high school", " hs", " h.s.", " prep", " academy", " school (", "school in",
]
DRAFT_COLLEGE_TERMS = [
    "university", "college", "state university", "community college", "junior college", "juco",
]
SIGNING_INTL_TERMS = [
    "amateur free agent", "international free agent", "signed as a free agent",
    "signed as amateur", "non-drafted free agent", "undrafted free agent",
]


def extract_bref_bio_context(html: str) -> dict:
    """Extract draft/signing context from the Baseball-Reference Register bio box.

    BRef Register pages usually include lines like:
      Draft: Drafted by ... out of Colleyville Heritage HS ...
      Draft: Drafted by ... from University of Arkansas ...
      Signing Bonus: ... / Amateur Free Agent: ...

    We use this text to classify acquisition pathway. This is more reliable than
    birth country because foreign-born players can be drafted out of U.S. high
    schools, and U.S./Puerto Rico players can sometimes have unusual pathways.
    """
    soup = BeautifulSoup(html, "lxml")
    meta = soup.find(id="meta") or soup
    lines = []
    for tag in meta.find_all(["p", "div", "span", "li"]):
        txt = tag.get_text(" ", strip=True)
        txt = re.sub(r"\s+", " ", txt).strip()
        if txt and len(txt) < 600:
            lines.append(txt)
    full = " | ".join(lines)

    draft_lines = [x for x in lines if re.search(r"\bDraft\s*:", x, flags=re.I) or "Drafted by" in x]
    signing_lines = [x for x in lines if re.search(r"amateur free agent|international free agent|signed", x, flags=re.I)]
    high_school_lines = [x for x in lines if re.search(r"High School\s*:", x, flags=re.I) or re.search(r"\bHS\b", x)]
    school_lines = [x for x in lines if re.search(r"School\s*:", x, flags=re.I) or re.search(r"College\s*:", x, flags=re.I)]

    text_lower = full.lower()
    draft_text = " | ".join(draft_lines)
    signing_text = " | ".join(signing_lines)
    hs_text = " | ".join(high_school_lines)
    school_text = " | ".join(school_lines)

    return {
        "Bio_Text": full,
        "Bio_Text_Lower": text_lower,
        "Draft_Text": draft_text,
        "Draft_Text_Lower": draft_text.lower(),
        "Signing_Text": signing_text,
        "Signing_Text_Lower": signing_text.lower(),
        "High_School_Text": hs_text,
        "School_Text": school_text,
        "Has_Draft": bool(draft_lines),
        "Has_Signing": bool(signing_lines),
        "Has_High_School_Line": bool(high_school_lines),
    }


def infer_acquisition_from_bio_and_rows(player_rows: list[dict], player_meta: pd.Series, bio: dict) -> tuple[str, str, str]:
    """Classify player acquisition pathway.

    Priority:
    1. If BRef has college/NCAA batting rows, this is a College acquisition.
    2. If BRef draft/bio text indicates a high-school draft, classify High School.
    3. If BRef draft text exists and there are no college rows, classify High School.
       This fixes cases where foreign-born players were being misclassified as International
       despite being Rule 4 high-school draftees.
    4. If BRef signing/free-agent language exists and no draft is present, classify International.
    5. Birth country is only a fallback, never the primary determinant.
    """
    if not player_rows:
        return "Unknown", "low", "No parsed batting rows through age 21"

    sources = {r["Source"] for r in player_rows}
    if "College" in sources:
        return "College", "high", "College/NCAA batting rows found through age 21"

    draft_text = bio.get("Draft_Text_Lower", "")
    signing_text = bio.get("Signing_Text_Lower", "")
    bio_text = bio.get("Bio_Text_Lower", "")
    hs_text = str(bio.get("High_School_Text", ""))

    # Explicit BRef high-school context.
    if bio.get("Has_High_School_Line") or any(term in draft_text for term in DRAFT_HIGH_SCHOOL_TERMS):
        return "High School", "high", f"BRef draft/bio text indicates high school path: {hs_text or bio.get('Draft_Text','')[:180]}"

    # Any Rule 4 draft line with no college stat rows should be treated as HS for this analysis.
    # College draftees should have NCAA/college rows on Register Batting; if not, mark medium.
    if bio.get("Has_Draft") or "drafted by" in draft_text:
        if any(term in draft_text for term in DRAFT_COLLEGE_TERMS):
            return "College", "medium", f"BRef draft text appears college-based, but no college batting rows parsed: {bio.get('Draft_Text','')[:180]}"
        return "High School", "medium", f"BRef has Rule 4 draft text and no college rows; treated as high school: {bio.get('Draft_Text','')[:180]}"

    # International/amateur free-agent signing language, when not drafted.
    if any(term in signing_text or term in bio_text for term in SIGNING_INTL_TERMS):
        return "International", "high", f"BRef signing/free-agent language with no Rule 4 draft: {bio.get('Signing_Text','')[:180]}"

    pro_ages = [r["Age"] for r in player_rows if r["Source"] == "Pro"]
    first_pro_age = min(pro_ages) if pro_ages else None
    country = str(player_meta.get("Birth_Country", "")).strip().upper()

    # Fallbacks only.
    if country and country not in NON_INTERNATIONAL_COUNTRIES:
        return "International", "low", f"Fallback only: no draft/signing context found; born outside US/Canada/PR ({country}); first pro age {first_pro_age}"
    if first_pro_age is not None and first_pro_age <= 19:
        return "High School", "low", f"Fallback only: no college rows and first pro age {first_pro_age}"
    return "Unknown", "low", "No college, draft, high-school, or signing context identified"

def infer_acquisition(player_rows: list[dict], player_meta: pd.Series) -> tuple[str, str, str]:
    if not player_rows:
        return "Unknown", "low", "No parsed batting rows through age 21"
    sources = {r["Source"] for r in player_rows}
    if "College" in sources:
        return "College", "high", "College/NCAA rows found through age 21"

    pro_ages = [r["Age"] for r in player_rows if r["Source"] == "Pro"]
    first_pro_age = min(pro_ages) if pro_ages else None
    country = str(player_meta.get("Birth_Country", "")).strip().upper()

    if country and country not in NON_INTERNATIONAL_COUNTRIES:
        return "International", "medium", f"No college rows; born outside US/Canada/PR ({country}); first pro age {first_pro_age}"

    if first_pro_age is not None and first_pro_age <= 19:
        return "High School", "medium", f"No college rows; first pro age {first_pro_age}"
    if first_pro_age is not None:
        return "High School", "low", f"No college rows; first pro age {first_pro_age}; verify manually"
    return "Unknown", "low", "No college or pro rows identified"


def main():
    ap = argparse.ArgumentParser(description="Scrape BRef Register standard_batting tables and write games through age 21.")
    ap.add_argument("--register-urls", default="data/interim/player_register_urls.csv")
    ap.add_argument("--limit", type=int, default=None)
    ap.add_argument("--sleep", type=float, default=25.0, help="Seconds to wait before every uncached Baseball-Reference request.")
    ap.add_argument("--out", default="data/processed/prospect_games_by_player_age.csv")
    ap.add_argument("--cache-dir", default="data/cache/bref_register_pages")
    ap.add_argument("--max-new-pages", type=int, default=None, help="Optional safety cap for uncached Baseball-Reference pages in this run.")
    ap.add_argument("--verbose", action="store_true")
    args = ap.parse_args()

    out_path = project_path(args.out)
    cache_dir = project_path(args.cache_dir)
    ensure_dir(out_path.parent)
    ensure_dir(cache_dir)

    players = pd.read_csv(project_path(args.register_urls))
    players = players[players["BRef_Register_URL"].fillna("").astype(str).str.len() > 0].copy()
    if args.limit:
        players = players.head(args.limit)
    print(f"Resolved players to scrape: {len(players)}")

    all_rows: list[dict] = []
    audit: list[dict] = []
    table_audits: list[dict] = []
    failures: list[dict] = []
    new_pages_fetched = 0

    for i, (_, p) in enumerate(players.iterrows(), start=1):
        print(f"[{i}/{len(players)}] {p['Player']}")
        url = str(p["BRef_Register_URL"])
        rid = str(p.get("BRef_Register_ID", "")).strip() or re.sub(r"[^a-zA-Z0-9_-]", "_", str(p["Player"]))
        cache_file = cache_dir / f"{rid}.html"
        try:
            if args.max_new_pages is not None and not cache_file.exists() and new_pages_fetched >= args.max_new_pages:
                print(f"Reached --max-new-pages={args.max_new_pages}. Stopping cleanly; rerun later to continue.")
                break

            status, html, final_url, from_cache = get_cached_url(url, cache_file, sleep=args.sleep, verbose=args.verbose)
            if not from_cache and status == 200:
                new_pages_fetched += 1
            if status != 200:
                failures.append({"Player": p["Player"], "URL": url, "Status": status, "Reason": "Non-200 response"})
                audit.append({"Player": p["Player"], "URL": url, "Status": status, "Rows": 0, "Cached": from_cache})
                if status == 429:
                    print("Baseball-Reference returned 429. Stopping now; rerun later with cached progress.")
                    break
                continue

            rows, tbl_audit = parse_register_rows(p, html, verbose=args.verbose)
            for ta in tbl_audit:
                ta["Player"] = p["Player"]
                ta["BRef_Register_ID"] = p.get("BRef_Register_ID", "")
            table_audits.extend(tbl_audit)

            bio = extract_bref_bio_context(html)
            acq, conf, reason = infer_acquisition_from_bio_and_rows(rows, p, bio)
            for r in rows:
                r["Acquisition_Type"] = acq
                r["Acquisition_Confidence"] = conf
                r["Acquisition_Reason"] = reason
            all_rows.extend(rows)

            audit.append({
                "Player": p["Player"],
                "URL": url,
                "Status": status,
                "Rows": len(rows),
                "Tables_Found": len(tbl_audit),
                "Tables_Accepted": sum(1 for t in tbl_audit if t.get("Accepted")),
                "Acquisition_Type": acq,
                "Confidence": conf,
                "Reason": reason,
                "Draft_Text": bio.get("Draft_Text", ""),
                "Signing_Text": bio.get("Signing_Text", ""),
                "High_School_Text": bio.get("High_School_Text", ""),
                "School_Text": bio.get("School_Text", ""),
                "Cached": from_cache,
                "Final_URL": final_url,
            })
            if not rows:
                failures.append({"Player": p["Player"], "URL": url, "Status": status, "Reason": "No standard_batting rows through age 21 parsed"})
        except Exception as e:
            msg = str(e)
            failures.append({"Player": p["Player"], "URL": url, "Status": "ERROR", "Reason": msg})
            audit.append({"Player": p["Player"], "URL": url, "Status": "ERROR", "Rows": 0, "Reason": msg, "Cached": cache_file.exists()})
            print(f"  ERROR: {msg}")
            if "429" in msg:
                print("Baseball-Reference rate limit hit. Stopping now. Rerun later; cached pages remain saved.")
                break

    out_df = pd.DataFrame(all_rows)
    if out_df.empty:
        out_df = pd.DataFrame(columns=OUTPUT_COLS)
    else:
        out_df = out_df[OUTPUT_COLS].drop_duplicates()

    out_df.to_csv(out_path, index=False)
    audit_path = out_path.parent / "scrape_audit.csv"
    table_audit_path = out_path.parent / "table_parse_audit.csv"
    fail_path = out_path.parent / "scrape_failures.csv"
    pd.DataFrame(audit).to_csv(audit_path, index=False)
    pd.DataFrame(table_audits).to_csv(table_audit_path, index=False)
    pd.DataFrame(failures).to_csv(fail_path, index=False)

    if not out_df.empty:
        out_df.groupby(["Age", "Acquisition_Type"], dropna=False)["Games"].sum().reset_index().to_csv(out_path.parent / "summary_age_acquisition.csv", index=False)
        out_df.groupby(["Age", "Acquisition_Type", "Source"], dropna=False)["Games"].sum().reset_index().to_csv(out_path.parent / "summary_age_acquisition_source.csv", index=False)

    print(f"Rows written: {len(out_df)}")
    print(f"New Baseball-Reference pages fetched this run: {new_pages_fetched}")
    print(f"Wrote: {out_path.relative_to(project_path('.'))}")
    print(f"Audit: {audit_path.relative_to(project_path('.'))}")
    print(f"Table audit: {table_audit_path.relative_to(project_path('.'))}")
    print(f"Failures: {fail_path.relative_to(project_path('.'))}")


if __name__ == "__main__":
    main()
