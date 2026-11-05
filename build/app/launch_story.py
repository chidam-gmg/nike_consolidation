"""New-article walkthrough — one style with no sales history, told end to end: the image, what we know about it
(master attributes and attributes read from the image), how much each attribute counts, the past launches it looks
like and what they matched, the arithmetic to the forecast, and how far to trust it. One self-contained HTML panel."""
from __future__ import annotations

import html

import pandas as pd
import streamlit.components.v1 as components

THEMES = {
    "light": dict(bg="#FFFFFF", card="#F7F9FA", line="#E1E7E8", ink="#20274C", dim="#5C6478", faint="#A9B1C2",
                  accent="#2658F2", accent_soft="#E4ECFE", warm="#A4502F", warm_soft="#FBECE4", ok="#12704D", ok_soft="#E3F3EA"),
    "dark": dict(bg="#0A0D16", card="#111626", line="#252E48", ink="#E5E9F4", dim="#8F98B3", faint="#4A5372",
                 accent="#6B92FF", accent_soft="#18244A", warm="#EB9677", warm_soft="#3A2219", ok="#5FD49A", ok_soft="#123024"),
}
LABEL = {"ah2": "Category", "sport": "Sport", "silhouette": "Silhouette", "plc": "Life cycle", "colour_family": "Colour (code)",
         "family": "Name family", "price": "Price", "fit": "Fit", "pattern": "Pattern", "graphic_size": "Graphic size",
         "text_graphic": "Text graphic", "logo_size": "Logo size", "style": "Style", "base_colour": "Base colour"}
IMAGE = {"fit", "pattern", "graphic_size", "text_graphic", "logo_size", "style", "base_colour"}
COUNTRY = {"AE": "UAE", "SA": "KSA", "KW": "Kuwait", "QA": "Qatar", "BH": "Bahrain", "OM": "Oman", "EG": "Egypt"}


def _label(name) -> str:
    return " ".join(w if (len(w) <= 2 or any(ch.isdigit() for ch in w)) else w.title() for w in str(name or "").split())


def _e(x) -> str:
    return html.escape("" if x is None or (isinstance(x, float) and x != x) else str(x))


def build(r: dict, img: str | None, img_src: str | None, ana: pd.DataFrame, weights: pd.DataFrame, cty: pd.DataFrame,
          wk: pd.DataFrame, bt: pd.DataFrame, mult: float | None, theme: str = "light") -> str:
    c = THEMES.get(theme, THEMES["light"])
    unit = "pairs" if str(r.get("division")).upper() == "FOOTWEAR" else "units"
    image_method = str(r.get("method")) == "image + attributes"
    look = ana[ana["kind"] == "look-alike"].copy()
    sibs = ana[ana["kind"] == "same model"].copy()
    est_b = float(look["analogue_units_13w"].mean()) if len(look) else float("nan")
    est_a = float(sibs["analogue_units_13w"].mean()) if len(sibs) else float("nan")
    p50, p80 = float(r.get("units_13w") or 0), float(r.get("units_p80") or 0)

    # 1 · what we know
    master = [("Division", r.get("division")), ("Category", r.get("ah2")), ("Gender", r.get("gender")), ("Sport", r.get("sport")),
              ("Silhouette", r.get("silhouette")), ("Life cycle", r.get("plc")), ("Global price (master)", f"{float(r.get('rrp') or 0):,.0f}"),
              ("Colour (from code)", r.get("colour_family"))]
    tags = [(LABEL[k], r.get(k)) for k in ("fit", "pattern", "graphic_size", "text_graphic", "logo_size", "style", "base_colour")]
    ok = lambda v: v is not None and not (isinstance(v, float) and v != v) and str(v).lower() not in ("", "nan", "none")
    kv = lambda rows: "".join(f'<div class="kv"><span>{_e(k)}</span><b>{_e(str(v).title() if ok(v) else "—")}</b></div>'
                              for k, v in rows)
    tag_block = (kv(tags) if any(ok(v) for _, v in tags) else
                 f'<p class="dim">No image read for this style yet, so it uses the attribute method.</p>')

    # 2 · weights
    w = weights.copy()
    w = w[w["weight"] > 0].sort_values("weight", ascending=False)
    tot = w["weight"].sum() or 1
    wbars = "".join(
        f'<div class="wrow"><span>{_e(LABEL.get(x.attribute, x.attribute))}</span>'
        f'<div class="wbar"><i style="width:{x.weight / w["weight"].max() * 100:.0f}%;background:{c["accent"] if x.attribute in IMAGE else c["ink"]}"></i></div>'
        f'<b>{x.weight / tot * 100:.0f}%</b></div>' for x in w.itertuples())

    # 3 · look-alikes
    def card(x):
        pic = f'<img src="{_e(x.img)}" alt="">' if isinstance(x.img, str) and x.img else '<div class="noimg">no image</div>'
        chips = "".join(f'<span class="chip{" img" if m.strip() in IMAGE else ""}">{_e(LABEL.get(m.strip(), m.strip()))}</span>'
                        for m in str(x.matched or "").split(",") if m.strip() and x.kind == "look-alike")
        sc = f'<span class="sc">score {x.score:.1f}</span>' if pd.notna(x.score) else '<span class="sc">same model</span>'
        return (f'<div class="ana">{pic}<div class="an">{_e(_label(x.analogue_name))}</div>{sc}'
                f'<div class="au"><b>{x.analogue_units_13w:,.0f}</b> {unit} in 13 wks</div><div class="chips">{chips}</div></div>')
    cards = "".join(card(x) for x in pd.concat([sibs, look]).itertuples())

    # 4 · arithmetic
    steps = [f"The {len(look)} best-matching past launches sold <b>{est_b:,.0f}</b> {unit} on average in their first 13 weeks."]
    if len(sibs):
        steps.append(f"{len(sibs)} earlier colourways of the same model averaged <b>{est_a:,.0f}</b>; the forecast blends the two 50/50.")
    steps.append(f"Forecast: <b>{p50:,.0f} {unit}</b> in the first 13 weeks (most likely).")
    if mult:
        steps.append(f"High case: × {mult:.2f} = <b>{p80:,.0f}</b> — in the back-test, 8 in 10 similar launches sold less than this.")
    if not cty.empty:
        steps.append("Split by country like its look-alikes sold: " +
                     " · ".join(f"{COUNTRY.get(a, a)} {b:,.0f}" for a, b in zip(cty["country"], cty["units_13w"])) + ".")
    steps.append(f"Spread over 13 weeks from the assumed launch week of {_e(r.get('launch_week'))} using the launch shape of "
                 f"{_e(str(r.get('division')).title())} · {_e(r.get('plc'))} styles.")
    spark = ""
    if not wk.empty:
        v = wk["units_p50"].astype(float).tolist(); mx = max(v) or 1; W, H = 360, 70
        pts = " ".join(f"{i * W / max(len(v) - 1, 1):.1f},{H - 6 - (x / mx) * (H - 14):.1f}" for i, x in enumerate(v))
        spark = (f'<svg viewBox="0 0 {W} {H}" width="100%" height="{H}"><polyline points="{pts}" fill="none" stroke="{c["accent"]}" '
                 f'stroke-width="2.5"/></svg><div class="dim small">Weekly forecast, weeks 1–13</div>')

    # 5 · trust
    vrow = weights.dropna(subset=["val_error"]).head(1)
    btd = {x["level"]: x for _, x in bt.iterrows()}
    trust = []
    if image_method and len(vrow):
        v = vrow.iloc[0]
        trust.append(f"Image + attributes, tested on <b>{int(v.val_styles)}</b> Spring 2026 men's tees it never saw: error "
                     f"<b>{v.val_error * 100:.0f}%</b> (attributes alone {v.val_error_fallback * 100:.0f}%), within 2× of actual "
                     f"<b>{v.val_within_2x * 100:.0f}%</b> ({v.val_within_2x_fallback * 100:.0f}%), ranking agreement "
                     f"{v.val_rank:.2f} ({v.val_rank_fallback:.2f}).")
    st_ = btd.get("style", {}); ct_ = btd.get("country", {}); dg_ = btd.get("division x gender", {})
    if st_ is not None and len(st_):
        trust.append(f"Whole method on all Spring 2026 launches: one style lands within 2× of actual "
                     f"{float(st_['within_2x']) * 100:.0f}% of the time, but totals land within "
                     f"<b>±{float(ct_['wape']) * 100:.0f}%</b> by country and <b>±{float(dg_['wape']) * 100:.0f}%</b> by "
                     f"division × gender — the level buys are planned at.")
    trust.append("This style has not sold yet, so its own error cannot be measured; these figures come from forecasting last "
                 "spring as if it were new and comparing with what actually sold.")

    method_pill = ("Image + attributes" if image_method else "Attributes (no image read)")
    css = f"""
    @import url('https://fonts.googleapis.com/css2?family=Source+Sans+3:wght@400;600;700&display=swap');
    * {{ box-sizing:border-box; margin:0; }}
    body {{ background:{c['bg']}; color:{c['ink']}; font:14px/1.5 'Source Sans 3', system-ui, sans-serif; padding:4px 2px; }}
    .hd {{ display:flex; gap:18px; align-items:center; flex-wrap:wrap; }}
    .hero {{ width:150px; height:150px; object-fit:contain; background:#FFFFFF; border:1px solid {c['line']}; border-radius:14px; padding:6px; }}
    .eyebrow {{ font-size:11px; letter-spacing:.12em; font-weight:700; color:{c['dim']}; text-transform:uppercase; }}
    h1 {{ font:600 26px/1.2 Georgia, serif; margin:4px 0; }}
    .dim {{ color:{c['dim']}; }} .small {{ font-size:12px; }}
    .pill {{ display:inline-block; border-radius:999px; padding:3px 11px; font-size:12px; font-weight:700; margin-right:6px;
             background:{c['accent_soft']}; color:{c['accent']}; }}
    .pill.warm {{ background:{c['warm_soft']}; color:{c['warm']}; }}
    .big {{ margin-left:auto; text-align:right; }} .big .v {{ font-size:40px; font-weight:700; line-height:1; }}
    .sec {{ border:1px solid {c['line']}; border-radius:14px; padding:14px 16px; margin-top:14px; }}
    .sec h2 {{ font:600 16px/1.3 'Source Sans 3', sans-serif; margin-bottom:8px; }}
    .n {{ display:inline-grid; place-items:center; width:22px; height:22px; border-radius:6px; background:{c['ink']}; color:{c['bg']};
          font-size:12px; font-weight:700; margin-right:8px; }}
    .two {{ display:grid; grid-template-columns:1fr 1fr; gap:18px; }}
    .kv {{ display:flex; justify-content:space-between; border-bottom:1px solid {c['line']}; padding:4px 0; font-size:13.5px; }}
    .kv span {{ color:{c['dim']}; }}
    .sub {{ font-size:12px; font-weight:700; letter-spacing:.08em; text-transform:uppercase; color:{c['dim']}; margin-bottom:4px; }}
    .sub.img {{ color:{c['accent']}; }}
    .wrow {{ display:grid; grid-template-columns:120px 1fr 44px; gap:10px; align-items:center; font-size:13px; padding:2px 0; }}
    .wbar {{ background:{c['card']}; border-radius:4px; height:10px; overflow:hidden; }} .wbar i {{ display:block; height:100%; border-radius:4px; }}
    .wrow b {{ text-align:right; font-variant-numeric:tabular-nums; }}
    .legend {{ font-size:12px; color:{c['dim']}; margin-top:6px; }} .legend i {{ display:inline-block; width:10px; height:10px; border-radius:2px; margin:0 4px 0 10px; vertical-align:middle; }}
    .grid {{ display:grid; grid-template-columns:repeat(auto-fill, minmax(150px, 1fr)); gap:10px; }}
    .ana {{ border:1px solid {c['line']}; border-radius:10px; padding:8px; background:{c['card']}; display:flex; flex-direction:column; gap:3px; }}
    .ana img, .noimg {{ width:100%; height:110px; object-fit:contain; background:#FFFFFF; border-radius:6px; }}
    .noimg {{ display:grid; place-items:center; color:{c['faint']}; font-size:12px; }}
    .an {{ font-weight:700; font-size:12.5px; line-height:1.25; }}
    .sc {{ font-size:11.5px; color:{c['dim']}; }} .au {{ font-size:12.5px; }} .au b {{ font-size:15px; }}
    .chips {{ display:flex; flex-wrap:wrap; gap:3px; }}
    .chip {{ font-size:10.5px; border-radius:999px; padding:1px 7px; background:{c['bg']}; border:1px solid {c['line']}; color:{c['dim']}; }}
    .chip.img {{ border-color:{c['accent']}; color:{c['accent']}; }}
    ol {{ padding-left:20px; display:grid; gap:4px; }}
    """
    return f"""<html><head><meta charset="utf-8"><style>{css}</style></head><body>
    <div class="hd">
      {f'<img class="hero" src="{_e(img)}" alt="">' if img else ''}
      <div><div class="eyebrow">New article walkthrough · Spring 2027 · no sales yet</div>
        <h1>{_e(_label(r.get('name')))}</h1>
        <div class="dim">{_e(r.get('parent'))} · launch week {_e(r.get('launch_week'))} (assumed)</div>
        <div style="margin-top:6px"><span class="pill">{method_pill}</span><span class="pill warm">{_e(r.get('tier'))}</span>
          {f'<span class="dim small">image: {_e(img_src)}</span>' if img_src else ''}</div></div>
      <div class="big"><div class="eyebrow">Forecast · first 13 weeks</div><div class="v">{p50:,.0f}</div>
        <div class="dim">{unit} · high case {p80:,.0f}</div></div>
    </div>

    <div class="sec"><h2><span class="n">1</span>What we know about it</h2>
      <div class="two"><div><div class="sub">From the product master</div>{kv(master)}</div>
        <div><div class="sub img">Read from the image (Databricks vision model)</div>{tag_block}</div></div></div>

    <div class="sec"><h2><span class="n">2</span>How much each attribute counts</h2>
      <div class="two"><div>{wbars}<div class="legend"><i style="background:{c['ink']}"></i>product master
        <i style="background:{c['accent']}"></i>read from the image</div></div>
        <div class="dim">{'These weights were learned from data: men’s tees launched March–October 2025, each forecast only from launches that had finished their first 13 weeks before it. Sport, silhouette and logo size matter most; style and graphic size added nothing, so they carry no weight.' if image_method else 'This style uses the standard attribute weights (no image read yet, or its category has no validated image weights). Once an image is read and its category is validated, it moves to the image + attribute weights.'}</div></div></div>

    <div class="sec"><h2><span class="n">3</span>Who it looks like — and what actually sold</h2>
      <div class="dim small" style="margin-bottom:8px">Each past launch is scored on the attributes above; blue chips were matched from the image.</div>
      <div class="grid">{cards}</div></div>

    <div class="sec"><h2><span class="n">4</span>From look-alikes to a number</h2>
      <div class="two"><ol>{''.join(f'<li>{x}</li>' for x in steps)}</ol><div>{spark}</div></div></div>

    <div class="sec"><h2><span class="n">5</span>How far to trust it</h2><ol>{''.join(f'<li>{x}</li>' for x in trust)}</ol></div>
    </body></html>"""


def render(*args, theme: str = "light", height: int = 1900, **kw):
    components.html(build(*args, theme=theme, **kw), height=height, scrolling=True)
