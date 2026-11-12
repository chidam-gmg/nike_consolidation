"""Build the review pack for the stock movement engine: a pre-filled CSV of every action that needs a person, plus an
HTML summary for the e-mail body.

  python3 build_review_pack.py [--run latest|<run_id>] [--pending-only] [--out DIR] [--force]

Writes <out>/nike_review_actions_<run_id>.csv, review_email.html and review_pack.json (paths, counts, subject).
Source: sports_analytics_test.nike_consolidation.agent_review_actions_for(run) — completed runs only. Refuses while the
job is running (its latest tables are half-replaced) unless --force."""
import argparse, html, json, pathlib, sys
from datetime import datetime, timezone, timedelta

sys.path.insert(0, str(pathlib.Path(__file__).resolve().parents[2] / "build" / "app"))
import databricks_io as dbio  # noqa: E402

FQ = "sports_analytics_test.nike_consolidation"
JOB_ID = 553755400746008
APP_URL = "https://nike-consolidation-8517174019523704.aws.databricksapps.com"
GST = timezone(timedelta(hours=4))
COLS = {"line_type": "Action", "review_reason": "Why review", "country": "Country", "category": "Category",
        "style_code": "Style", "style_name": "Style name", "article": "Article", "size": "Size", "from_site": "From",
        "from_name": "From name", "to_site": "To", "to_name": "To name", "qty": "Qty", "value_aed": "Value (AED)",
        "engine_reason": "Engine reason", "current_status": "Current status", "decision": "Decision (Approve / Reject)",
        "comment": "Comment", "line_key": "Line key (do not edit)", "run_id": "Run ID"}


def job_running() -> bool:
    runs = list(dbio.get_client().jobs.list_runs(job_id=JOB_ID, active_only=True, limit=1))
    return bool(runs)


def money(v):
    v = float(v or 0)
    return f"AED {v / 1e6:.1f}M" if v >= 1e6 else f"AED {v / 1e3:.0f}K" if v >= 1e3 else f"AED {v:,.0f}"


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--run", default="latest")
    ap.add_argument("--pending-only", action="store_true")
    ap.add_argument("--out", default=".")
    ap.add_argument("--force", action="store_true")
    a = ap.parse_args()
    if not a.force and job_running():
        print(json.dumps({"error": "The stock movement job is running right now; its results are not archived yet. "
                                   "Wait for it to finish (about 8 minutes) or pass --run with an earlier run ID."}))
        sys.exit(2)

    df = dbio.run_sql(f"SELECT * FROM {FQ}.agent_review_actions_for('{a.run.replace(chr(39), '')}')")
    if df.empty:
        print(json.dumps({"error": f"No review actions found for run '{a.run}'."})); sys.exit(3)
    df["qty"] = df["qty"].astype(float).astype(int)
    df["value_aed"] = df["value_aed"].astype(float).round(0)
    if a.pending_only:
        df = df[df["current_status"] == "Pending"]
    df = df.sort_values(["line_type", "to_site", "value_aed"], ascending=[False, True, False])
    run_id, run_by, sales = df["run_id"].iloc[0], df["run_by"].iloc[0], df["sales_through"].iloc[0]

    out = pathlib.Path(a.out); out.mkdir(parents=True, exist_ok=True)
    csv_path = out / f"nike_review_actions_{run_id}.csv"
    df[list(COLS)].rename(columns=COLS).to_csv(csv_path, index=False, encoding="utf-8-sig")

    pend = df[df["current_status"] == "Pending"]
    by_type = df.groupby("line_type").agg(lines=("line_key", "size"), units=("qty", "sum"), value=("value_aed", "sum")).reset_index()
    reasons = (df.assign(r=df["review_reason"].str.split(" · ")).explode("r").groupby("r").size().sort_values(ascending=False))
    stores = (df.groupby(["to_site", "to_name"], dropna=False).agg(lines=("line_key", "size"), units=("qty", "sum"))
                .reset_index().sort_values("lines", ascending=False).head(10))
    e = lambda x: html.escape("" if x is None or (isinstance(x, float) and x != x) else str(x))
    rows_t = "".join(f"<tr><td>{e(r.line_type)}</td><td>{r.lines:,}</td><td>{int(r.units):,}</td><td>{money(r.value)}</td></tr>"
                     for r in by_type.itertuples())
    rows_r = "".join(f"<li>{e(k)}: {v:,} lines</li>" for k, v in reasons.items())
    rows_s = "".join(f"<tr><td>{e(r.to_site)}</td><td>{e(r.to_name)}</td><td>{r.lines:,}</td><td>{int(r.units):,}</td></tr>"
                     for r in stores.itertuples())
    who = "the scheduled 10:30 run" if run_by == "scheduled" else f"a run by {e(run_by)}"
    body = f"""<p>Hello,</p>
<p><b>{len(pend):,} actions from the Nike stock movement engine need your review</b> ({len(df):,} in the file, from {who}, run {e(run_id)}, sales through {e(sales)}). Everything else in the run is routine and approved automatically.</p>
<table><tr><th>Action</th><th>Lines</th><th>Units</th><th>Value</th></tr>{rows_t}</table>
<p>Why they need a person:</p><ul>{rows_r}</ul>
<p>Receiving stores with the most lines to review:</p>
<table><tr><th>Store</th><th>Name</th><th>Lines</th><th>Units</th></tr>{rows_s}</table>
<p><b>How to review:</b> open the CSV, fill the column <b>Decision (Approve / Reject)</b> on each line and add a comment where useful. Keep the column <b>Line key</b> unchanged so the decisions can be loaded back. You can also decide directly in the app: <a href="{APP_URL}">Stock movement app</a>, tab Store consolidation, Approvals.</p>
%%CSV_LINK%%
<p>Store moves: value is cost value. DC lines: value is retail value. Full-size top-ups are DC units sent only so a store can display a full size run.</p>"""
    (out / "review_email.html").write_text(body)
    meta = {"run_id": run_id, "run_by": run_by, "sales_through": str(sales), "csv": str(csv_path.resolve()),
            "csv_bytes": csv_path.stat().st_size, "html": str((out / "review_email.html").resolve()),
            "lines": int(len(df)), "pending": int(len(pend)),
            "by_type": {r.line_type: {"lines": int(r.lines), "units": int(r.units), "value_aed": float(r.value)} for r in by_type.itertuples()},
            "subject": f"Nike stock movement: {len(pend):,} actions need your review (run {run_id})",
            "built_at_gst": datetime.now(GST).strftime("%Y-%m-%d %H:%M")}
    (out / "review_pack.json").write_text(json.dumps(meta, indent=1))
    print(json.dumps(meta, indent=1))


if __name__ == "__main__":
    main()
