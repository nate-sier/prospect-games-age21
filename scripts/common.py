from __future__ import annotations

import re
import time
from email.utils import parsedate_to_datetime
from datetime import datetime, timezone
import unicodedata
from pathlib import Path
from typing import Optional

import pandas as pd
import requests
from bs4 import BeautifulSoup, Comment

PROJECT_ROOT = Path(__file__).resolve().parents[1]

PITCHER_RE = re.compile(r"(^|/|,|\s)(RHP|LHP|P)(\s|$|/|,)", re.I)
REGISTER_BASE = "https://www.baseball-reference.com/register/player.fcgi?id={id}"

# Chadwick Register currently stores people in 16 files: people-0.csv through people-f.csv.
CHADWICK_BRANCHES = ["master", "main"]
CHADWICK_SUFFIXES = list("0123456789abcdef")

HEADERS = {
    "User-Agent": (
        "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) "
        "AppleWebKit/537.36 (KHTML, like Gecko) Chrome/123.0 Safari/537.36"
    ),
    "Accept-Language": "en-US,en;q=0.9",
}

OUTPUT_COLS = [
    "Player", "Best_Rank", "Best_Rank_Year", "Latest_Pipeline_Year", "Latest_Position", "Latest_Team",
    "Acquisition_Type", "Acquisition_Confidence", "Acquisition_Reason",
    "BRef_Register_ID", "BRef_Register_URL", "Birth_Year", "Birth_Country", "Birth_State",
    "Year", "Age", "Source", "Games", "Teams", "Leagues", "Levels", "Table_Index",
]


def ensure_dir(path: str | Path) -> Path:
    p = Path(path)
    p.mkdir(parents=True, exist_ok=True)
    return p


def project_path(path: str | Path) -> Path:
    p = Path(path)
    return p if p.is_absolute() else PROJECT_ROOT / p


def strip_accents(value: str) -> str:
    value = str(value or "")
    return "".join(c for c in unicodedata.normalize("NFKD", value) if not unicodedata.combining(c))


def clean_name(value: str) -> str:
    value = strip_accents(value).lower().strip()
    value = re.sub(r"\b(jr|sr|ii|iii|iv|v)\.?\b", "", value)
    value = re.sub(r"[^a-z0-9\s'-]", " ", value)
    value = re.sub(r"\s+", " ", value).strip()
    return value


def split_name(full_name: str) -> tuple[str, str]:
    cleaned = clean_name(full_name)
    parts = cleaned.split()
    if not parts:
        return "", ""
    if len(parts) == 1:
        return parts[0], ""
    return parts[0], parts[-1]


def safe_int(x) -> Optional[int]:
    if x is None or pd.isna(x):
        return None
    s = str(x).strip()
    if s == "" or s.lower() in {"nan", "none", "null", "--"}:
        return None
    m = re.search(r"-?\d+", s)
    if not m:
        return None
    try:
        return int(m.group())
    except Exception:
        return None


def read_csv_any(path: str | Path) -> pd.DataFrame:
    return pd.read_csv(project_path(path), encoding="utf-8-sig")


def normalize_colnames(df: pd.DataFrame) -> pd.DataFrame:
    out = df.copy()
    out.columns = [str(c).strip() for c in out.columns]
    return out


def player_pool_from_pipeline(df: pd.DataFrame) -> pd.DataFrame:
    df = normalize_colnames(df)
    required = {"Year", "Rank", "Player", "Position", "Team", "Age"}
    missing = sorted(required - set(df.columns))
    if missing:
        raise ValueError(f"Pipeline CSV missing required columns: {missing}")

    df = df.copy()
    df["Position"] = df["Position"].astype(str).str.strip()
    # regex=False warning-safe version would miss variants; keep regex but noncapturing pattern.
    pitcher_re = re.compile(r"(?:^|/|,|\s)(?:RHP|LHP|P)(?:\s|$|/|,)", re.I)
    df = df[~df["Position"].str.contains(pitcher_re, na=False)].copy()
    df["Name_Key"] = df["Player"].map(clean_name)
    df["Pipeline_Year"] = pd.to_numeric(df["Year"], errors="coerce")
    df["Pipeline_Rank"] = pd.to_numeric(df["Rank"], errors="coerce")
    df["Pipeline_Age"] = pd.to_numeric(df["Age"], errors="coerce")

    rows = []
    for _, g in df.groupby("Name_Key", dropna=False):
        g = g.sort_values(["Pipeline_Rank", "Pipeline_Year"], ascending=[True, False])
        best = g.iloc[0]
        latest = g.sort_values("Pipeline_Year", ascending=False).iloc[0]
        first, last = split_name(best["Player"])
        rows.append({
            "Player": best["Player"],
            "Name_Key": best["Name_Key"],
            "First_Name_Key": first,
            "Last_Name_Key": last,
            "Best_Rank": int(best["Pipeline_Rank"]) if pd.notna(best["Pipeline_Rank"]) else None,
            "Best_Rank_Year": int(best["Pipeline_Year"]) if pd.notna(best["Pipeline_Year"]) else None,
            "Latest_Pipeline_Year": int(latest["Pipeline_Year"]) if pd.notna(latest["Pipeline_Year"]) else None,
            "Latest_Position": latest["Position"],
            "Latest_Team": latest["Team"],
            "Latest_Level": latest.get("Level", ""),
            "Latest_Age": int(latest["Pipeline_Age"]) if pd.notna(latest["Pipeline_Age"]) else None,
            "Pipeline_Appearances": len(g),
            "All_Pipeline_Years": ";".join(map(str, sorted(g["Pipeline_Year"].dropna().astype(int).unique()))),
        })
    return pd.DataFrame(rows).sort_values(["Best_Rank", "Player"])


def chadwick_people_urls() -> list[str]:
    urls = []
    for branch in CHADWICK_BRANCHES:
        for suffix in CHADWICK_SUFFIXES:
            urls.append(f"https://raw.githubusercontent.com/chadwickbureau/register/{branch}/data/people-{suffix}.csv")
    return urls


def load_or_download_chadwick(cache_path: str | Path, force: bool = False, verbose: bool = False) -> pd.DataFrame:
    cache_path = project_path(cache_path)
    ensure_dir(cache_path.parent)
    if cache_path.exists() and not force:
        return pd.read_csv(cache_path, low_memory=False)

    chunks = []
    errors = []

    # Try master then main as complete sets. If one branch succeeds for all 16, use it.
    for branch in CHADWICK_BRANCHES:
        branch_chunks = []
        branch_ok = True
        for suffix in CHADWICK_SUFFIXES:
            url = f"https://raw.githubusercontent.com/chadwickbureau/register/{branch}/data/people-{suffix}.csv"
            try:
                if verbose:
                    print(f"Downloading Chadwick: {url}")
                r = requests.get(url, headers=HEADERS, timeout=60)
                r.raise_for_status()
                from io import BytesIO
                branch_chunks.append(pd.read_csv(BytesIO(r.content), low_memory=False))
            except Exception as e:
                errors.append(f"{url}: {e}")
                branch_ok = False
                break
        if branch_ok and branch_chunks:
            chunks = branch_chunks
            break

    if not chunks:
        raise RuntimeError("Could not download Chadwick people split files. Last errors:\n" + "\n".join(errors[-6:]))

    chad = pd.concat(chunks, ignore_index=True)
    chad.to_csv(cache_path, index=False)
    return chad


def candidate_bref_minor_columns(df: pd.DataFrame) -> list[str]:
    cols = list(df.columns)
    lower = {c.lower(): c for c in cols}
    preferred = [
        "key_bbref_minors", "key_bref_minors", "key_bbref_minor", "key_bref_minor",
        "bbref_minors", "bref_minors", "key_milb", "key_minors",
    ]
    out = []
    for p in preferred:
        if p in lower:
            out.append(lower[p])
    for c in cols:
        lc = c.lower()
        if ("bbref" in lc or "bref" in lc) and ("minor" in lc or "register" in lc) and c not in out:
            out.append(c)
    return out


def chadwick_add_name_keys(chad: pd.DataFrame) -> pd.DataFrame:
    chad = chad.copy()
    for c in ["name_first", "name_last", "name_given", "name_full", "name", "birth_year", "birth_country", "birth_state"]:
        if c not in chad.columns:
            chad[c] = ""
    if chad["name_full"].fillna("").eq("").all():
        chad["name_full"] = (chad["name_first"].fillna("") + " " + chad["name_last"].fillna("")).str.strip()
    chad["Name_Key"] = chad["name_full"].map(clean_name)
    chad["First_Name_Key"] = chad["name_first"].map(clean_name)
    chad["Last_Name_Key"] = chad["name_last"].map(clean_name)
    return chad


def retry_after_seconds(response: requests.Response) -> int | None:
    """Parse Retry-After if Sports Reference sends it. Returns seconds or None."""
    value = response.headers.get("Retry-After")
    if not value:
        return None
    value = value.strip()
    if value.isdigit():
        return int(value)
    try:
        retry_dt = parsedate_to_datetime(value)
        if retry_dt.tzinfo is None:
            retry_dt = retry_dt.replace(tzinfo=timezone.utc)
        return max(0, int((retry_dt - datetime.now(timezone.utc)).total_seconds()))
    except Exception:
        return None


def get_cached_url(url: str, cache_file: str | Path, sleep: float = 22.0, timeout: int = 60, verbose: bool = False) -> tuple[int, str, str, bool]:
    """Fetch a URL slowly and cache it.

    Design choices for Sports Reference / Baseball-Reference:
    - cached pages do not sleep or hit the network
    - uncached pages always wait before requesting
    - 429 immediately raises and includes Retry-After when available
    - no retry loop: rerun later rather than hammering the site
    """
    cache_file = project_path(cache_file)
    ensure_dir(cache_file.parent)
    if cache_file.exists() and cache_file.stat().st_size > 0:
        return 200, cache_file.read_text(encoding="utf-8", errors="ignore"), url, True
    if sleep:
        time.sleep(float(sleep))
    r = requests.get(url, headers=HEADERS, timeout=timeout, allow_redirects=True)
    if verbose:
        print(f"  GET {url} -> {r.status_code} final={r.url}")
    if r.status_code == 429:
        ra = retry_after_seconds(r)
        if ra is not None:
            raise RuntimeError(f"Baseball-Reference returned 429 rate limit. Retry-After: {ra} seconds. Stop scraping and rerun later with cached progress.")
        raise RuntimeError("Baseball-Reference returned 429 rate limit. Stop scraping and rerun later with cached progress.")
    if r.status_code == 200 and r.text:
        cache_file.write_text(r.text, encoding="utf-8")
    return r.status_code, r.text, r.url, False


def read_html_tables_with_comments(html: str) -> list[pd.DataFrame]:
    chunks = [html]
    soup = BeautifulSoup(html, "lxml")
    for comment in soup.find_all(string=lambda text: isinstance(text, Comment)):
        txt = str(comment)
        if "<table" in txt:
            chunks.append(txt)
    tables = []
    for chunk in chunks:
        try:
            for t in pd.read_html(chunk):
                tables.append(t)
        except ValueError:
            continue
        except Exception:
            continue
    return tables
