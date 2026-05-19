from __future__ import annotations

import argparse
from pathlib import Path

import pandas as pd

from common import (
    REGISTER_BASE,
    candidate_bref_minor_columns,
    chadwick_add_name_keys,
    ensure_dir,
    load_or_download_chadwick,
    project_path,
    safe_int,
)


def first_nonempty(row: pd.Series, columns: list[str]) -> str:
    for c in columns:
        if c in row and pd.notna(row[c]) and str(row[c]).strip() not in {"", "nan", "None", "<NA>"}:
            return str(row[c]).strip()
    return ""


def pick_best_match(player_row: pd.Series, chad: pd.DataFrame, minor_cols: list[str]) -> tuple[pd.Series | None, str, str]:
    name_key = str(player_row.get("Name_Key", ""))
    first = str(player_row.get("First_Name_Key", ""))
    last = str(player_row.get("Last_Name_Key", ""))
    latest_year = safe_int(player_row.get("Latest_Pipeline_Year"))
    latest_age = safe_int(player_row.get("Latest_Age"))
    approx_birth_year = latest_year - latest_age if latest_year and latest_age else None

    attempts = []
    candidates = chad[chad["Name_Key"].eq(name_key)].copy()
    attempts.append(("exact_full_name", len(candidates)))

    if candidates.empty and first and last:
        candidates = chad[(chad["First_Name_Key"].eq(first)) & (chad["Last_Name_Key"].eq(last))].copy()
        attempts.append(("exact_first_last", len(candidates)))

    if candidates.empty and first and last:
        candidates = chad[(chad["Last_Name_Key"].eq(last)) & (chad["First_Name_Key"].str.startswith(first[:4], na=False))].copy()
        attempts.append(("last_exact_first_prefix4", len(candidates)))

    if candidates.empty and first and last:
        candidates = chad[(chad["Last_Name_Key"].eq(last)) & (chad["First_Name_Key"].str.startswith(first[:3], na=False))].copy()
        attempts.append(("last_exact_first_prefix3", len(candidates)))

    if candidates.empty:
        return None, "unresolved", "No Chadwick match. Attempts: " + "; ".join(f"{a}={n}" for a, n in attempts)

    candidates["_minor_id"] = candidates.apply(lambda r: first_nonempty(r, minor_cols), axis=1)
    with_minor = candidates[candidates["_minor_id"].ne("")].copy()
    if not with_minor.empty:
        candidates = with_minor

    candidates["_birth_year_num"] = pd.to_numeric(candidates.get("birth_year"), errors="coerce")
    if approx_birth_year:
        candidates["_birth_dist"] = (candidates["_birth_year_num"] - approx_birth_year).abs()
    else:
        candidates["_birth_dist"] = 999

    # Prefer: has minor ID, plausible birth year, pro/college playing years near relevant period.
    for c in ["pro_played_first", "pro_played_last", "col_played_first", "col_played_last"]:
        if c not in candidates.columns:
            candidates[c] = pd.NA
        candidates[c + "_num"] = pd.to_numeric(candidates[c], errors="coerce")

    candidates["_has_minor"] = candidates["_minor_id"].ne("").astype(int)
    candidates["_played_recent"] = (
        candidates["pro_played_last_num"].fillna(0).ge(2010) |
        candidates["col_played_last_num"].fillna(0).ge(2010)
    ).astype(int)
    candidates = candidates.sort_values(["_has_minor", "_birth_dist", "_played_recent"], ascending=[False, True, False])
    best = candidates.iloc[0]
    minor_id = str(best.get("_minor_id", "")).strip()
    method = "chadwick_name_match_with_minor_id" if minor_id else "chadwick_name_match_no_minor_id"
    reason = (
        f"matches={len(candidates)}; selected_birth_year={best.get('birth_year','')}; "
        f"approx_birth_year={approx_birth_year}; minor_id={minor_id}; attempts=" + "; ".join(f"{a}={n}" for a, n in attempts)
    )
    return best, method, reason


def main():
    ap = argparse.ArgumentParser(description="Resolve player pool to Baseball-Reference Register URLs using Chadwick Register.")
    ap.add_argument("--player-pool", default="data/interim/player_pool.csv")
    ap.add_argument("--out", default="data/interim/player_register_urls.csv")
    ap.add_argument("--audit", default="data/interim/resolver_audit.csv")
    ap.add_argument("--chadwick-cache", default="data/interim/chadwick_people_combined.csv")
    ap.add_argument("--force-download-chadwick", action="store_true")
    ap.add_argument("--verbose", action="store_true")
    args = ap.parse_args()

    pool = pd.read_csv(project_path(args.player_pool))
    chad = load_or_download_chadwick(args.chadwick_cache, force=args.force_download_chadwick, verbose=args.verbose)
    chad = chadwick_add_name_keys(chad)
    minor_cols = candidate_bref_minor_columns(chad)
    if not minor_cols:
        raise RuntimeError(f"No Baseball-Reference minor/register ID columns found in Chadwick columns: {list(chad.columns)[:50]}")

    rows = []
    audit = []
    for _, p in pool.iterrows():
        best, method, reason = pick_best_match(p, chad, minor_cols)
        base = p.to_dict()
        if best is None:
            out = {**base, "BRef_Register_ID": "", "BRef_Register_URL": "", "Resolve_Status": "unresolved", "Resolve_Method": method, "Resolve_Reason": reason, "Birth_Year": "", "Birth_Country": "", "Birth_State": ""}
        else:
            rid = first_nonempty(best, minor_cols)
            url = REGISTER_BASE.format(id=rid) if rid else ""
            out = {
                **base,
                "BRef_Register_ID": rid,
                "BRef_Register_URL": url,
                "Resolve_Status": "resolved" if rid else "matched_no_register_id",
                "Resolve_Method": method,
                "Resolve_Reason": reason,
                "Birth_Year": best.get("birth_year", ""),
                "Birth_Country": best.get("birth_country", ""),
                "Birth_State": best.get("birth_state", ""),
                "Chadwick_Name": best.get("name_full", ""),
                "Chadwick_Key": best.get("key_person", ""),
            }
        rows.append(out)
        audit.append({k: out.get(k, "") for k in ["Player", "Resolve_Status", "Resolve_Method", "Resolve_Reason", "BRef_Register_ID", "BRef_Register_URL", "Birth_Year", "Birth_Country", "Birth_State", "Chadwick_Name"]})

    out_df = pd.DataFrame(rows)
    audit_df = pd.DataFrame(audit)
    out = project_path(args.out)
    audit_path = project_path(args.audit)
    ensure_dir(out.parent)
    out_df.to_csv(out, index=False)
    audit_df.to_csv(audit_path, index=False)
    print(f"Player pool: {len(pool)}")
    print(f"Resolved URLs: {out_df['BRef_Register_URL'].fillna('').astype(str).str.len().gt(0).sum()}")
    print(f"Wrote: {out.relative_to(project_path('.'))}")
    print(f"Audit: {audit_path.relative_to(project_path('.'))}")


if __name__ == "__main__":
    main()
