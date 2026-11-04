"""Store walkthrough — one store x style told as a story for leadership: the forecast, the store pack, what is in
stock, what to send. Pure presentation over the forecast run's tables (fc_forecast / fc_history / al_size_run);
rendered as one self-contained HTML panel (inline SVG charts, light + dark) like the Agent lab board."""
from __future__ import annotations

import html
import math

import pandas as pd
import streamlit.components.v1 as components

APPAREL_ORDER = ["XXS", "XS", "S", "M", "L", "XL", "XXL", "2XL", "3XL", "4XL"]
THEMES = {
    "light": dict(bg="#FFFFFF", card="#F7F9FA", line="#E1E7E8", ink="#20274C", dim="#5C6478", faint="#A9B1C2",
                  accent="#2658F2", accent_soft="#E4ECFE", band="#2658F2", warm="#B15940", warm_soft="#FBEADB",
                  shelf="#20274C", over="#9AA6C7", transit="#C9CFDD", ok="#12704D", ok_soft="#E3F3EA"),
    "dark": dict(bg="#0A0D16", card="#111626", line="#252E48", ink="#E5E9F4", dim="#8790AB", faint="#4A5372",
                 accent="#5B84FF", accent_soft="#17234A", band="#5B84FF", warm="#E08A6E", warm_soft="#3A2219",
                 shelf="#C9D0E4", over="#5A6690", transit="#39425E", ok="#5FD49A", ok_soft="#123024"),
}
EVENT_RANK = ["White Friday", "Ramadan", "Eid al-Fitr", "Eid al-Adha", "Dubai Shopping Festival", "Back to school",
              "UAE National Day", "11.11"]
COUNTRY = {"AE": "UAE", "SA": "KSA", "KW": "Kuwait", "QA": "Qatar", "BH": "Bahrain", "OM": "Oman", "EG": "Egypt"}


def _size_key(s: str):
    s = str(s).strip().upper()
    try:
        return (0, float(s.rstrip("YCK")), s)
    except ValueError:
        return (1, APPAREL_ORDER.index(s), s) if s in APPAREL_ORDER else (2, 0, s)


def style_label(name: str) -> str:
    """Title case for readability, but keep short codes (LE, NK, DF, SS) and model numbers as written."""
    return " ".join(w if (len(w) <= 2 or any(ch.isdigit() for ch in w)) else w.title() for w in str(name).split())


def _n(v, d=0):
    v = float(v or 0)
    return f"{v:,.{d}f}"


def _nice_max(v, ticks=4):
    """Axis max that splits into `ticks` round steps (1, 2, 5 x 10^k)."""
    if v <= 0:
        return ticks
    raw = v / ticks
    e = 10 ** math.floor(math.log10(raw))
    step = next(m * e for m in (1, 2, 5, 10) if raw <= m * e)
    return step * ticks


def _demand_chart(ser: pd.DataFrame, events: dict, unit: str, c: dict, W=880, H=250) -> str:
    """Weekly demand: actual history, forecast P50 with the P80 band, the 2 allocated weeks shaded, events marked."""
    L, R, T, B = 46, 14, 26, 34
    ser = ser.sort_values("wk").reset_index(drop=True)
    n = len(ser)
    if n == 0:
        return ""
    ymax = _nice_max(max(ser["units"].max(skipna=True) or 0, ser["p80"].max(skipna=True) or 0) * 1.08)
    x = lambda i: L + (W - L - R) * (i / max(n - 1, 1))
    y = lambda v: T + (H - T - B) * (1 - float(v) / ymax)
    fut = ser.index[ser["p50"].notna()].tolist()
    hist = ser.index[ser["units"].notna()].tolist()
    out = [f'<svg viewBox="0 0 {W} {H}" width="100%" role="img" aria-label="Weekly demand, actual and forecast">']
    for k in range(5):                                                       # grid + y labels
        v = ymax * k / 4
        out.append(f'<line x1="{L}" x2="{W-R}" y1="{y(v):.1f}" y2="{y(v):.1f}" stroke="{c["line"]}" stroke-width="1"/>')
        out.append(f'<text x="{L-8}" y="{y(v)+4:.1f}" text-anchor="end" class="ax">{_n(v)}</text>')
    if fut:
        f0 = fut[0]
        cov = [i for i in fut[:2]]
        xa, xb = x(cov[0]) - (x(1) - x(0)) / 2, x(cov[-1]) + (x(1) - x(0)) / 2
        out.append(f'<rect x="{xa:.1f}" y="{T}" width="{xb-xa:.1f}" height="{H-T-B}" fill="{c["accent_soft"]}"/>')
        out.append(f'<text x="{xb+6:.1f}" y="{H-B-8}" class="tag" fill="{c["accent"]}">← 2 weeks allocated</text>')
        xt = x(f0) - (x(1) - x(0)) / 2
        out.append(f'<line x1="{xt:.1f}" x2="{xt:.1f}" y1="{T}" y2="{H-B}" stroke="{c["faint"]}" stroke-dasharray="3 3"/>')
        out.append(f'<text x="{xt-6:.1f}" y="{H-B-8}" text-anchor="end" class="ax">today</text>')
        # P80 band (between P50 and P80) and the P50 line, joined to the last actual week
        top = " ".join(f"{x(i):.1f},{y(ser.at[i,'p80']):.1f}" for i in fut)
        bot = " ".join(f"{x(i):.1f},{y(ser.at[i,'p50']):.1f}" for i in reversed(fut))
        out.append(f'<polygon points="{top} {bot}" fill="{c["band"]}" fill-opacity=".14"/>')
        p50 = ([f"{x(hist[-1]):.1f},{y(ser.at[hist[-1],'units']):.1f}"] if hist else []) + \
              [f"{x(i):.1f},{y(ser.at[i,'p50']):.1f}" for i in fut]
        out.append(f'<polyline points="{" ".join(p50)}" fill="none" stroke="{c["accent"]}" stroke-width="2.4" stroke-dasharray="6 4"/>')
        first = {}                                                           # event pins; labels by importance
        for i in fut:
            names = [e.strip() for e in str(events.get(str(pd.Timestamp(ser.at[i, "wk"]).date()), "")).split(",") if e.strip()]
            if names:
                out.append(f'<circle cx="{x(i):.1f}" cy="{y(ser.at[i,"p80"])-9:.1f}" r="3.5" fill="{c["warm"]}"/>')
            for e in names:
                first.setdefault(e, i)
        placed = []
        for e in sorted(first, key=lambda e: EVENT_RANK.index(e) if e in EVENT_RANK else len(EVENT_RANK)):
            xi = x(first[e])
            if all(abs(xi - p) > 80 for p in placed):                        # skip labels that would collide (all listed left)
                out.append(f'<text x="{min(max(xi, L + 40), W - R - 40):.1f}" y="{y(ser.at[first[e],"p80"])-17:.1f}" '
                           f'text-anchor="middle" class="ev">{html.escape(e)}</text>')
                placed.append(xi)
    if hist:
        pts = " ".join(f"{x(i):.1f},{y(ser.at[i,'units']):.1f}" for i in hist)
        out.append(f'<polyline points="{pts}" fill="none" stroke="{c["ink"]}" stroke-width="2"/>')
        i = hist[-1]
        out.append(f'<circle cx="{x(i):.1f}" cy="{y(ser.at[i,"units"]):.1f}" r="3.5" fill="{c["ink"]}"/>')
    seen = set()
    for i in range(n):                                                       # month labels
        wk = pd.Timestamp(ser.at[i, "wk"])
        if wk.strftime("%b%y") not in seen and wk.day <= 7:
            seen.add(wk.strftime("%b%y"))
            out.append(f'<text x="{x(i):.1f}" y="{H-12}" text-anchor="middle" class="ax">{wk.strftime("%b")}</text>')
    out.append("</svg>")
    return "".join(out)


def _size_chart(sr: pd.DataFrame, unit: str, c: dict, W=1180, H=210) -> str:
    """Per size, stacked: on shelf (within the pack, then above it), in transit, sent now; the pack as a marker line."""
    L, R, T, B = 34, 8, 24, 46
    sr = sr.copy()
    sr["k"] = sr["size"].map(_size_key)
    sr = sr.sort_values("k").reset_index(drop=True)
    n = len(sr)
    if n == 0:
        return ""
    shelf = sr["atp"].clip(lower=0)
    tot = shelf + sr["intransit"].clip(lower=0) + sr["alloc_qty"]
    ymax = _nice_max(max(tot.max(), sr["target"].max(), 1) * 1.12, ticks=4)
    bw = (W - L - R) / n
    y = lambda v: T + (H - T - B) * (1 - float(v) / ymax)
    out = [f'<svg viewBox="0 0 {W} {H}" width="100%" role="img" aria-label="Store pack by size">',
]
    for k in range(5):
        v = ymax * k / 4
        out.append(f'<line x1="{L}" x2="{W-R}" y1="{y(v):.1f}" y2="{y(v):.1f}" stroke="{c["line"]}"/>')
        out.append(f'<text x="{L-6}" y="{y(v)+4:.1f}" text-anchor="end" class="ax">{_n(v)}</text>')
    for i, r in sr.iterrows():
        w = min(bw * 0.62, 46)
        x0 = L + i * bw + (bw - w) / 2
        base = 0.0
        within = min(shelf[i], max(r["target"], 0))
        for val, fill in ((within, c["shelf"]), (shelf[i] - within, c["over"]),
                          (max(r["intransit"], 0), c["transit"]), (r["alloc_qty"], c["accent"])):
            if val > 0:
                out.append(f'<rect x="{x0:.1f}" y="{y(base+val):.1f}" width="{w:.1f}" height="{y(base)-y(base+val):.1f}" '
                           f'fill="{fill}" rx="1.5"/>')
                base += val
        if r["target"] > 0:                                                  # store pack marker
            out.append(f'<line x1="{x0-5:.1f}" x2="{x0+w+5:.1f}" y1="{y(r["target"]):.1f}" y2="{y(r["target"]):.1f}" '
                       f'stroke="{c["warm"]}" stroke-width="2.6"/>')
        if r["alloc_qty"] > 0:
            out.append(f'<text x="{x0+w/2:.1f}" y="{y(base)-6:.1f}" text-anchor="middle" class="send">+{int(r["alloc_qty"])}</text>')
        elif r["need"] > r["alloc_qty"]:
            out.append(f'<text x="{x0+w/2:.1f}" y="{y(max(base, r["target"]))-6:.1f}" text-anchor="middle" class="miss">'
                       f'−{int(r["need"])}</text>')
        dim = r["target"] == 0 and base == 0
        out.append(f'<text x="{x0+w/2:.1f}" y="{H-B+16}" text-anchor="middle" class="{"szd" if dim else "sz"}">'
                   f'{html.escape(str(r["size"]))}</text>')
        if int(r.get("key_size", 0) or 0) == 1:
            out.append(f'<circle cx="{x0+w/2:.1f}" cy="{H-B+26}" r="2.5" fill="{c["dim"]}"/>')
    out.append("</svg>")
    return "".join(out)


def build(sr: pd.DataFrame, ser: pd.DataFrame, events: dict, theme: str = "light", image: str | None = None,
          colour: str | None = None) -> str:
    """sr = al_size_run rows for one store x style; ser = weekly actual + forecast for the style in the country."""
    c = THEMES.get(theme, THEMES["light"])
    for col in ("cover_units", "store_share", "size_share", "demand", "p_sell", "target", "atp", "intransit", "need",
                "alloc_qty", "key_size"):
        sr[col] = pd.to_numeric(sr[col], errors="coerce").fillna(0)
    for col in ("units", "p50", "p80"):
        ser[col] = pd.to_numeric(ser[col], errors="coerce")
    ser["wk"] = pd.to_datetime(ser["wk"])
    r0 = sr.iloc[0]
    unit = "pairs" if str(r0["cluster"]).upper().startswith("FOOTWEAR") else "units"
    country = COUNTRY.get(r0["country"], r0["country"])
    style = style_label(r0.get("style_name") or r0["parent"])
    store = str(r0["store_name"]).title()
    fc2, fc8 = float(r0["cover_units"]), float(ser["p50"].sum(skipna=True))
    store_dem = fc2 * float(r0["store_share"])
    pack = int(sr["target"].sum())
    shelf, transit = int(sr["atp"].clip(lower=0).sum()), int(sr["intransit"].clip(lower=0).sum())
    send = int(sr["alloc_qty"].sum())
    over = int((sr["atp"].clip(lower=0) + sr["intransit"].clip(lower=0) - sr["target"]).clip(lower=0).sum())
    short = int((sr["need"] - sr["alloc_qty"]).clip(lower=0).sum())
    sizes_sent = sr[sr["alloc_qty"] > 0]
    src = ", ".join(sorted({s for s in sr["source"].dropna().astype(str) if s}))
    n_sizes = int((sr["target"] > 0).sum())
    cl = [p.strip().title() for p in str(r0["cluster"]).split("·")]

    # the worked example: the size with the most to send (else the biggest gap)
    ex = (sizes_sent.sort_values("alloc_qty", ascending=False) if len(sizes_sent) else
          sr.sort_values("need", ascending=False)).iloc[0]
    have = max(ex["atp"], 0) + max(ex["intransit"], 0)
    steps = [
        f"{country} is expected to sell <b>{_n(fc2)} {unit}</b> of this style in the next 2 weeks.",
        f"{store} sells <b>{ex['store_share']*100:.0f}%</b> of {country}'s {cl[2].lower()} {cl[0].lower()} "
        f"→ <b>{_n(store_dem, 1)} {unit}</b> here.",
        f"Size {html.escape(str(ex['size']))} is <b>{ex['size_share']*100:.0f}%</b> of the {cl[2].lower()} "
        f"{cl[1].lower()} size curve → "
        f"<b>{_n(ex['demand'], 1)} {unit}</b>, a {ex['p_sell']*100:.0f}% chance of selling at least one.",
        f"To cover 8 in 10 likely outcomes the store should hold <b>{int(ex['target'])}</b>; it has <b>{int(have)}</b>.",
        (f"Send <b>{int(ex['alloc_qty'])}</b> from {html.escape(str(ex['source']))}." if ex["alloc_qty"] > 0 else
         f"It needs <b>{int(ex['need'])}</b>, but no DC holds this size — a buying signal, not a transfer."),
    ]
    status_txt = (f"Send {send} {unit}" + (f" · {src}" if src else "")) if send else "Nothing to send — DC empty"
    ev_next = list(dict.fromkeys(e.strip() for k, v in sorted(events.items()) if v
                                 and pd.Timestamp(k) >= ser.loc[ser["p50"].notna(), "wk"].min() for e in str(v).split(",")))

    css = f"""
    @import url('https://fonts.googleapis.com/css2?family=Source+Sans+3:wght@400;600;700&display=swap');
    * {{ box-sizing:border-box; margin:0; }}
    body {{ background:{c['bg']}; color:{c['ink']}; font:14px/1.5 'Source Sans 3', system-ui, sans-serif; padding:4px 2px; }}
    .hd {{ display:flex; justify-content:space-between; align-items:flex-start; gap:16px; flex-wrap:wrap; }}
    .who {{ display:flex; gap:16px; align-items:center; min-width:0; }}
    .prod {{ width:96px; height:96px; object-fit:contain; background:#FFFFFF; border:1px solid {c['line']}; border-radius:12px; padding:4px; }}
    .eyebrow {{ font-size:11px; letter-spacing:.12em; font-weight:700; color:{c['dim']}; text-transform:uppercase; }}
    h1 {{ font:600 26px/1.2 Georgia, serif; margin-top:4px; }}
    .meta {{ color:{c['dim']}; margin-top:4px; }}
    .chip {{ display:inline-block; border:1px solid {c['line']}; border-radius:999px; padding:1px 9px; margin-right:4px;
             font-size:12px; color:{c['dim']}; }}
    .pill {{ background:{c['accent'] if send else c['warm_soft']}; color:{'#FFFFFF' if send else c['warm']};
             font-weight:700; border-radius:10px; padding:9px 14px; white-space:nowrap; font-size:15px; }}
    .lvl {{ border:1px solid {c['line']}; border-radius:14px; padding:14px 16px 10px; margin-top:16px; }}
    .lvlh {{ display:flex; align-items:baseline; gap:10px; flex-wrap:wrap; }}
    .lvlh .tag {{ font-size:11px; font-weight:700; letter-spacing:.1em; text-transform:uppercase; color:{c['bg']};
                  background:{c['ink']}; border-radius:6px; padding:2px 8px; }}
    .lvlh h2 {{ font:600 17px/1.3 'Source Sans 3', sans-serif; }}
    .lvlh .sub {{ color:{c['dim']}; font-size:13px; }}
    .country {{ display:grid; grid-template-columns:220px 1fr; gap:18px; margin-top:8px; align-items:start; }}
    .big {{ font-size:34px; font-weight:700; line-height:1.1; font-variant-numeric:tabular-nums; }}
    .big small {{ font-size:15px; font-weight:600; margin-left:4px; }}
    .kv {{ margin-top:10px; display:grid; gap:6px; font-size:13px; color:{c['dim']}; }}
    .kv b {{ color:{c['ink']}; font-variant-numeric:tabular-nums; }}
    .link {{ display:flex; align-items:center; gap:12px; margin:10px 0 0 26px; color:{c['ink']}; font-size:14px; }}
    .link .rail {{ width:2px; height:34px; background:{c['accent']}; border-radius:2px; }}
    .link .share {{ background:{c['accent_soft']}; color:{c['accent']}; font-weight:700; border-radius:8px; padding:4px 10px; }}
    .steps {{ display:grid; grid-template-columns:1fr 18px 1fr 18px 1fr 18px 1fr; align-items:stretch; margin-top:10px; }}
    .arrow {{ display:flex; align-items:center; justify-content:center; color:{c['faint']}; font-size:16px; }}
    .step {{ background:{c['card']}; border:1px solid {c['line']}; border-radius:12px; padding:11px 14px; min-width:0; }}
    .step.go {{ background:{c['accent']}; border-color:{c['accent']}; color:#FFFFFF; }}
    .step .n {{ font-size:11px; font-weight:700; letter-spacing:.1em; color:{c['dim']}; text-transform:uppercase; }}
    .step.go .n, .step.go .s {{ color:rgba(255,255,255,.88); }}
    .step .v {{ font-size:26px; font-weight:700; line-height:1.15; margin-top:3px; font-variant-numeric:tabular-nums; }}
    .step .v small {{ font-size:14px; font-weight:600; margin-left:3px; }}
    .step .s {{ font-size:12.5px; color:{c['dim']}; margin-top:2px; }}
    .legend {{ display:flex; flex-wrap:wrap; gap:14px; font-size:12px; color:{c['dim']}; margin:12px 0 0; }}
    .legend i {{ display:inline-block; width:14px; height:3px; vertical-align:middle; margin-right:5px; border-radius:2px; }}
    .legend i.blk {{ height:10px; width:10px; border-radius:2px; }}
    .ctitle {{ font-weight:600; margin-top:12px; }}
    svg .ax {{ font-size:11px; fill:{c['dim']}; }}
    svg .tag {{ font-size:11px; font-weight:700; }}
    svg .ev {{ font-size:10.5px; fill:{c['warm']}; font-weight:600; }}
    svg .sz {{ font-size:12px; fill:{c['ink']}; font-weight:600; }}
    svg .szd {{ font-size:12px; fill:{c['faint']}; }}
    svg .send {{ font-size:12px; fill:{c['accent']}; font-weight:700; }}
    svg .miss {{ font-size:11.5px; fill:{c['warm']}; font-weight:700; }}
    .how {{ margin-top:10px; background:{c['card']}; border-radius:12px; padding:11px 16px; }}
    .how ol {{ padding-left:20px; margin-top:6px; display:grid; gap:2px; }}
    @media (max-width: 900px) {{ .steps {{ grid-template-columns:1fr; gap:8px; }} .arrow {{ display:none; }}
                                .country {{ grid-template-columns:1fr; }} }}
    """
    stock_s = f"on shelf {shelf} · in transit {transit}"
    over_s = f"{over} above the pack — can move to other stores" if over > 0 else "nothing above the pack"
    send_s = (f"to {len(sizes_sent)} size{'s' if len(sizes_sent) != 1 else ''} from {html.escape(src)}" if send else "none in any DC")
    if short:
        send_s += f" · {short} more wanted, DC empty"
    seg = f"{cl[2].lower()} {cl[0].lower()}"
    ev_s = html.escape(", ".join(ev_next)) if ev_next else "none in the next 8 weeks"
    return f"""<html><head><meta charset="utf-8"><style>{css}</style></head><body>
    <div class="hd">
      <div class="who">{f'<img class="prod" src="{html.escape(image)}" alt="{html.escape(style)}">' if image else ''}
        <div><div class="eyebrow">Store walkthrough</div>
        <h1>{html.escape(style)}</h1>
        <div class="meta">{html.escape(str(r0['parent']))} &nbsp; {''.join(f'<span class="chip">{html.escape(p)}</span>' for p in cl + ([colour] if colour else []))}</div></div></div>
      <div class="pill">{html.escape(status_txt)}</div>
    </div>

    <div class="lvl">
      <div class="lvlh"><span class="tag">{html.escape(country)}</span><h2>Country demand for this style</h2>
        <span class="sub">all {html.escape(country)} Nike stores · weekly, actual and forecast</span></div>
      <div class="country">
        <div><div class="eyebrow">Next 2 weeks</div>
          <div class="big">{_n(fc2)}<small>{unit}</small></div>
          <div class="kv"><span>8-week forecast <b>{_n(fc8)} {unit}</b></span>
            <span>Allocation plans for the first <b>2 weeks</b> (shaded)</span>
            <span>Events ahead: <b>{ev_s}</b></span></div></div>
        <div><div class="legend" style="margin-top:0"><span><i style="background:{c['ink']}"></i>Actual</span>
            <span><i style="background:{c['accent']}"></i>Forecast (most likely)</span>
            <span><i class="blk" style="background:{c['band']};opacity:.25"></i>High case (8 in 10 weeks below)</span></div>
          {_demand_chart(ser, events, unit, c)}</div>
      </div>
    </div>

    <div class="link"><div class="rail"></div>
      <span>{html.escape(store)} sells <span class="share">{float(r0['store_share'])*100:.0f}%</span> of {html.escape(country)}'s {html.escape(seg)}
      → <b>{_n(store_dem)} {unit}</b> expected here in the next 2 weeks</span></div>

    <div class="lvl">
      <div class="lvlh"><span class="tag">Store</span><h2>{html.escape(store)}</h2>
        <span class="sub">grade {html.escape(str(r0['grade']))} · all sizes of this style</span></div>
      <div class="steps">
        <div class="step"><div class="n">Store demand</div><div class="v">{_n(store_dem)}<small>{unit}</small></div>
          <div class="s">next 2 weeks · split across sizes by the {html.escape(cl[2].lower())} {html.escape(cl[1].lower())} size curve</div></div>
        <div class="arrow">→</div>
        <div class="step"><div class="n">Store pack</div><div class="v">{pack}<small>{unit}</small></div>
          <div class="s">what it should hold · {n_sizes} sizes · covers 8 in 10 likely outcomes</div></div>
        <div class="arrow">→</div>
        <div class="step"><div class="n">In stock</div><div class="v">{shelf + transit}<small>{unit}</small></div>
          <div class="s">{html.escape(stock_s)} · {html.escape(over_s)}</div></div>
        <div class="arrow">→</div>
        <div class="step {'go' if send else ''}"><div class="n">Send</div><div class="v">{send}<small>{unit}</small></div>
          <div class="s">{send_s}</div></div>
      </div>
      <div class="ctitle">Pack by size — what the store has against what it should hold</div>
      <div class="legend"><span><i class="blk" style="background:{c['shelf']}"></i>On shelf (within pack)</span>
        <span><i class="blk" style="background:{c['over']}"></i>On shelf, above pack</span>
        <span><i class="blk" style="background:{c['transit']}"></i>In transit</span>
        <span><i class="blk" style="background:{c['accent']}"></i>Sent now</span>
        <span><i style="background:{c['warm']};height:3px"></i>Pack (should hold)</span>
        {f'<span><b style="color:{c["warm"]}">−n</b> wanted, no DC stock</span>' if short else ''}
        <span>● key size</span></div>
      {_size_chart(sr, unit, c)}
      <div class="how"><div class="eyebrow">How size {html.escape(str(ex['size']))} was worked out</div>
        <ol>{''.join(f'<li>{x}</li>' for x in steps)}</ol></div>
    </div>
    </body></html>"""


def render(sr: pd.DataFrame, ser: pd.DataFrame, events: dict, theme: str = "light", height: int = 1210,
           image: str | None = None, colour: str | None = None):
    components.html(build(sr, ser, events, theme, image, colour), height=height, scrolling=False)
