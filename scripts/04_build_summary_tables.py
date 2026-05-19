from __future__ import annotations

import argparse
from pathlib import Path

import pandas as pd

from common import ensure_dir, project_path

ACQ_ORDER = ["College", "High School", "International", "Unknown"]
AGE_ORDER = list(range(14, 22))


def main():
    ap = argparse.ArgumentParser(description="Build summary tables from processed games file.")
    ap.add_argument("--games", default="data/processed/prospect_games_by_player_age.csv")
    ap.add_argument("--out-dir", default="data/processed")
    args = ap.parse_args()

    games_path = project_path(args.games)
    out_dir = project_path(args.out_dir)
    ensure_dir(out_dir)
    if not games_path.exists():
        raise FileNotFoundError(games_path)
    df = pd.read_csv(games_path)
    if df.empty:
        print("No game rows found. Summary tables not built.")
        return
    df["Games"] = pd.to_numeric(df["Games"], errors="coerce").fillna(0).astype(int)
    df["Age"] = pd.to_numeric(df["Age"], errors="coerce").astype("Int64")

    total = df.groupby(["Age", "Acquisition_Type"], dropna=False)["Games"].sum().reset_index()
    total_pivot = total.pivot_table(index="Age", columns="Acquisition_Type", values="Games", aggfunc="sum", fill_value=0).reset_index()
    for c in ACQ_ORDER:
        if c not in total_pivot.columns:
            total_pivot[c] = 0
    total_pivot = total_pivot[["Age"] + ACQ_ORDER]
    total_pivot.to_csv(out_dir / "summary_total_games_by_age_acquisition.csv", index=False)

    player_age = df.groupby(["Player", "Age", "Acquisition_Type"], dropna=False)["Games"].sum().reset_index()
    avg = player_age.groupby(["Age", "Acquisition_Type"], dropna=False)["Games"].mean().reset_index()
    avg_pivot = avg.pivot_table(index="Age", columns="Acquisition_Type", values="Games", aggfunc="mean", fill_value=0).reset_index()
    for c in ACQ_ORDER:
        if c not in avg_pivot.columns:
            avg_pivot[c] = 0
    avg_pivot = avg_pivot[["Age"] + ACQ_ORDER]
    avg_pivot.to_csv(out_dir / "summary_avg_games_by_age_acquisition.csv", index=False)

    counts = player_age.groupby(["Age", "Acquisition_Type"], dropna=False)["Player"].nunique().reset_index(name="Players")
    counts_pivot = counts.pivot_table(index="Age", columns="Acquisition_Type", values="Players", aggfunc="sum", fill_value=0).reset_index()
    for c in ACQ_ORDER:
        if c not in counts_pivot.columns:
            counts_pivot[c] = 0
    counts_pivot = counts_pivot[["Age"] + ACQ_ORDER]
    counts_pivot.to_csv(out_dir / "summary_players_by_age_acquisition.csv", index=False)

    source = df.groupby(["Age", "Acquisition_Type", "Source"], dropna=False)["Games"].sum().reset_index()
    source.to_csv(out_dir / "summary_games_by_age_acquisition_source_long.csv", index=False)
    print(f"Wrote summaries to {out_dir.relative_to(project_path('.'))}")


if __name__ == "__main__":
    main()
