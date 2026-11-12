"""Data access for the Stock Movement workbench (role pages: Allocator, Line manager, Executive).

Three movement types, always in this order:
  FIRST_ALLOCATION  DC -> store, first trip of a new article (opening pack; nl_pack_country / nl_opening_pack)
  DC_TRANSFER       DC -> store replenishment (replen_recommendations)
  IBT               store -> store (recommendations)
A transfer is one article x size from one site to another; "transfers" is the count, "qty" the units.
Decisions live in wb_decisions (one row per run x movement type x line key, latest wins); the transfers the
allocator hands to SAP live in wb_sap_sends. Reads/writes go through databricks_io.run_sql (runs as the viewer).
"""
from __future__ import annotations

import uuid
import pandas as pd

import databricks_io as dbio
from databricks_io import FQ, run_sql, REVIEW_MIN_QTY, REVIEW_MIN_AED

DEC_FQ = f"{FQ}.wb_decisions"
SEND_FQ = f"{FQ}.wb_sap_sends"
TOLERANCE_QTY = 1          # allocator changes within +-1 qty go straight to SAP (proposed; confirm with the business)
CUTOFF = "13:30"           # SAP cut-off for next-day DC pick (proposed)
MANAGER_BY = "13:00"

TYPES = ["FIRST_ALLOCATION", "DC_TRANSFER", "IBT"]
TYPE_LABEL = {"FIRST_ALLOCATION": "First allocation", "DC_TRANSFER": "DC transfer", "IBT": "IBT"}

# opening-pack settings (pipeline/newlaunch/nl_pack.py)
PACK_SETTINGS = [
    ("Pack covers the first", "3", "weeks", "Until the first DC transfer lands (COVER_WEEKS)"),
    ("Chance the pack covers demand", "80", "%", "Per store and size (PACK_SERVICE)"),
    ("Kept at the DC, at least", "40", "% of buy", "Feeds DC transfers once the article sells (RESERVE_SHARE)"),
    ("Store gets the article if it sells", "1", "% +", "Of the country's division x gender units (MIN_STORE_SHARE)"),
]


def _lit(x) -> str:
    if x is None or (isinstance(x, float) and x != x) or str(x) in ("nan", "None", "<NA>"):
        return "NULL"
    return "'" + str(x).replace("'", "''") + "'"


def _num(x) -> str:
    try:
        return "NULL" if x is None or pd.isna(x) else str(int(round(float(x))))
    except Exception:
        return "NULL"


def _nums(df: pd.DataFrame, cols) -> pd.DataFrame:
    for c in cols:
        if c in df.columns:
            df[c] = pd.to_numeric(df[c], errors="coerce")
    return df


def _ibt_src(run_id: str) -> str:
    return dbio._src("recommendations", run_id)


def _dc_src(run_id: str) -> str:
    return dbio._src("replen_recommendations", run_id)


_IBT_REVIEW = f"(inventory_cost_aed >= {float(REVIEW_MIN_AED)} OR recommended_qty >= {int(REVIEW_MIN_QTY)})"
_DC_REVIEW = (f"(decision IS NOT NULL OR recommended_qty >= {int(REVIEW_MIN_QTY)} "
              f"OR recommended_qty * unit_price_aed >= {float(REVIEW_MIN_AED)})")


# ---------- run + scope ----------
def latest_run() -> dict:
    """Latest full-scope run (all countries and categories) from run_stats_history."""
    df = run_sql(f"""SELECT run_id, run_ts, as_of_date, triggered_by FROM {FQ}.run_stats_history
                     WHERE COALESCE(country_scope, 'ALL') = 'ALL' AND COALESCE(category_scope, 'ALL') = 'ALL'
                     ORDER BY run_ts DESC LIMIT 1""")
    return df.iloc[0].to_dict() if not df.empty else {}


def store_scope() -> dict:
    """Stores the engine runs on (cfg_store_grade, the authority) vs the 71-store baseline."""
    df = run_sql(f"""
        SELECT b.country, b.store_code, b.store_name, b.grade, c.store_code IS NOT NULL AS in_run
        FROM {FQ}.cfg_store_grade_baseline b LEFT JOIN {FQ}.cfg_store_grade c USING (store_code)
        UNION ALL
        SELECT c.country, c.store_code, c.store_name, c.grade, TRUE
        FROM {FQ}.cfg_store_grade c LEFT ANTI JOIN {FQ}.cfg_store_grade_baseline b USING (store_code)""")
    df["in_run"] = df["in_run"].astype(str).str.lower().eq("true")
    by = df.groupby("country").agg(baseline=("store_code", "size"), in_run=("in_run", "sum")).reset_index()
    return {"stores": df, "by_country": by, "in_run": int(df["in_run"].sum()), "baseline": len(df),
            "countries": sorted(df.loc[df["in_run"], "country"].unique().tolist())}


def store_names() -> dict:
    df = run_sql(f"SELECT store_code, store_name FROM {FQ}.cfg_store_grade_baseline")
    return dict(zip(df["store_code"].astype(str), df["store_name"].astype(str)))


# ---------- today: totals per movement type ----------
def type_totals(run_id: str) -> pd.DataFrame:
    """Per movement type x country (country NULL = all countries): SKUs (child articles) and qty, total and needing a person."""
    df = run_sql(f"""
        WITH u AS (
          SELECT 'IBT' AS movement_type, country, article, recommended_qty AS qty, {_IBT_REVIEW} AS rv FROM {_ibt_src(run_id)}
          UNION ALL
          SELECT 'DC_TRANSFER', country, article, recommended_qty, {_DC_REVIEW} FROM {_dc_src(run_id)} WHERE recommended_qty >= 1)
        SELECT movement_type, country, COUNT(*) AS transfers, COUNT(DISTINCT article) AS skus, SUM(qty) AS qty,
               COUNT_IF(rv) AS review_transfers, COUNT(DISTINCT IF(rv, article, NULL)) AS review_skus,
               SUM(IF(rv, qty, 0)) AS review_qty
        FROM u GROUP BY GROUPING SETS ((movement_type, country), (movement_type))""")
    return _nums(df, ["transfers", "skus", "qty", "review_transfers", "review_skus", "review_qty"])


def decision_counts(run_id: str) -> pd.DataFrame:
    df = run_sql(f"""
        SELECT movement_type, status, mgr_decision, COUNT(*) AS n, SUM(qty) AS qty, SUM(proposed_qty) AS proposed
        FROM {DEC_FQ} WHERE run_id = {_lit(run_id)} GROUP BY ALL""")
    return _nums(df, ["n", "qty", "proposed"])


# ---------- review queue (transfers that need the allocator) ----------
def review_queue(run_id: str, movement_type: str) -> pd.DataFrame:
    rid = _lit(run_id)
    if movement_type == "IBT":
        body = f"""
          SELECT 'IBT' AS movement_type, country, CONCAT(donor_store, '|', receiver_store, '|', article) AS line_key,
                 parent_article, COALESCE(generic_article_name, article_name) AS name, article, size,
                 donor_store AS from_site, receiver_store AS to_site, CAST(recommended_qty AS INT) AS proposed_qty,
                 ROUND(inventory_cost_aed, 0) AS value_aed,
                 CONCAT_WS(' · ', CASE WHEN inventory_cost_aed >= {float(REVIEW_MIN_AED)} THEN 'High value' END,
                                  CASE WHEN recommended_qty >= {int(REVIEW_MIN_QTY)} THEN 'Large qty' END) AS review_reason,
                 CONCAT_WS(' · ', movement_note,
                           CONCAT('giver cover ', ROUND(donor_cover_days, 0), 'd → receiver cover ', ROUND(receiver_cover_days, 0), 'd'),
                           CONCAT('receiver run ', receiver_run_before, ' → ', receiver_run_after, ' sizes'),
                           receiver_size_status_post_move) AS why
          FROM {_ibt_src(run_id)} WHERE {_IBT_REVIEW}"""
    else:
        body = f"""
          SELECT 'DC_TRANSFER' AS movement_type, country, CONCAT(source, '|', receiver_store, '|', article) AS line_key,
                 parent_article, generic_article_name AS name, article, size, source AS from_site,
                 receiver_store AS to_site, CAST(recommended_qty AS INT) AS proposed_qty,
                 ROUND(recommended_qty * unit_price_aed, 0) AS value_aed,
                 CONCAT_WS(' · ', CASE WHEN decision IS NOT NULL THEN 'Full-size top-up' END,
                                  CASE WHEN recommended_qty * unit_price_aed >= {float(REVIEW_MIN_AED)} THEN 'High value' END,
                                  CASE WHEN recommended_qty >= {int(REVIEW_MIN_QTY)} THEN 'Large qty' END) AS review_reason,
                 CONCAT_WS(' · ', reason, CONCAT('store has ', CAST(store_atp AS INT), ', needs ', CAST(store_need AS INT)),
                           CONCAT('DC has ', CAST(source_available AS INT)),
                           CONCAT(ROUND(demand_rate_day, 2), ' a day')) AS why
          FROM {_dc_src(run_id)} WHERE recommended_qty >= 1 AND {_DC_REVIEW}"""
    df = run_sql(f"""
        WITH u AS ({body})
        SELECT u.*, d.status, d.qty AS decided_qty, d.reason, d.decided_by, d.mgr_decision, d.mgr_note
        FROM u LEFT JOIN {DEC_FQ} d ON d.run_id = {rid} AND d.movement_type = u.movement_type AND d.line_key = u.line_key
        ORDER BY CASE WHEN d.status IS NULL OR d.mgr_decision = 'SENT_BACK' THEN 0 ELSE 1 END, value_aed DESC""")
    return _nums(df, ["proposed_qty", "value_aed", "decided_qty"])


def save_decisions(run_id: str, rows: pd.DataFrame, status: str, reason: str | None, user: str) -> int:
    """Allocator decisions for queue rows. status APPROVED | CHANGED | REJECTED; a CHANGED row within the
    tolerance becomes APPROVED (goes straight to SAP). A new allocator decision clears any manager decision."""
    if rows is None or rows.empty:
        return 0
    cols = ("run_id, movement_type, line_key, parent_article, article, size, from_site, to_site, proposed_qty, qty, "
            "value_aed, review_reason, status, reason, decided_by")
    n = 0
    for i in range(0, len(rows), 300):
        vals = []
        for _, r in rows.iloc[i:i + 300].iterrows():
            qty = r.get("new_qty", r.get("proposed_qty"))
            st = status
            if status == "REJECTED":
                qty = 0
            elif status == "CHANGED" and abs(float(qty) - float(r["proposed_qty"])) <= TOLERANCE_QTY:
                st = "APPROVED"
            elif status == "APPROVED":
                qty = r["proposed_qty"]          # an edited qty only goes through "Change" (tolerance / manager)
            vals.append(f"({_lit(run_id)}, {_lit(r['movement_type'])}, {_lit(r['line_key'])}, {_lit(r.get('parent_article'))}, "
                        f"{_lit(r.get('article'))}, {_lit(r.get('size'))}, {_lit(r.get('from_site'))}, {_lit(r.get('to_site'))}, "
                        f"{_num(r['proposed_qty'])}, {_num(qty)}, {_num(r.get('value_aed'))}, {_lit(r.get('review_reason'))}, "
                        f"{_lit(st)}, {_lit(reason)}, {_lit(user)})")
        run_sql(f"""
            MERGE INTO {DEC_FQ} t
            USING (SELECT * FROM VALUES {', '.join(vals)} AS v({cols})) s
            ON t.run_id = s.run_id AND t.movement_type = s.movement_type AND t.line_key = s.line_key
            WHEN MATCHED THEN UPDATE SET qty = CAST(s.qty AS INT), status = s.status, reason = s.reason,
                 decided_by = s.decided_by, decided_at = current_timestamp(),
                 mgr_decision = NULL, mgr_note = NULL, mgr_by = NULL, mgr_at = NULL
            WHEN NOT MATCHED THEN INSERT ({cols}, decided_at)
              VALUES (s.run_id, s.movement_type, s.line_key, s.parent_article, s.article, s.size, s.from_site, s.to_site,
                      CAST(s.proposed_qty AS INT), CAST(s.qty AS INT), CAST(s.value_aed AS DOUBLE), s.review_reason,
                      s.status, s.reason, s.decided_by, current_timestamp())""")
        n += min(300, len(rows) - i)
    return n


def undo_decisions(run_id: str, rows: pd.DataFrame) -> None:
    if rows is None or rows.empty:
        return
    keys = ", ".join(_lit(f"{r['movement_type']}#{r['line_key']}") for _, r in rows.iterrows())
    run_sql(f"DELETE FROM {DEC_FQ} WHERE run_id = {_lit(run_id)} AND CONCAT(movement_type, '#', line_key) IN ({keys}) "
            f"AND mgr_decision IS NULL")


def add_transfer(run_id: str, mt: str, from_site: str, to_site: str, article: str, size: str, qty: int, reason: str,
                 user: str) -> None:
    key = f"{from_site}|{to_site}|{article}|ADDED"
    run_sql(f"""INSERT INTO {DEC_FQ} (run_id, movement_type, line_key, article, size, from_site, to_site, proposed_qty, qty,
                review_reason, status, reason, decided_by, decided_at)
                VALUES ({_lit(run_id)}, {_lit(mt)}, {_lit(key)}, {_lit(article)}, {_lit(size)}, {_lit(from_site)},
                        {_lit(to_site)}, 0, {_num(qty)}, 'Added by allocator', 'ADDED', {_lit(reason)}, {_lit(user)},
                        current_timestamp())""")


# ---------- manager ----------
def manager_queue(run_id: str) -> pd.DataFrame:
    df = run_sql(f"""
        SELECT movement_type, line_key, parent_article, article, size, from_site, to_site, proposed_qty, qty, value_aed,
               review_reason, status, reason, decided_by, decided_at, mgr_decision, mgr_note, mgr_by
        FROM {DEC_FQ}
        WHERE run_id = {_lit(run_id)} AND status IN ('CHANGED', 'REJECTED', 'ADDED')
        ORDER BY CASE WHEN mgr_decision IS NULL THEN 0 ELSE 1 END,
                 CASE movement_type WHEN 'FIRST_ALLOCATION' THEN 0 WHEN 'DC_TRANSFER' THEN 1 ELSE 2 END, decided_at""")
    return _nums(df, ["proposed_qty", "qty", "value_aed"])


def save_manager(run_id: str, rows: pd.DataFrame, decision: str, note: str | None, user: str) -> int:
    """decision APPROVED (the allocator's change/rejection/addition stands) | KEPT (send the proposal) | SENT_BACK."""
    if rows is None or rows.empty:
        return 0
    keys = ", ".join(_lit(f"{r['movement_type']}#{r['line_key']}") for _, r in rows.iterrows())
    run_sql(f"""UPDATE {DEC_FQ} SET mgr_decision = {_lit(decision)}, mgr_note = {_lit(note)}, mgr_by = {_lit(user)},
                mgr_at = current_timestamp()
                WHERE run_id = {_lit(run_id)} AND CONCAT(movement_type, '#', line_key) IN ({keys})""")
    return len(rows)


def team_progress(run_id: str) -> pd.DataFrame:
    df = run_sql(f"""
        SELECT decided_by, COUNT(*) AS decided, COUNT_IF(status IN ('CHANGED', 'REJECTED', 'ADDED')) AS to_manager,
               MAX(decided_at) AS last_at
        FROM {DEC_FQ} WHERE run_id = {_lit(run_id)} AND movement_type <> 'FIRST_ALLOCATION' GROUP BY decided_by""")
    return _nums(df, ["decided", "to_manager"])


# ---------- send to SAP ----------
def _ready_sql(run_id: str, country: str | None = None) -> str:
    rid = _lit(run_id)
    cf = f"AND country = {_lit(country)}" if country else ""
    return f"""
        WITH u AS (
          SELECT 'IBT' AS mt, CONCAT(donor_store, '|', receiver_store, '|', article) AS line_key, parent_article, article,
                 size, donor_store AS from_site, receiver_store AS to_site, CAST(recommended_qty AS INT) AS qty,
                 {_IBT_REVIEW} AS needs_review
          FROM {_ibt_src(run_id)} WHERE 1 = 1 {cf}
          UNION ALL
          SELECT 'DC_TRANSFER', CONCAT(source, '|', receiver_store, '|', article), parent_article, article, size, source,
                 receiver_store, CAST(recommended_qty AS INT), {_DC_REVIEW}
          FROM {_dc_src(run_id)} WHERE recommended_qty >= 1 {cf}
          UNION ALL
          SELECT movement_type, line_key, parent_article, article, size, from_site, to_site, qty, TRUE
          FROM {DEC_FQ} WHERE run_id = {rid} AND status = 'ADDED'),
        d AS (SELECT * FROM {DEC_FQ} WHERE run_id = {rid}),
        r AS (
          SELECT u.mt, u.line_key, u.parent_article, u.article, u.size, u.from_site, u.to_site,
                 CASE WHEN NOT u.needs_review THEN u.qty
                      WHEN d.status = 'APPROVED' THEN d.qty
                      WHEN d.status IN ('CHANGED', 'ADDED') AND d.mgr_decision = 'APPROVED' THEN d.qty
                      WHEN d.status IN ('CHANGED', 'REJECTED') AND d.mgr_decision = 'KEPT' THEN d.proposed_qty END AS qty,
                 CASE WHEN NOT u.needs_review THEN 'Routine'
                      WHEN d.status = 'APPROVED' AND d.qty <> d.proposed_qty THEN 'Changed within tolerance'
                      WHEN d.status = 'APPROVED' THEN 'Approved'
                      WHEN d.status = 'ADDED' THEN 'Added, manager approved'
                      WHEN d.mgr_decision = 'APPROVED' THEN 'Change approved by manager'
                      ELSE 'Proposal kept by manager' END AS decision,
                 CASE WHEN NOT u.needs_review THEN 'guardrails' ELSE COALESCE(d.mgr_by, d.decided_by) END AS approved_by
          FROM u LEFT JOIN d ON d.movement_type = u.mt AND d.line_key = u.line_key)
        SELECT r.* FROM r
        WHERE r.qty > 0 AND NOT EXISTS (SELECT 1 FROM {SEND_FQ} s WHERE s.run_id = {rid} AND s.movement_type = r.mt
                                         AND s.line_key = r.line_key)"""


def ready_to_send(run_id: str, country: str | None = None) -> dict:
    df = run_sql(f"""SELECT COALESCE(mt, 'ALL') AS mt, COUNT(*) AS n, COUNT(DISTINCT article) AS skus, SUM(qty) AS qty
                     FROM ({_ready_sql(run_id, country)}) GROUP BY GROUPING SETS ((mt), ())""")
    df = _nums(df, ["n", "skus", "qty"])
    tot = df[df["mt"] == "ALL"]
    g = lambda c: int(tot[c].iloc[0] or 0) if len(tot) else 0
    return {"transfers": g("n"), "skus": g("skus"), "qty": g("qty"), "by_type": df[df["mt"] != "ALL"]}


def send_to_sap(run_id: str, user: str, country: str | None = None) -> dict:
    send_id = f"SEND-{run_id[:8]}-{uuid.uuid4().hex[:6]}"
    run_sql(f"""INSERT INTO {SEND_FQ}
                SELECT {_lit(send_id)}, {_lit(run_id)}, mt, line_key, parent_article, article, size, from_site, to_site,
                       qty, decision, approved_by, {_lit(user)}, current_timestamp()
                FROM ({_ready_sql(run_id, country)})""")
    df = _nums(run_sql(f"SELECT COUNT(*) AS n, SUM(qty) AS qty FROM {SEND_FQ} WHERE send_id = {_lit(send_id)}"), ["n", "qty"])
    return {"send_id": send_id, "transfers": int(df["n"].iloc[0] or 0), "qty": int(df["qty"].iloc[0] or 0)}


def sent_runs(limit: int = 20) -> pd.DataFrame:
    return run_sql(f"""SELECT run_id, MIN(sent_at) AS first_sent, COUNT(DISTINCT send_id) AS sends, COUNT(*) AS transfers,
                              SUM(qty) AS qty FROM {SEND_FQ} GROUP BY run_id ORDER BY first_sent DESC LIMIT {int(limit)}""")


def sends(run_id: str) -> pd.DataFrame:
    df = run_sql(f"""SELECT send_id, run_id, movement_type, line_key, parent_article, article, size, from_site, to_site, qty,
                            decision, approved_by, sent_by, sent_at
                     FROM {SEND_FQ} WHERE run_id = {_lit(run_id)} ORDER BY sent_at, movement_type, from_site, to_site""")
    return _nums(df, ["qty"])


def not_sent(run_id: str) -> pd.DataFrame:
    df = run_sql(f"""SELECT movement_type, article, size, from_site, to_site, proposed_qty, status, reason, mgr_decision, mgr_note
                     FROM {DEC_FQ}
                     WHERE run_id = {_lit(run_id)} AND movement_type <> 'FIRST_ALLOCATION'
                       AND ((status = 'REJECTED' AND COALESCE(mgr_decision, '') <> 'KEPT')
                            OR mgr_decision = 'SENT_BACK'
                            OR (status IN ('CHANGED', 'ADDED') AND mgr_decision IS NULL))""")
    return _nums(df, ["proposed_qty"])


# ---------- first allocation (opening packs) ----------
_CC = {"UAE": "AE", "KSA": "SA", "KWT": "KW", "QTR": "QA", "BAH": "BH", "OMN": "OM"}


CC_NAME = {"AE": "UAE", "SA": "KSA", "KW": "Kuwait", "QA": "Qatar", "BH": "Bahrain", "OM": "Oman"}


def country_codes(countries: list) -> list:
    """Store-list country names (UAE, KSA, …) -> the 2-letter codes used by the engine outputs."""
    return [_CC.get(c, c) for c in countries] or ["AE"]


def fa_country(countries: list) -> str:
    return "AE" if "AE" in country_codes(countries) else country_codes(countries)[0]


def fa_summary(country) -> dict:
    """Opening packs at child (SKU) level. country: one code or a list. Needs you = flagged packs not yet approved."""
    cl = country if isinstance(country, (list, tuple)) else [country]
    cin = ", ".join(_lit(c) for c in cl)
    df = run_sql(f"""
        WITH p AS (SELECT country, parent, review, run_id, buy_source, reserve_units, pack_units FROM {FQ}.nl_pack_country
                   WHERE country IN ({cin})),
        d AS (SELECT line_key FROM {DEC_FQ} WHERE movement_type = 'FIRST_ALLOCATION' AND status = 'APPROVED'
              AND run_id IN (SELECT DISTINCT run_id FROM p)),
        o AS (SELECT o.art, o.pack_qty FROM {FQ}.nl_opening_pack o JOIN p ON p.country = o.country AND p.parent = o.parent
              WHERE o.pack_qty > 0),
        np AS (SELECT p.* FROM p LEFT JOIN d ON d.line_key = CONCAT(p.country, '|', p.parent)
               WHERE p.review <> '' AND d.line_key IS NULL),
        nk AS (SELECT DISTINCT MATERIAL AS art FROM prod_mena.gold.mena_sports_product_master_view
               WHERE MATL_CAT = '02' AND RT_CONFMAT IN (SELECT parent FROM np))
        SELECT (SELECT COUNT(DISTINCT art) FROM o) AS skus,
               (SELECT COUNT(*) FROM (SELECT art FROM o UNION SELECT art FROM nk)) AS all_skus,
               (SELECT SUM(pack_qty) FROM o) AS pack,
               (SELECT COUNT(*) FROM nk) AS need_skus,
               (SELECT SUM(pack_units) FROM np) AS need_qty,
               (SELECT SUM(reserve_units) FROM p) AS reserve,
               (SELECT COUNT(*) FROM p) AS articles, (SELECT COUNT_IF(review <> '') FROM p) AS review,
               (SELECT COUNT_IF(pack_units = 0) FROM p) AS held,
               (SELECT MAX(run_id) FROM p) AS run_id, (SELECT MAX(buy_source) FROM p) AS buy_source""")
    d = df.iloc[0].to_dict() if not df.empty else {}
    for k in ["skus", "all_skus", "pack", "need_skus", "need_qty", "reserve", "articles", "review", "held"]:
        d[k] = int(float(d.get(k) or 0))
    return d


SEASON_NAME = {"SPRG": "Spring", "SUMR": "Summer", "FALL": "Fall", "HOLI": "Holiday"}


def _dc_sites(country: str) -> str:
    return f"SELECT dc_site FROM {FQ}.cfg_replen_dc WHERE country = {_lit(country)} AND tier = 1"


def dc_name(country: str) -> str:
    df = run_sql(f"SELECT CONCAT_WS(' + ', SORT_ARRAY(COLLECT_LIST(dc_site))) AS s FROM {FQ}.cfg_replen_dc "
                 f"WHERE country = {_lit(country)} AND tier = 1")
    return str(df["s"].iloc[0]) if len(df) else ""


def fa_articles(country: str, which: str, search: str = "", limit: int = 400) -> pd.DataFrame:
    """Opening packs for one country, with season/year (article master) and stock already at the country's DC."""
    where = [f"p.country = {_lit(country)}"]
    if which == "need":
        where.append("p.review <> '' AND d.status IS NULL")
    elif which == "ready":
        where.append("(p.review = '' OR d.status IS NOT NULL)")
    if search:
        s = search.upper().replace("'", "''")
        where.append(f"(UPPER(p.parent) LIKE '%{s}%' OR UPPER(p.name) LIKE '%{s}%')")
    df = run_sql(f"""
        WITH p0 AS (
          SELECT p.parent, p.name, p.buy_qty, p.buy_source, p.units_13w, p.units_p80, p.demand_3w, p.pack_units,
                 p.reserve_units, p.stores_ranged,
                 p.stores_eligible, p.status, p.review, p.confidence, p.run_id, d.status AS decision, d.decided_by
          FROM {FQ}.nl_pack_country p
          LEFT JOIN {DEC_FQ} d ON d.movement_type = 'FIRST_ALLOCATION' AND d.line_key = CONCAT(p.country, '|', p.parent)
                               AND d.run_id = p.run_id
          WHERE {' AND '.join(where)}
          ORDER BY CASE WHEN p.review <> '' AND d.status IS NULL THEN 0 ELSE 1 END, p.buy_qty DESC
          LIMIT {int(limit)}),
        ch AS (SELECT MATERIAL AS art, RT_CONFMAT AS parent, RT_SEAYR AS yr, `/BIC/ZCHSEASON` AS seas
               FROM prod_mena.gold.mena_sports_product_master_view
               WHERE MATL_CAT = '02' AND RT_CONFMAT IN (SELECT parent FROM p0)),
        sea AS (SELECT parent, MAX(yr) AS yr, MAX(seas) AS seas, COUNT(DISTINCT art) AS skus FROM ch GROUP BY parent),
        snap AS (SELECT MAX(c_file_date) AS d FROM sap_gmg_datalake.bronze.atp_detail),
        dcs AS (SELECT ch.parent, SUM(a.atp_qty) AS dc_now
                FROM sap_gmg_datalake.bronze.atp_detail a JOIN ch ON ch.art = a.article_code
                WHERE a.c_file_date = (SELECT d FROM snap) AND a.storage_location = 'SL01'
                  AND a.site_code IN ({_dc_sites(country)}) GROUP BY ch.parent)
        SELECT p0.*, sea.yr, sea.seas, sea.skus, COALESCE(dcs.dc_now, 0) AS dc_now
        FROM p0 LEFT JOIN sea USING (parent) LEFT JOIN dcs USING (parent)
        ORDER BY CASE WHEN p0.review <> '' AND p0.decision IS NULL THEN 0 ELSE 1 END, p0.buy_qty DESC""")
    df = _nums(df, ["buy_qty", "units_13w", "units_p80", "demand_3w", "pack_units", "reserve_units", "stores_ranged",
                    "stores_eligible", "skus", "dc_now"])
    df["season"] = [f"{SEASON_NAME.get(str(x), x or '')} {y or ''}".strip() for x, y in zip(df["seas"], df["yr"])]
    return df


def fa_detail(parent: str) -> dict:
    """Forecast, image and look-alikes (with images) for one new article."""
    f = run_sql(f"""SELECT f.*, COALESCE(f.url, i.url) AS img, m.url AS model_img, m.parent AS model_img_parent
                    FROM {FQ}.nl_forecast f
                    LEFT JOIN (SELECT parent, MAX(url) AS url FROM {FQ}.dim_product_image WHERE url IS NOT NULL GROUP BY parent) i
                      ON i.parent = f.parent
                    LEFT JOIN (SELECT SPLIT(parent, '-')[0] AS model, MAX(parent) AS parent, MAX(url) AS url
                               FROM {FQ}.dim_product_image WHERE url IS NOT NULL GROUP BY 1) m
                      ON m.model = SPLIT(f.parent, '-')[0]
                    WHERE f.parent = {_lit(parent)}""")
    a = run_sql(f"""WITH a AS (SELECT * FROM {FQ}.nl_analogues WHERE parent = {_lit(parent)} ORDER BY rank LIMIT 5),
                    pm AS (SELECT RT_CONFMAT AS analogue, MAX(TRIM(CM_CDT3_TXTMD)) AS ah2
                           FROM prod_mena.gold.mena_sports_product_master_view
                           WHERE MATL_CAT = '02' AND RT_CONFMAT IN (SELECT analogue FROM a) GROUP BY RT_CONFMAT)
                    SELECT a.rank, a.analogue, a.analogue_name, a.analogue_units_13w, a.analogue_first_sale, a.matched, i.url, pm.ah2
                    FROM a
                    LEFT JOIN (SELECT parent, MAX(url) AS url FROM {FQ}.dim_product_image WHERE url IS NOT NULL GROUP BY parent) i
                      ON i.parent = a.analogue
                    LEFT JOIN pm ON pm.analogue = a.analogue
                    ORDER BY a.rank""")
    a = _nums(a, ["rank", "analogue_units_13w"])
    return {"f": f.iloc[0].to_dict() if len(f) else {}, "analogues": a}


def approve_packs(country: str, rows: pd.DataFrame, user: str) -> int:
    if rows is None or rows.empty:
        return 0
    q = pd.DataFrame({
        "movement_type": "FIRST_ALLOCATION", "line_key": country + "|" + rows["parent"].astype(str),
        "parent_article": rows["parent"], "article": rows["parent"], "size": "all", "from_site": "DC",
        "to_site": rows["stores_ranged"].fillna(0).astype(int).astype(str) + " stores",
        "proposed_qty": rows["pack_units"], "new_qty": rows["pack_units"], "value_aed": None,
        "review_reason": rows["review"]})
    return save_decisions(str(rows["run_id"].iloc[0]), q, "APPROVED", None, user)


def fa_pack_grid(country: str, parent: str) -> pd.DataFrame:
    """Opening pack at store x size, for the store -> size-curve drill-down."""
    df = run_sql(f"""SELECT store, store_name, grade, size, key_size, pack_qty AS qty FROM {FQ}.nl_opening_pack
                     WHERE country = {_lit(country)} AND parent = {_lit(parent)} AND pack_qty > 0""")
    return _nums(df, ["qty", "key_size"])


def fa_pack(country: str, parent: str) -> tuple:
    base = f"FROM {FQ}.nl_opening_pack WHERE country = {_lit(country)} AND parent = {_lit(parent)}"
    by_store = _nums(run_sql(f"SELECT store, MAX(store_name) AS store_name, MAX(grade) AS grade, SUM(pack_qty) AS qty {base} "
                             f"GROUP BY store HAVING SUM(pack_qty) > 0 ORDER BY qty DESC"), ["qty"])
    by_size = _nums(run_sql(f"SELECT size, MAX(key_size) AS key_size, SUM(pack_qty) AS qty {base} GROUP BY size"),
                    ["qty", "key_size"])
    if len(by_size):
        by_size["_o"] = pd.to_numeric(by_size["size"].str.extract(r"([\d.]+)")[0], errors="coerce")
        by_size = by_size.sort_values(["_o", "size"]).drop(columns="_o")
    return by_store, by_size


# ---------- impact ----------
def daily_series(days: int = 30) -> pd.DataFrame:
    """One point per full-scope daily run: SKUs and qty for IBT and DC transfer, plus IBT expected uplift."""
    df = run_sql(f"""
        WITH s AS (SELECT run_id, run_ts, total_expected_uplift
                   FROM {FQ}.run_stats_history WHERE (triggered_by = 'app' OR triggered_by LIKE 'dev-%')
                     AND COALESCE(category_scope, 'ALL') = 'ALL' AND run_ts >= current_date() - INTERVAL {int(days)} DAYS),
             i AS (SELECT run_id, COUNT(DISTINCT article) AS ibt_skus, SUM(recommended_qty) AS ibt_qty
                   FROM {FQ}.recommendations_history WHERE run_id IN (SELECT run_id FROM s) GROUP BY run_id),
             r AS (SELECT run_id, COUNT(DISTINCT article) AS dc_skus, SUM(recommended_qty) AS dc_qty
                   FROM {FQ}.replen_recommendations_history
                   WHERE recommended_qty >= 1 AND run_id IN (SELECT run_id FROM s) GROUP BY run_id)
        SELECT TO_DATE(s.run_ts) AS day, s.run_id, i.ibt_skus, i.ibt_qty, s.total_expected_uplift AS ibt_uplift,
               r.dc_skus, r.dc_qty
        FROM s LEFT JOIN i USING (run_id) LEFT JOIN r USING (run_id) ORDER BY s.run_ts""")
    df = _nums(df, ["ibt_skus", "ibt_qty", "ibt_uplift", "dc_skus", "dc_qty"])
    if len(df):
        df["day"] = pd.to_datetime(df["day"])
        df = df.sort_values("day").drop_duplicates("day", keep="last")
    return df


# ---------- rules ----------
def run_settings() -> pd.DataFrame:
    return run_sql(f"""
        SELECT s.param_name, COALESCE(c.param_value, s.param_value) AS value, s.notes
        FROM {FQ}.cfg_scalar_params s LEFT JOIN {FQ}.run_config c USING (param_name)
        WHERE s.param_name NOT IN ('COUNTRY')
        UNION ALL SELECT param_name, param_value, notes FROM {FQ}.cfg_replen_params""")


def size_rules() -> pd.DataFrame:
    df = run_sql(f"""
        SELECT division, ah2_description AS category, gender, COUNT(*) AS key_sizes,
               MAX(threshold) AS full_size_when_more_than, CONCAT_WS(', ', SORT_ARRAY(COLLECT_LIST(size))) AS sizes
        FROM {FQ}.cfg_size_curve GROUP BY ALL ORDER BY division, category, gender""")
    df = _nums(df, ["key_sizes", "full_size_when_more_than"])
    df["check"] = ""
    df.loc[(df["key_sizes"] >= 20) & (df["full_size_when_more_than"] <= 5), "check"] = "Many sizes, low threshold"
    return df


def change_log(limit_per_table: int = 6) -> pd.DataFrame:
    out = []
    for t in ["cfg_store_grade", "cfg_size_curve", "cfg_scalar_params", "cfg_replen_params", "cfg_guardrails"]:
        try:
            h = run_sql(f"""SELECT timestamp, operation, userName, operationMetrics['numOutputRows'] AS rows_written
                            FROM (DESCRIBE HISTORY {FQ}.{t}) WHERE operation IN ('WRITE', 'CREATE OR REPLACE TABLE AS SELECT',
                            'MERGE', 'UPDATE', 'DELETE') ORDER BY version DESC LIMIT {int(limit_per_table)}""")
            h["table"] = t
            out.append(h)
        except Exception:
            pass
    if not out:
        return pd.DataFrame()
    df = pd.concat(out, ignore_index=True)
    df["timestamp"] = pd.to_datetime(df["timestamp"], errors="coerce")
    return df.sort_values("timestamp", ascending=False)


# ---------- coverage: which articles the engine evaluates ----------
_PM = """SELECT MATERIAL AS a, MAX(RF_BNDID) AS brand, MAX(TRIM(`/BIC/ZCHWGH5`)) AS gender,
                MAX(TRIM(`/BIC/ZRWGH2_T`)) AS division,
                MAX(UPPER(CONCAT(COALESCE(RF_BNDID, ''), COALESCE(`/BIC/ZRWGH3_T`, ''), COALESCE(`/BIC/ZCHWGH5`, ''),
                                 COALESCE(`/BIC/ZRWGH2_T`, '')))) AS ck
         FROM prod_mena.gold.mena_sports_product_master_view WHERE MATL_CAT = '02' GROUP BY MATERIAL"""


def coverage(run_id: str) -> pd.DataFrame:
    """One row per category x brand x gender x division: child articles with stock in the run's stores or DC pools
    (Nike-family brands per cfg_brand_mapping), how many the engine evaluated, and how many got an IBT / DC transfer."""
    df = run_sql(f"""
        WITH pm AS ({_PM}),
        cat AS (SELECT UPPER(converter_key) AS ck, MAX(business_category) AS category FROM {FQ}.cfg_category_converter GROUP BY 1),
        stk AS (SELECT DISTINCT article_code AS a FROM {FQ}.effective_stock WHERE effective_stock > 0),
        brands AS (SELECT brand_id AS brand FROM {FQ}.cfg_brand_mapping WHERE is_nike),
        dcs AS (SELECT DISTINCT d.article_code AS a FROM {FQ}.dc_pool_stock d JOIN pm ON pm.a = d.article_code
                WHERE d.pool_atp > 0 AND pm.brand IN (SELECT brand FROM brands)),
        dem AS (SELECT DISTINCT article_code AS a FROM {FQ}.replen_demand),
        ibt AS (SELECT DISTINCT article AS a FROM {_ibt_src(run_id)}),
        dct AS (SELECT DISTINCT article AS a FROM {_dc_src(run_id)} WHERE recommended_qty >= 1),
        u AS (SELECT a FROM stk UNION SELECT a FROM dcs)
        SELECT COALESCE(cat.category, 'Not mapped') AS category, COALESCE(pm.brand, 'Not in article master') AS brand,
               COALESCE(pm.gender, '—') AS gender, COALESCE(pm.division, '—') AS division,
               COUNT(*) AS with_stock, COUNT(stk.a) AS in_stores, COUNT(dcs.a) AS at_dc,
               COUNT_IF(stk.a IS NOT NULL OR dem.a IS NOT NULL) AS evaluated,
               COUNT(dem.a) AS dc_evaluated, COUNT(ibt.a) AS with_ibt, COUNT(dct.a) AS with_dc,
               COUNT_IF(ibt.a IS NOT NULL OR dct.a IS NOT NULL) AS with_transfer,
               COUNT_IF(stk.a IS NULL AND dem.a IS NULL) AS dc_only_not_evaluated
        FROM u LEFT JOIN pm ON pm.a = u.a LEFT JOIN cat ON cat.ck = pm.ck
        LEFT JOIN stk ON stk.a = u.a LEFT JOIN dcs ON dcs.a = u.a LEFT JOIN dem ON dem.a = u.a
        LEFT JOIN ibt ON ibt.a = u.a LEFT JOIN dct ON dct.a = u.a
        GROUP BY ALL""")
    return _nums(df, ["with_stock", "in_stores", "at_dc", "evaluated", "dc_evaluated", "with_ibt", "with_dc",
                      "with_transfer", "dc_only_not_evaluated"])


def master_funnel() -> dict:
    """Article master (child articles of the Nike-family brands) -> with stock anywhere in GMG today."""
    df = run_sql(f"""
        WITH pm AS ({_PM}),
        stk AS (SELECT DISTINCT article_code AS a FROM {FQ}.effective_stock WHERE effective_stock > 0),
        brands AS (SELECT brand_id AS brand FROM {FQ}.cfg_brand_mapping WHERE is_nike),
        snap AS (SELECT MAX(c_file_date) AS d FROM sap_gmg_datalake.bronze.atp_detail),
        anyst AS (SELECT DISTINCT article_code AS a FROM sap_gmg_datalake.bronze.atp_detail
                  WHERE c_file_date = (SELECT d FROM snap) AND atp_qty > 0)
        SELECT COUNT(*) AS master_articles, COUNT(anyst.a) AS stocked_anywhere
        FROM pm LEFT JOIN anyst ON anyst.a = pm.a WHERE pm.brand IN (SELECT brand FROM brands)""")
    return {k: int(float(v or 0)) for k, v in df.iloc[0].to_dict().items()} if not df.empty else {}


def coverage_gap() -> pd.DataFrame:
    """Why evaluated < with stock: Nike-family SKUs that sit only at the DC (no store stock, no store demand), by reason."""
    df = run_sql(f"""
        WITH pm AS (SELECT MATERIAL AS a, MAX(RF_BNDID) AS brand, MAX(RT_SEAYR) AS yr
                    FROM prod_mena.gold.mena_sports_product_master_view WHERE MATL_CAT = '02' GROUP BY MATERIAL),
        stk AS (SELECT DISTINCT article_code AS a FROM {FQ}.effective_stock WHERE effective_stock > 0),
        dem AS (SELECT DISTINCT article_code AS a FROM {FQ}.replen_demand),
        gap AS (SELECT d.article_code AS a, SUM(d.pool_atp) AS q FROM {FQ}.dc_pool_stock d JOIN pm ON pm.a = d.article_code
                WHERE d.pool_atp > 0 AND pm.brand IN (SELECT brand_id FROM {FQ}.cfg_brand_mapping WHERE is_nike)
                  AND d.article_code NOT IN (SELECT a FROM stk) AND d.article_code NOT IN (SELECT a FROM dem)
                GROUP BY d.article_code),
        sm AS (SELECT PLANT, MAX(`/BIC/ZBU`) AS bu FROM prod_mena.gold.mena_sports_store_master_view GROUP BY PLANT),
        sl AS (SELECT s.variant_article_code AS a, MAX(IF(sm.bu = 'NIKE', 1, 0)) AS nike_sold, MAX(IF(sm.bu <> 'NIKE', 1, 0)) AS other_sold
               FROM sap_gmg_datalake.silver.fct_sap_sales_detail_v s JOIN sm ON sm.PLANT = s.site_code
               WHERE s.txn_date > current_date() - INTERVAL 180 DAYS AND s.txn_qty > 0 AND s.variant_article_code IN (SELECT a FROM gap)
               GROUP BY s.variant_article_code),
        fa AS (SELECT DISTINCT art AS a FROM {FQ}.nl_opening_pack)
        SELECT CASE WHEN fa.a IS NOT NULL THEN 'Spring 2027: in the opening packs'
                    WHEN pm.yr >= '2026' AND COALESCE(sl.nike_sold, 0) = 1 THEN '2026: sold in Nike stores, now out of stock'
                    WHEN pm.yr >= '2026' AND COALESCE(sl.other_sold, 0) = 1 THEN '2026: sells only in SSS and other banners'
                    WHEN pm.yr >= '2026' THEN '2026: no sales anywhere in 180 days'
                    ELSE '2025 or older' END AS reason,
               COUNT(*) AS skus, SUM(gap.q) AS qty
        FROM gap LEFT JOIN pm ON pm.a = gap.a LEFT JOIN sl ON sl.a = gap.a LEFT JOIN fa ON fa.a = gap.a
        GROUP BY 1""")
    return _nums(df, ["skus", "qty"])
