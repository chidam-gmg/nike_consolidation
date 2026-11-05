"""New launches tab — forecast for styles with no sales history (the target season), and why each number is what
it is: the past launches it borrows from, with their actual first-13-week sales and images."""
from __future__ import annotations

import html

import pandas as pd
import streamlit as st

import brief_view
import databricks_io as dbio
import launch_story

COUNTRY = {"AE": "UAE", "SA": "KSA", "KW": "Kuwait", "QA": "Qatar", "BH": "Bahrain", "OM": "Oman", "EG": "Egypt"}


def _label(name) -> str:
    return " ".join(w if (len(w) <= 2 or any(ch.isdigit() for ch in w)) else w.title() for w in str(name or "").split())


@st.cache_data(show_spinner="Reading the launch forecast…", ttl=600)
def _styles() -> pd.DataFrame:
    df = dbio.run_sql(f"""SELECT f.*, coalesce(i.url, t.image_data_uri) img,
                                 CASE WHEN i.url IS NOT NULL THEN 'Akeneo' WHEN t.image_data_uri IS NOT NULL THEN t.source END img_source
                          FROM {dbio.FQ}.nl_forecast f LEFT JOIN {dbio.FQ}.dim_product_image i USING (parent)
                          LEFT JOIN {dbio.FQ}.nl_target_images t USING (parent)""")
    for c in ("units_13w", "units_p80", "rrp", "siblings", "est_a", "est_b", "est_c", "look_similarity"):
        df[c] = pd.to_numeric(df[c], errors="coerce")
    return df


@st.cache_data(show_spinner=False, ttl=600)
def _countries() -> pd.DataFrame:
    df = dbio.run_sql(f"SELECT country, ROUND(SUM(units_13w)) units FROM {dbio.FQ}.nl_forecast_country GROUP BY 1")
    df["units"] = pd.to_numeric(df["units"], errors="coerce")
    return df


@st.cache_data(show_spinner=False, ttl=600)
def _backtest() -> pd.DataFrame:
    return dbio.run_sql(f"SELECT * FROM {dbio.FQ}.nl_backtest")


@st.cache_data(show_spinner=False, ttl=600)
def _detail(parent: str):
    p = str(parent).replace("'", "''")
    wk = dbio.run_sql(f"SELECT week_start, units_p50, units_p80 FROM {dbio.FQ}.nl_forecast_weekly WHERE parent = '{p}' ORDER BY week_start")
    cc = dbio.run_sql(f"SELECT country, units_13w FROM {dbio.FQ}.nl_forecast_country WHERE parent = '{p}' ORDER BY units_13w DESC")
    an = dbio.run_sql(f"""SELECT a.kind, a.analogue, a.analogue_name, a.analogue_units_13w, a.analogue_first_sale, a.score, a.matched, a.look,
                                 i.url img, i.colour FROM {dbio.FQ}.nl_analogues a
                          LEFT JOIN {dbio.FQ}.dim_product_image i ON i.parent = a.analogue WHERE a.parent = '{p}' ORDER BY a.rank""")
    for c in ("analogue_units_13w", "score", "look"):
        an[c] = pd.to_numeric(an[c], errors="coerce")
    for c in ("units_p50", "units_p80"):
        wk[c] = pd.to_numeric(wk[c], errors="coerce")
    cc["units_13w"] = pd.to_numeric(cc["units_13w"], errors="coerce")
    return wk, cc, an


@st.cache_data(show_spinner=False, ttl=600)
def _weights() -> pd.DataFrame:
    w = dbio.run_sql(f"SELECT * FROM {dbio.FQ}.nl_weights")
    for c in [c for c in w.columns if c == "weight" or c.startswith("val_")]:
        w[c] = pd.to_numeric(w[c], errors="coerce")
    return w


def _analogue_card(x) -> str:
    img = f'<img src="{html.escape(x.img)}" alt="">' if isinstance(x.img, str) and x.img else ""
    return (f'<div class="nl-a">{img}<span class="nl-kind">{html.escape(x.kind)}</span>'
            f'<b>{html.escape(_label(x.analogue_name))}</b><span class="u">{x.analogue_units_13w:,.0f}</span> units in '
            f'13 weeks · launched {html.escape(str(x.analogue_first_sale))}</div>')


def render(theme: str):
    t = brief_view.TOKENS.get(theme, brief_view.TOKENS["light"])
    st.markdown(brief_view._css(t) + f"""<style>
      .nl-ana {{ display:grid; grid-template-columns:repeat(auto-fill,minmax(150px,1fr)); gap:10px; }}
      .nl-a {{ border:1px solid {t['line']}; border-radius:10px; padding:8px; background:{t['card']}; font-size:12.5px; color:{t['dim']}; }}
      .nl-a img {{ width:100%; height:110px; object-fit:contain; background:#FFFFFF; border-radius:6px; }}
      .nl-a b {{ color:{t['ink']}; display:block; font-size:13px; line-height:1.3; margin-top:4px; }}
      .nl-a .u {{ color:{t['ink']}; font-weight:700; font-size:15px; }}
      .nl-kind {{ font-size:11px; font-weight:700; text-transform:uppercase; letter-spacing:.06em; color:{t['accent']}; }}
    </style>""", unsafe_allow_html=True)
    try:
        s, cc, bt = _styles(), _countries(), _backtest()
    except Exception as e:
        st.info(f"The launch forecast is not available yet ({e}).")
        return
    if s.empty:
        st.info("No launch forecast yet.")
        return
    btd = {r["level"]: r for _, r in bt.iterrows()}
    season = str(bt["season"].iloc[0]).replace("SPRG", "Spring") if len(bt) else ""
    nxt = "Spring " + str(int(season.split()[-1]) + 1) if season else "the next season"
    lw = pd.to_datetime(s["launch_week"]).min()
    a_n = int((s["tier"].str.startswith("A")).sum())
    ctry = btd.get("country", {})
    st.markdown(f"""<div class="bf-eyebrow">New launches · {nxt} · Nike · styles with no sales history</div>
      <div class="bf-h1">{len(s):,} new styles, {s['units_13w'].sum() / 1000:,.0f}K units in their first 13 weeks.</div>
      <p class="bf-lede">Each new style borrows from the closest styles that launched before: earlier colourways of the same model
      where they exist ({a_n:,} styles), otherwise the 10 past launches most like it by category, sport, silhouette, life cycle,
      colour and price. Launches are assumed from the week of {lw:%d %b %Y}, as last spring; buyer dates replace this when
      they arrive.</p>
      <div class="bf-figs">
        <div class="bf-fig"><div class="l">New styles</div><div class="v">{len(s):,}</div><div class="s">{s['model'].nunique():,} models</div></div>
        <div class="bf-fig"><div class="l">Forecast · 13 weeks</div><div class="v">{s['units_13w'].sum() / 1000:,.0f}K</div>
          <div class="s">units, most likely</div></div>
        <div class="bf-fig"><div class="l">Borrow from same model</div><div class="v">{a_n:,}</div>
          <div class="s">styles · rest from look-alikes</div></div>
        <div class="bf-fig need"><div class="l">Back-test · {html.escape(season)}</div>
          <div class="v">±{float(ctry.get('wape', 0)) * 100:.0f}%</div><div class="s">by country · ±{float(btd.get('style', {}).get('wape', 0)) * 100:.0f}% per style</div></div>
      </div>""", unsafe_allow_html=True)

    left, right = st.columns([1.5, 1], gap="large")
    with left:
        st.markdown("##### By division and gender")
        g = (s.groupby(["division", "gender"]).agg(Styles=("parent", "size"), Forecast=("units_13w", "sum"), High=("units_p80", "sum"))
             .reset_index().sort_values("Forecast", ascending=False))
        g["Forecast"], g["High"] = g["Forecast"].round(0), g["High"].round(0)
        st.dataframe(g.rename(columns={"division": "Division", "gender": "Gender", "Forecast": "Forecast (13 wks)",
                                       "High": "High case (sum of styles)"}), hide_index=True, use_container_width=True, height=300)
    with right:
        st.markdown("##### By country")
        c2 = cc.assign(Country=cc["country"].map(COUNTRY).fillna(cc["country"])).sort_values("units", ascending=False)
        st.bar_chart(c2.set_index("Country")["units"], height=300, horizontal=True)

    st.markdown("##### Styles")
    f = st.columns([1, 1, 1, 1.2, 2])
    div = f[0].multiselect("Division", sorted(s["division"].dropna().unique()), key="nl_div", placeholder="All divisions",
                           label_visibility="collapsed")
    gen = f[1].multiselect("Gender", sorted(s["gender"].dropna().unique()), key="nl_gen", placeholder="All genders",
                           label_visibility="collapsed")
    spt = f[2].multiselect("Sport", sorted(s["sport"].dropna().unique()), key="nl_spt", placeholder="All sports",
                           label_visibility="collapsed")
    tier = f[3].multiselect("Based on", sorted(s["tier"].dropna().unique()), key="nl_tier", placeholder="Same model or look-alikes",
                            label_visibility="collapsed")
    q = f[4].text_input("Search", key="nl_q", placeholder="Search name or style code — e.g. Air Jordan, Calm Slide",
                        label_visibility="collapsed")
    v = s
    for col, sel in (("division", div), ("gender", gen), ("sport", spt), ("tier", tier)):
        if sel:
            v = v[v[col].isin(sel)]
    if q.strip():
        v = v[(v["name"].fillna("") + " " + v["parent"]).str.upper().str.contains(q.strip().upper(), regex=False)]
    v = v.sort_values("units_13w", ascending=False)
    tab = pd.DataFrame({"Image": v["img"], "Style": v["parent"], "Name": v["name"].map(_label), "Division": v["division"],
                        "Category": v["ah2"], "Gender": v["gender"], "Colour": v["colour_family"],
                        "Global price": v["rrp"].round(0), "Launch week": v["launch_week"],
                        "Forecast (13 wks)": v["units_13w"].round(0), "High case": v["units_p80"].round(0),
                        "Based on": v["tier"], "Method": v["method"], "Confidence": v.get("confidence")})
    if v["img"].notna().mean() < 0.2:          # most new-season styles have no image in Akeneo yet
        tab = tab.drop(columns="Image")
    st.dataframe(tab, hide_index=True, use_container_width=True, height=380,
                 column_config={"Image": st.column_config.ImageColumn(width="small")})
    st.download_button(f"Download ({len(tab):,} styles)", data=dbio.to_csv_bytes(tab.drop(columns="Image", errors="ignore")),
                       file_name="new_launch_forecast.csv", mime="text/csv", key="nl_dl")

    st.markdown("##### New article walkthrough")
    st.caption("Pick any style: its image, what we know about it, how much each attribute counts, the past launches it "
               "looks like and what they sold, and how the number is worked out.")
    if v.empty:
        st.caption("No styles match the filters.")
        return
    pool = pd.concat([v[v["method"] == "image + attributes"], v.head(300)]).drop_duplicates("parent")   # image-read styles first
    opts = [f"{_label(x.name)} · {x.parent} · {x.method} · {x.units_13w:,.0f}" for x in pool.itertuples()]
    default = next((i for i, x in enumerate(pool.itertuples()) if x.parent == "NKIU5193-010"), 0)
    pick = st.selectbox("Style", opts, index=default, key="nl_pick", label_visibility="collapsed")
    r = pool.iloc[opts.index(pick)]
    wk, cty, an = _detail(r["parent"])
    w = _weights()
    scope = " · ".join([str(r["division"]), str(r["gender"]), str(r["ah2"])])
    ws = w[w["scope"] == scope] if r["method"] == "image + attributes" else w[w["scope"].str.startswith("fallback")]
    mult = (r["units_p80"] / r["units_13w"]) if r["units_13w"] else None
    launch_story.render(r.to_dict(), r["img"] if isinstance(r["img"], str) else None,
                        r["img_source"] if isinstance(r.get("img_source"), str) else None, an, ws, cty, wk, bt, mult, theme=theme)

    with st.expander("How accurate is this? Back-test on last spring"):
        b = bt.copy()
        for c in ("wape", "bias", "within_2x"):
            b[c] = (pd.to_numeric(b[c], errors="coerce") * 100).round(0)
        st.dataframe(b.rename(columns={"level": "Level", "groups": "Groups", "wape": "Error %", "bias": "Bias %",
                                       "within_2x": "Within 2× %"})[["Level", "Groups", "Error %", "Bias %", "Within 2× %"]],
                     hide_index=True, use_container_width=True)
        st.caption(f"Each {html.escape(season)} launch was forecast using only what was known a month before the season, then "
                   "compared with what it sold in its first 13 weeks. Single styles are hard to call (under half land within 2× "
                   "of actual), but totals by country and by division × gender land within 10–16% — the level buys are planned at. "
                   f"The high case held for {float(bt['p80_cover'].iloc[0]) * 100:.0f}% of styles.")
