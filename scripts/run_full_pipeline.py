from __future__ import annotations

import argparse
import subprocess
import sys
from pathlib import Path

PROJECT_ROOT = Path(__file__).resolve().parents[1]


def run(cmd):
    print("\n$ " + " ".join(cmd))
    subprocess.check_call(cmd, cwd=PROJECT_ROOT)


def main():
    ap = argparse.ArgumentParser(description="Run full prospect age-21 games pipeline.")
    ap.add_argument("--pipeline-csv", default="data/raw/milb_top_prospects_last_10_years_2017_2026.csv")
    ap.add_argument("--limit", type=int, default=None)
    ap.add_argument("--sleep", type=float, default=22.0, help="Seconds between uncached Baseball-Reference page requests.")
    ap.add_argument("--max-new-pages", type=int, default=None, help="Optional cap on uncached Baseball-Reference pages for this run.")
    ap.add_argument("--verbose", action="store_true")
    ap.add_argument("--force-download-chadwick", action="store_true")
    args = ap.parse_args()

    py = sys.executable
    prep = [py, "scripts/01_prepare_player_pool.py", "--pipeline-csv", args.pipeline_csv, "--out", "data/interim/player_pool.csv"]
    run(prep)

    resolve = [py, "scripts/02_resolve_bref_register_urls.py", "--player-pool", "data/interim/player_pool.csv", "--out", "data/interim/player_register_urls.csv"]
    if args.verbose:
        resolve.append("--verbose")
    if args.force_download_chadwick:
        resolve.append("--force-download-chadwick")
    run(resolve)

    scrape = [py, "scripts/03_scrape_games_through_age21.py", "--register-urls", "data/interim/player_register_urls.csv", "--sleep", str(args.sleep), "--out", "data/processed/prospect_games_by_player_age.csv"]
    if args.limit:
        scrape += ["--limit", str(args.limit)]
    if args.max_new_pages is not None:
        scrape += ["--max-new-pages", str(args.max_new_pages)]
    if args.verbose:
        scrape.append("--verbose")
    run(scrape)

    run([py, "scripts/04_build_summary_tables.py", "--games", "data/processed/prospect_games_by_player_age.csv", "--out-dir", "data/processed"])


if __name__ == "__main__":
    main()
