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
                  shelf="#20274C", transit="#8A96B8", ok="#12704D", ok_soft="#E3F3EA"),
    "dark": dict(bg="#0A0D16", card="#111626", line="#252E48", ink="#E5E9F4", dim="#8790AB", faint="#4A5372",
                 accent="#5B84FF", accent_soft="#17234A", band="#5B84FF", warm="#E08A6E", warm_soft="#3A2219",
                 shelf="#C9D0E4", transit="#6B7699", ok="#5FD49A", ok_soft="#123024"),
}
COUNTRY = {"AE": "UAE", "SA": "KSA", "KW": "Kuwait", "QA": "Qatar", "BH": "Bahrain", "OM": "Oman", "EG": "Egypt"}


def _size_key(s: str):
    s = str(s).strip().upper()
    try:
        return (0, float(s.rstrip("YCK")), s)
    except ValueError:
        return (1, APPAREL_ORDER.index(s), s) if s in APPAREL_ORDER else (2, 0, s)


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


def _demand_chart(ser: pd.DataFrame, events: dict, unit: str, c: dict, W=660, H=270) -> str:
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
        out.append(f'<text x="{(xa+xb)/2:.1f}" y="{T-8}" text-anchor="middle" class="tag" fill="{c["accent"]}">2 weeks allocated</text>')
        xt = x(f0) - (x(1) - x(0)) / 2
        out.append(f'<line x1="{xt:.1f}" x2="{xt:.1f}" y1="{T}" y2="{H-B}" stroke="{c["faint"]}" stroke-dasharray="3 3"/>')
        out.append(f'<text x="{xt-6:.1f}" y="{H-B-6}" text-anchor="end" class="ax">today</text>')
        # P80 band (between P50 and P80) and the P50 line, joined to the last actual week
        top = " ".join(f"{x(i):.1f},{y(ser.at[i,'p80']):.1f}" for i in fut)
        bot = " ".join(f"{x(i):.1f},{y(ser.at[i,'p50']):.1f}" for i in reversed(fut))
        out.append(f'<polygon points="{top} {bot}" fill="{c["band"]}" fill-opacity=".14"/>')
        p50 = ([f"{x(hist[-1]):.1f},{y(ser.at[hist[-1],'units']):.1f}"] if hist else []) + \
              [f"{x(i):.1f},{y(ser.at[i,'p50']):.1f}" for i in fut]
        out.append(f'<polyline points="{" ".join(p50)}" fill="none" stroke="{c["accent"]}" stroke-width="2.4" stroke-dasharray="6 4"/>')
        labelled, last_x, row = set(), -999, 0                               # event pins: one label per event
        for i in fut:
            names = [e.strip() for e in str(events.get(str(pd.Timestamp(ser.at[i, "wk"]).date()), "")).split(",") if e.strip()]
            if not names:
                continue
            out.append(f'<circle cx="{x(i):.1f}" cy="{y(ser.at[i,"p80"])-9:.1f}" r="3.5" fill="{c["warm"]}"/>')
            new = [e for e in names if e not in labelled]
            if new and x(i) - last_x > 34:
                row = 0 if x(i) - last_x > 90 else 1 - row
                out.append(f'<text x="{min(x(i), W - R - 4):.1f}" y="{y(ser.at[i,"p80"])-17-row*12:.1f}" text-anchor="end" '
                           f'class="ev">{html.escape(new[0])}</text>')
                labelled.update(new); last_x = x(i)
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


def _size_chart(sr: pd.DataFrame, unit: str, c: dict, W=470, H=270) -> str:
    """Per size: on shelf + in transit + to send stacked, the store pack as a target marker."""
    L, R, T, B = 34, 8, 24, 46
    sr = sr.copy()
    sr["k"] = sr["size"].map(_size_key)
    sr = sr.sort_values("k").reset_index(drop=True)
    n = len(sr)
    if n == 0:
        return ""
    shelf = sr["atp"].clip(lower=0)
    tot = shelf + sr["intransit"].clip(lower=0) + sr["alloc_qty"]
    ymax = _nice_max(max(tot.max(), sr["target"].max(), 1) * 1.15, ticks=2)
    bw = (W - L - R) / n
    y = lambda v: T + (H - T - B) * (1 - float(v) / ymax)
    out = [f'<svg viewBox="0 0 {W} {H}" width="100%" role="img" aria-label="Store pack by size">',
           '<defs><pattern id="hatch" width="5" height="5" patternUnits="userSpaceOnUse" patternTransform="rotate(45)">'
           f'<rect width="5" height="5" fill="{c["transit"]}" fill-opacity=".25"/>'
           f'<line x1="0" y1="0" x2="0" y2="5" stroke="{c["transit"]}" stroke-width="2"/></pattern></defs>']
    for k in range(3):
        v = ymax * k / 2
        out.append(f'<line x1="{L}" x2="{W-R}" y1="{y(v):.1f}" y2="{y(v):.1f}" stroke="{c["line"]}"/>')
        out.append(f'<text x="{L-6}" y="{y(v)+4:.1f}" text-anchor="end" class="ax">{_n(v)}</text>')
    for i, r in sr.iterrows():
        x0 = L + i * bw + bw * 0.18
        w = bw * 0.64
        base = 0.0
        for val, fill in ((shelf[i], c["shelf"]), (max(r["intransit"], 0), "url(#hatch)"), (r["alloc_qty"], c["accent"])):
            if val > 0:
                out.append(f'<rect x="{x0:.1f}" y="{y(base+val):.1f}" width="{w:.1f}" height="{y(base)-y(base+val):.1f}" '
                           f'fill="{fill}" rx="1.5"/>')
                base += val
        if r["target"] > 0:                                                  # store pack marker
            out.append(f'<line x1="{x0-3:.1f}" x2="{x0+w+3:.1f}" y1="{y(r["target"]):.1f}" y2="{y(r["target"]):.1f}" '
                       f'stroke="{c["warm"]}" stroke-width="2.4"/>')
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


def build(sr: pd.DataFrame, ser: pd.DataFrame, events: dict, theme: str = "light") -> str:
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
    style = " ".join(w if (len(w) <= 2 or any(ch.isdigit() for ch in w)) else w.title()
                     for w in str(r0.get("style_name") or r0["parent"]).split())
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
    .eyebrow {{ font-size:11px; letter-spacing:.12em; font-weight:700; color:{c['dim']}; text-transform:uppercase; }}
    h1 {{ font:600 26px/1.2 Georgia, serif; margin-top:4px; }}
    .meta {{ color:{c['dim']}; margin-top:4px; }}
    .chip {{ display:inline-block; border:1px solid {c['line']}; border-radius:999px; padding:1px 9px; margin-right:4px;
             font-size:12px; color:{c['dim']}; }}
    .pill {{ background:{c['accent'] if send else c['warm_soft']}; color:{'#FFFFFF' if send else c['warm']};
             font-weight:700; border-radius:10px; padding:9px 14px; white-space:nowrap; font-size:15px; }}
    .steps {{ display:grid; grid-template-columns:1fr 18px 1fr 18px 1fr 18px 1fr; align-items:stretch; margin-top:18px; }}
    .arrow {{ display:flex; align-items:center; justify-content:center; color:{c['faint']}; font-size:16px; }}
    .step {{ background:{c['card']}; border:1px solid {c['line']}; border-radius:12px; padding:12px 14px; min-width:0; }}
    .step.go {{ background:{c['accent']}; border-color:{c['accent']}; color:#FFFFFF; }}
    .step .n {{ font-size:11px; font-weight:700; letter-spacing:.1em; color:{c['dim']}; text-transform:uppercase; }}
    .step.go .n, .step.go .s {{ color:rgba(255,255,255,.85); }}
    .step .v {{ font-size:28px; font-weight:700; line-height:1.15; margin-top:4px; font-variant-numeric:tabular-nums; }}
    .step .v small {{ font-size:14px; font-weight:600; margin-left:3px; }}
    .step .s {{ font-size:12.5px; color:{c['dim']}; margin-top:2px; }}
    .grid {{ display:grid; grid-template-columns:1.4fr 1fr; gap:14px; margin-top:16px; }}
    .panel {{ border:1px solid {c['line']}; border-radius:12px; padding:12px 14px 8px; min-width:0; }}
    .panel h2 {{ font:600 15px/1.3 'Source Sans 3', sans-serif; }}
    .panel .sub {{ color:{c['dim']}; font-size:12.5px; }}
    .legend {{ display:flex; flex-wrap:wrap; gap:12px; font-size:12px; color:{c['dim']}; margin:6px 0 2px; }}
    .legend i {{ display:inline-block; width:14px; height:3px; vertical-align:middle; margin-right:5px; border-radius:2px; }}
    .legend i.blk {{ height:10px; width:10px; border-radius:2px; }}
    svg .ax {{ font-size:11px; fill:{c['dim']}; }}
    svg .tag {{ font-size:11px; font-weight:700; }}
    svg .ev {{ font-size:10.5px; fill:{c['warm']}; font-weight:600; }}
    svg .sz {{ font-size:11.5px; fill:{c['ink']}; font-weight:600; }}
    svg .szd {{ font-size:11.5px; fill:{c['faint']}; }}
    svg .send {{ font-size:11.5px; fill:{c['accent']}; font-weight:700; }}
    svg .miss {{ font-size:11px; fill:{c['warm']}; font-weight:700; }}
    .how {{ margin-top:14px; background:{c['card']}; border-radius:12px; padding:12px 16px; }}
    .how ol {{ padding-left:20px; margin-top:6px; display:grid; gap:3px; }}
    .how b {{ font-weight:700; }}
    @media (max-width: 900px) {{ .steps {{ grid-template-columns:1fr; gap:8px; }} .arrow {{ display:none; }}
                                .grid {{ grid-template-columns:1fr; }} }}
    """
    pack_s = f"{n_sizes} sizes · covers 8 in 10 likely outcomes over 2 weeks"
    stock_s = f"on shelf {shelf} · in transit {transit}" + (f" · {over} above pack, can move to other stores" if over > 0 else "")
    send_s = (f"to {len(sizes_sent)} size{'s' if len(sizes_sent) != 1 else ''} from {html.escape(src)}" if send else "none in DC")
    if short:
        send_s += f" · {short} more wanted, DC empty"
    return f"""<html><head><meta charset="utf-8"><style>{css}</style></head><body>
    <div class="hd">
      <div><div class="eyebrow">Store walkthrough · {html.escape(country)}</div>
        <h1>{html.escape(style)}</h1>
        <div class="meta">{html.escape(str(r0['parent']))} &nbsp; {''.join(f'<span class="chip">{html.escape(p)}</span>' for p in cl)}
          &nbsp; <b>{html.escape(store)}</b> · grade {html.escape(str(r0['grade']))}</div></div>
      <div class="pill">{html.escape(status_txt)}</div>
    </div>
    <div class="steps">
      <div class="step"><div class="n">1 · Forecast</div><div class="v">{_n(fc2)}<small>{unit}</small></div>
        <div class="s">{html.escape(country)} · next 2 weeks (8-week view: {_n(fc8)})</div></div>
      <div class="arrow">→</div>
      <div class="step"><div class="n">2 · Store pack</div><div class="v">{pack}<small>{unit}</small></div>
        <div class="s">{html.escape(pack_s)}</div></div>
      <div class="arrow">→</div>
      <div class="step"><div class="n">3 · In stock</div><div class="v">{shelf + transit}<small>{unit}</small></div>
        <div class="s">{html.escape(stock_s)}</div></div>
      <div class="arrow">→</div>
      <div class="step {'go' if send else ''}"><div class="n">4 · Send</div><div class="v">{send}<small>{unit}</small></div>
        <div class="s">{send_s}</div></div>
    </div>
    <div class="grid">
      <div class="panel"><h2>{html.escape(country)} weekly demand · actual and 8-week forecast</h2>
        <div class="sub">Forecast follows seasonality and events{(' — next up: ' + html.escape(', '.join(ev_next))) if ev_next else ''}</div>
        <div class="legend"><span><i style="background:{c['ink']}"></i>Actual</span>
          <span><i style="background:{c['accent']}"></i>Forecast (most likely)</span>
          <span><i class="blk" style="background:{c['band']};opacity:.25"></i>High case (8 in 10 weeks below)</span></div>
        {_demand_chart(ser, events, unit, c)}</div>
      <div class="panel"><h2>{html.escape(store)} · pack by size</h2>
        <div class="sub">Bar = what the store will have; line = what it should hold</div>
        <div class="legend"><span><i class="blk" style="background:{c['shelf']}"></i>On shelf</span>
          <span><i class="blk" style="background:{c['transit']};opacity:.6"></i>In transit</span>
          <span><i class="blk" style="background:{c['accent']}"></i>Send</span>
          <span><i style="background:{c['warm']}"></i>Store pack</span>
          {f'<span style="color:{c["warm"]};font-weight:700">−n</span><span style="margin-left:-8px">&nbsp;wanted, no DC stock</span>' if short else ''}
          <span>● key size</span></div>
        {_size_chart(sr, unit, c)}</div>
    </div>
    <div class="how"><div class="eyebrow">How size {html.escape(str(ex['size']))} was worked out</div>
      <ol>{''.join(f'<li>{s}</li>' for s in steps)}</ol></div>
    </body></html>"""


def render(sr: pd.DataFrame, ser: pd.DataFrame, events: dict, theme: str = "light", height: int = 860):
    components.html(build(sr, ser, events, theme), height=height, scrolling=False)
