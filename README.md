# Prospect Games Through Age 21 — Baseball-Reference compliant scraper

This version returns to Baseball-Reference Register pages and is designed to stay well under Sports Reference's public rate-limit guidance.

## What it does

1. Reads the MLB Pipeline 2017–2026 Top 100 CSV.
2. Removes pitchers.
3. Deduplicates repeated players.
4. Resolves Baseball-Reference Register IDs using the Chadwick Register.
5. Scrapes only direct Baseball-Reference Register pages.
6. Caches every downloaded page.
7. Stops immediately on HTTP 429 instead of continuing.
8. Counts college + summer + pro games through age 21.
9. Keeps `Acquisition_Type` separate from `Source`.
10. Builds Streamlit-ready summary tables.

## Setup

```bash
cd ~/Desktop/prospect_games_age21_bref_v10
python3 -m venv .venv
source .venv/bin/activate
pip install --upgrade pip
pip install -r requirements.txt
```

## First run: resolve players only

```bash
python scripts/01_prepare_player_pool.py \
  --pipeline-csv data/raw/milb_top_prospects_last_10_years_2017_2026.csv \
  --out data/interim/player_pool.csv

python scripts/02_resolve_bref_register_urls.py \
  --player-pool data/interim/player_pool.csv \
  --out data/interim/player_register_urls.csv \
  --verbose
```

## Small safe scrape test

This pulls at most 3 new Baseball-Reference pages at ~1 page every 25 seconds.

```bash
python scripts/03_scrape_games_through_age21.py \
  --register-urls data/interim/player_register_urls.csv \
  --limit 10 \
  --sleep 25 \
  --max-new-pages 3 \
  --verbose \
  --out data/processed/prospect_games_by_player_age.csv
```

Check output:

```bash
head -30 data/processed/prospect_games_by_player_age.csv
cat data/processed/scrape_audit.csv
cat data/processed/scrape_failures.csv
```

Build summaries:

```bash
python scripts/04_build_summary_tables.py \
  --games data/processed/prospect_games_by_player_age.csv \
  --out-dir data/processed
```

Open dashboard:

```bash
streamlit run streamlit_app.py
```

## Full scrape

Use this only after the small test works. This will take a long time by design.

```bash
python scripts/03_scrape_games_through_age21.py \
  --register-urls data/interim/player_register_urls.csv \
  --sleep 25 \
  --verbose \
  --out data/processed/prospect_games_by_player_age.csv

python scripts/04_build_summary_tables.py \
  --games data/processed/prospect_games_by_player_age.csv \
  --out-dir data/processed
```

## If you get 429

Stop. Do not rerun immediately. Wait at least 1–2 hours, preferably overnight. Cached pages remain in `data/cache/bref_register_pages/`, so reruns do not redownload pages already saved.


## v13 acquisition fix

Acquisition pathway classification now uses Baseball-Reference draft/signing/high-school bio context before birth country. This prevents foreign-born high-school draftees from being incorrectly labeled International. Birth country is fallback only.
