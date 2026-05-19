from __future__ import annotations

import argparse
from pathlib import Path

from common import ensure_dir, player_pool_from_pipeline, read_csv_any, project_path


def main():
    ap = argparse.ArgumentParser(description="Prepare deduped MLB Pipeline position-player pool.")
    ap.add_argument("--pipeline-csv", default="data/raw/milb_top_prospects_last_10_years_2017_2026.csv")
    ap.add_argument("--out", default="data/interim/player_pool.csv")
    args = ap.parse_args()

    df = read_csv_any(args.pipeline_csv)
    pool = player_pool_from_pipeline(df)
    out = project_path(args.out)
    ensure_dir(out.parent)
    pool.to_csv(out, index=False)
    print(f"Input rows: {len(df)}")
    print(f"Position-player unique players: {len(pool)}")
    print(f"Wrote: {out.relative_to(project_path('.'))}")


if __name__ == "__main__":
    main()
