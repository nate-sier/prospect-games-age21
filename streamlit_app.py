import streamlit as st
import pandas as pd
from pathlib import Path

st.set_page_config(
    page_title="Prospect Games Through Age 21",
    page_icon="⚾",
    layout="wide",
)

DATA_PATH = Path("data/processed/prospect_games_by_player_age.csv")

st.markdown(
    """
    <style>
        .block-container {padding-top: 2rem; padding-bottom: 2rem; max-width: 1300px;}
        h1, h2, h3 {color: #172033;}
        .small-note {color: #667085; font-size: 0.92rem; margin-top: -0.5rem;}
        div[data-testid="stMetricValue"] {font-size: 1.55rem;}
        .stDataFrame {border: 1px solid #e5e7eb; border-radius: 10px;}
        .section-note {color: #667085; font-size: 0.9rem; margin-bottom: 0.75rem;}
    </style>
    """,
    unsafe_allow_html=True,
)

st.title("MLB Pipeline Top Prospects: Games Through Age 21")
st.markdown(
    "<div class='small-note'>Comparison-only dashboard. Counts college + summer + professional games by player age and acquisition type.</div>",
    unsafe_allow_html=True,
)

REQUIRED_COLS = {"Player", "Age", "Acquisition_Type", "Source", "Games"}
ACQ_ORDER = ["College", "High School", "International", "Unknown"]
AGE_ORDER = list(range(16, 22))

@st.cache_data(show_spinner=False)
def load_data(path: Path) -> pd.DataFrame:
    if not path.exists():
        return pd.DataFrame()
    df = pd.read_csv(path)
    df.columns = [str(c).strip() for c in df.columns]
    return df


def clean_games_df(df: pd.DataFrame) -> pd.DataFrame:
    if df.empty:
        return df

    out = df.copy()
    for col in ["Player", "Acquisition_Type", "Source"]:
        if col in out.columns:
            out[col] = out[col].astype(str).str.strip()

    out["Age"] = pd.to_numeric(out["Age"], errors="coerce")
    out["Games"] = pd.to_numeric(out["Games"], errors="coerce")

    out = out[out["Age"].between(16, 21, inclusive="both")]
    out = out[out["Games"].notna()]
    out = out[out["Games"] >= 0]

    out["Source"] = out["Source"].replace({"nan": "Unknown", "None": "Unknown", "": "Unknown"})
    out["Acquisition_Type"] = out["Acquisition_Type"].replace({"nan": "Unknown", "None": "Unknown", "": "Unknown"})

    out["Acquisition_Type"] = out["Acquisition_Type"].replace({
        "HS": "High School",
        "HighSchool": "High School",
        "High school": "High School",
        "Intl": "International",
    })
    out["Source"] = out["Source"].replace({
        "NCAA": "College",
        "Smr": "Summer",
        "summer": "Summer",
        "pro": "Pro",
    })

    out["Age"] = out["Age"].astype(int)
    out["Games"] = out["Games"].astype(float)
    return out


def build_player_age_totals(df: pd.DataFrame) -> pd.DataFrame:
    # One row per player-age-acquisition bucket. This is the unit for the distribution tables.
    group_cols = ["Player", "Age", "Acquisition_Type"]
    keep_cols = [
        c for c in ["Pipeline_Year", "Pipeline_Rank", "Pipeline_Position", "Pipeline_Team"]
        if c in df.columns
    ]

    base = (
        df.groupby(group_cols, dropna=False)
          .agg(
              Total_Games=("Games", "sum"),
              Sources=("Source", lambda x: ", ".join(sorted(set(map(str, x))))),
              Row_Count=("Games", "size"),
          )
          .reset_index()
    )

    if keep_cols:
        meta = df.groupby("Player", dropna=False)[keep_cols].first().reset_index()
        base = base.merge(meta, on="Player", how="left")

    return base


def distribution_for_acquisition(player_age: pd.DataFrame, acq: str) -> pd.DataFrame:
    sub = player_age[player_age["Acquisition_Type"] == acq].copy()
    if sub.empty:
        return pd.DataFrame({
            "Age": AGE_ORDER,
            "Players": [0] * len(AGE_ORDER),
            "Mean": [0.0] * len(AGE_ORDER),
            "Median": [0.0] * len(AGE_ORDER),
            "SD": [0.0] * len(AGE_ORDER),
            "Min": [0.0] * len(AGE_ORDER),
            "P25": [0.0] * len(AGE_ORDER),
            "P75": [0.0] * len(AGE_ORDER),
            "P90": [0.0] * len(AGE_ORDER),
            "Total_Games": [0] * len(AGE_ORDER),
        })

    dist = (
        sub.groupby("Age", dropna=False)["Total_Games"]
        .agg(
            Players="count",
            Mean="mean",
            Median="median",
            SD="std",
            Min="min",
            P25=lambda s: s.quantile(0.25),
            P75=lambda s: s.quantile(0.75),
            P90="max",
            Total_Games="sum",
        )
        .reindex(AGE_ORDER)
        .reset_index()
    )

    for c in ["Players", "Total_Games"]:
        dist[c] = dist[c].fillna(0).astype(int)
    for c in ["Mean", "Median", "SD", "Min", "P25", "P75", "P90"]:
        dist[c] = dist[c].fillna(0).round(1)

    return dist


def build_total_matrix(player_age: pd.DataFrame) -> pd.DataFrame:
    if player_age.empty:
        return pd.DataFrame()
    mat = (
        player_age.pivot_table(
            index="Age",
            columns="Acquisition_Type",
            values="Total_Games",
            aggfunc="sum",
            fill_value=0,
        )
        .reset_index()
    )
    for col in ACQ_ORDER:
        if col not in mat.columns:
            mat[col] = 0
    mat = mat[["Age"] + ACQ_ORDER]
    for col in mat.columns:
        if col != "Age":
            mat[col] = mat[col].astype(int)
    return mat


def build_source_split(df: pd.DataFrame) -> pd.DataFrame:
    if df.empty:
        return pd.DataFrame()
    split = (
        df.groupby(["Age", "Acquisition_Type", "Source"], dropna=False)
          .agg(Games=("Games", "sum"), Rows=("Games", "size"), Players=("Player", "nunique"))
          .reset_index()
          .sort_values(["Age", "Acquisition_Type", "Source"])
    )
    split["Games"] = split["Games"].astype(int)
    return split


def source_split_for_acquisition(source_split: pd.DataFrame, acq: str) -> pd.DataFrame:
    sub = source_split[source_split["Acquisition_Type"] == acq].copy()
    if sub.empty:
        return pd.DataFrame(columns=["Age", "Source", "Games", "Rows", "Players"])
    return sub[["Age", "Source", "Games", "Rows", "Players"]].sort_values(["Age", "Source"])


def format_distribution_table(df: pd.DataFrame) -> pd.DataFrame:
    cols = ["Age", "Players", "Mean", "Median", "SD", "Min", "P25", "P75", "P90", "Total_Games"]
    return df[cols]


def show_distribution_table(acq: str, player_age: pd.DataFrame, source_split: pd.DataFrame):
    sub_players = player_age[player_age["Acquisition_Type"] == acq]
    total_players = sub_players["Player"].nunique()
    total_games = int(sub_players["Total_Games"].sum()) if not sub_players.empty else 0
    player_age_obs = len(sub_players)

    st.markdown(f"### {acq}")
    m1, m2, m3 = st.columns(3)
    m1.metric("Players", f"{total_players:,}")
    m2.metric("Player-age observations", f"{player_age_obs:,}")
    m3.metric("Total games", f"{total_games:,}")

    dist = distribution_for_acquisition(player_age, acq)
    st.dataframe(
        format_distribution_table(dist),
        use_container_width=True,
        hide_index=True,
        column_config={
            "Age": st.column_config.NumberColumn("Age", format="%d"),
            "Players": st.column_config.NumberColumn("Players", format="%d"),
            "Mean": st.column_config.NumberColumn("Mean", format="%.1f"),
            "Median": st.column_config.NumberColumn("Median", format="%.1f"),
            "SD": st.column_config.NumberColumn("SD", format="%.1f"),
            "Min": st.column_config.NumberColumn("Min", format="%.1f"),
            "P25": st.column_config.NumberColumn("P25", format="%.1f"),
            "P75": st.column_config.NumberColumn("P75", format="%.1f"),
            "P90": st.column_config.NumberColumn("P90", format="%.1f"),
            "Total_Games": st.column_config.NumberColumn("Total Games", format="%d"),
        },
    )

    with st.expander(f"Show {acq} source split by age", expanded=False):
        st.caption("Source tells where the games came from. Smr/summer rows should appear as Summer, not Pro.")
        st.dataframe(source_split_for_acquisition(source_split, acq), use_container_width=True, hide_index=True)

    st.download_button(
        f"Download {acq.lower().replace(' ', '_')}_distribution.csv",
        data=dist.to_csv(index=False).encode("utf-8"),
        file_name=f"{acq.lower().replace(' ', '_')}_distribution_by_age.csv",
        mime="text/csv",
        key=f"download_{acq}",
    )


raw = load_data(DATA_PATH)

if raw.empty:
    st.warning("No game data found yet. Expected file: data/processed/prospect_games_by_player_age.csv")
    st.stop()

missing = REQUIRED_COLS - set(raw.columns)
if missing:
    st.error(f"The game file is missing required columns: {sorted(missing)}")
    st.stop()

clean = clean_games_df(raw)

if clean.empty:
    st.warning("The game data file exists but has no usable rows after filtering ages 16-21.")
    st.stop()

player_age = build_player_age_totals(clean)
total_matrix = build_total_matrix(player_age)
source_split = build_source_split(clean)

# Header metrics
c1, c2, c3, c4 = st.columns(4)
c1.metric("Players", f"{clean['Player'].nunique():,}")
c2.metric("Player-age observations", f"{len(player_age):,}")
c3.metric("Total games", f"{int(clean['Games'].sum()):,}")
c4.metric("Rows in source file", f"{len(clean):,}")

st.divider()

st.subheader("Distribution of games played by acquisition type")
st.markdown(
    "<div class='section-note'>Each table summarizes player-level total games within an age bucket for one acquisition type. Total Games is the sum of all player games in that age bucket.</div>",
    unsafe_allow_html=True,
)

for acq in ["College", "High School", "International"]:
    show_distribution_table(acq, player_age, source_split)
    st.divider()

unknown = player_age[player_age["Acquisition_Type"] == "Unknown"]
if not unknown.empty:
    show_distribution_table("Unknown", player_age, source_split)
    st.divider()

st.subheader("Total games matrix")
st.caption("Same total-games view as before, shown as a compact matrix.")
st.dataframe(total_matrix, use_container_width=True, hide_index=True)

st.download_button(
    "Download total games matrix CSV",
    data=total_matrix.to_csv(index=False).encode("utf-8"),
    file_name="total_games_by_age_acquisition.csv",
    mime="text/csv",
)

st.subheader("Player-level audit")
st.caption("One row per player-age-acquisition bucket. Use this to spot suspicious player-age totals.")

acq_options = [a for a in ACQ_ORDER if a in player_age["Acquisition_Type"].unique()]
acq_filter = st.multiselect("Filter acquisition type", options=acq_options, default=acq_options)
age_filter = st.multiselect("Filter age", options=AGE_ORDER, default=AGE_ORDER)

player_view = player_age[
    player_age["Acquisition_Type"].isin(acq_filter) & player_age["Age"].isin(age_filter)
].sort_values(["Acquisition_Type", "Age", "Total_Games"], ascending=[True, True, False])

st.dataframe(player_view, use_container_width=True, hide_index=True)

st.download_button(
    "Download player audit CSV",
    data=player_view.to_csv(index=False).encode("utf-8"),
    file_name="player_age_games_audit.csv",
    mime="text/csv",
)
