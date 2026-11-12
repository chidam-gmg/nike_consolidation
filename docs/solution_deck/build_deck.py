"""Solution architecture deck for the Nike stock movement solution (Databricks + Genie + Claude).
python3 build_deck.py  ->  nike_stock_movement_solution_architecture.html (+ .pdf via headless Chrome)."""
import html, json, pathlib, subprocess

HERE = pathlib.Path(__file__).parent
H = "https://dbc-74c28e5e-272e.cloud.databricks.com"
O = "?o=8517174019523704"
FQ = "sports_analytics_test.nike_consolidation"
DATE = "06 Nov 2026"
e = html.escape

# ---------------- links ----------------
def tbl(t):
    full = t if t.count(".") == 2 else f"{FQ}.{t}"
    return f"{H}/explore/data/{full.replace('.', '/')}{O}"
def nb(i): return f"{H}/editor/notebooks/{i}{O}"
def job(i): return f"{H}/jobs/{i}{O}"
def genie(i): return f"{H}/genie/rooms/{i}{O}"
def folder(i): return f"{H}/browse/folders/{i}{O}"
def fn(name): return f"{H}/explore/data/functions/sports_analytics_test/nike_consolidation/{name}{O}"
def A(url, text, cls=""): return f'<a href="{e(url)}"{f" class={chr(34)}{cls}{chr(34)}" if cls else ""}>{text}</a>'
def T(t, short=None): return A(tbl(t), f"<code>{e(short or t)}</code>")
def C(x): return f"<code>{e(x)}</code>"

APP_LIVE = "https://nike-consolidation-8517174019523704.aws.databricksapps.com"
APP_DEV = "https://nike-consolidation-dev-8517174019523704.aws.databricksapps.com"
APP_SET = f"{H}/apps/nike-consolidation{O}"
APP_DEV_SET = f"{H}/apps/nike-consolidation-dev{O}"
WH = f"{H}/sql/warehouses/1c9706d9594f1f2f{O}"
SCHEMA = f"{H}/explore/data/sports_analytics_test/nike_consolidation{O}"
REPO = "https://github.com/chidam-gmg/nike_consolidation"
JOB = job(553755400746008)
LAST_RUN = f"{H}/?o=8517174019523704#job/553755400746008/run/709356959614185"
SP = {"pipeline": "01f1d9a620be1fe189c4897e4ace2ac1", "consolidation": "01f1d9a6214c10fbad697e7e5c7f5b9a",
      "replenishment": "01f1d9a621dd1f2287c5dd3baa3a2585", "merch": "01f1d371857d11438cd5701cb35f9a2c",
      "cxo": "01f0e59a14701f63a1b948133d8c0c86"}
ART = {"genie_doc": "https://claude.ai/artifact/4sDtU6RvqMYC94TPEPqnTk", "sysmap": "https://claude.ai/artifact/7bY5XpisQmS3MVzEkKqset",
       "brief": "https://claude.ai/artifact/AJavutSDtrB3Tjbz1vUG1o", "handover": "https://claude.ai/artifact/PuUESeft3bcenRNA8BRh3U",
       "agentlab": "https://claude.ai/artifact/T5tueSp46a2GYUj5CVrHzr", "lifecycle": "https://claude.ai/artifact/LvMjA2iG6bsQemQMMnQ9nz"}
NBID = {"00_config_validation": 2208784873396675, "01_effective_stock": 2208784873396676, "02_ros_cover_classify": 2208784873396677,
        "03_pools_guards": 2208784873396678, "04_match_size": 2208784873396679, "05_size_curve": 2208784873396680,
        "06_score_output": 2208784873396681, "07_history_archive": 513551203851668, "08_receiver_guard": 3380146532593582,
        "10_replen_demand": 2278953126573161, "11_replen_ration": 2278953126573162, "12_replen_output": 2278953126573163,
        "13_replen_history": 2278953126573164}
MCP_GENIE = lambda sid: f"{H}/api/2.0/mcp/genie/{sid}"
MCP_FN = f"{H}/api/2.0/mcp/functions/sports_analytics_test/nike_consolidation"

# ---------------- slide machinery ----------------
SLIDES = []
SECTIONS = []


def section(name):
    SECTIONS.append((name, len(SLIDES) + 1))


def slide(title, body, kicker=None, cls=""):
    sec = SECTIONS[-1][0] if SECTIONS else ""
    SLIDES.append((sec, title, body, kicker, cls))


def bullets(items, cls="b"):
    return f'<ul class="{cls}">' + "".join(f"<li>{x}</li>" for x in items) + "</ul>"


def table(head, rows, cls="t", widths=None):
    cg = "".join(f'<col style="width:{w}">' for w in widths) if widths else ""
    th = "".join(f"<th>{h}</th>" for h in head)
    tr = "".join("<tr>" + "".join(f"<td>{c}</td>" for c in r) + "</tr>" for r in rows)
    return f'<table class="{cls}"><colgroup>{cg}</colgroup><thead><tr>{th}</tr></thead><tbody>{tr}</tbody></table>'


def kpis(items):
    return '<div class="kpis">' + "".join(f'<div class="kpi"><div class="v">{v}</div><div class="l">{l}</div></div>' for v, l in items) + "</div>"


def cols(*parts, ratio=None):
    st = f' style="grid-template-columns:{ratio}"' if ratio else ""
    return f'<div class="cols"{st}>' + "".join(f"<div>{p}</div>" for p in parts) + "</div>"


def note(x, kind=""):
    return f'<div class="note {kind}">{x}</div>'


# ---------------- SVG diagram helper ----------------
class D:
    def __init__(self, w, h):
        self.w, self.h, self.parts = w, h, []

    def box(self, x, y, w, h, title, lines=(), kind="", url=None, tsize=13.5, lsize=11.5):
        fill = {"accent": "var(--acc-soft)", "dark": "var(--ink)", "warm": "var(--warm-soft)", "ok": "var(--ok-soft)",
                "tint": "var(--tint)", "": "var(--card)", "dash": "var(--card)"}[kind]
        stroke = {"accent": "var(--acc)", "dark": "var(--ink)", "warm": "var(--warm)", "ok": "var(--ok)", "tint": "var(--line)",
                  "": "var(--line2)", "dash": "var(--line2)"}[kind]
        tc = "#FFFFFF" if kind == "dark" else "var(--ink)"
        sc = "#DCE3F7" if kind == "dark" else "var(--dim)"
        dash = ' stroke-dasharray="6 4"' if kind == "dash" else ""
        g = f'<rect x="{x}" y="{y}" width="{w}" height="{h}" rx="8" fill="{fill}" stroke="{stroke}" stroke-width="{2 if kind in ("accent","warm","ok") else 1.2}"{dash}/>'
        g += f'<text x="{x + 10}" y="{y + 20}" font-size="{tsize}" font-weight="700" fill="{tc}">{e(title)}</text>'
        for i, ln in enumerate(lines):
            g += f'<text x="{x + 10}" y="{y + 38 + i * 15}" font-size="{lsize}" fill="{sc}">{e(ln)}</text>'
        if url:
            g = f'<a href="{e(url)}">{g}</a>'
        self.parts.append(g)

    def group(self, x, y, w, h, title, kind="tint"):
        self.parts.append(f'<rect x="{x}" y="{y}" width="{w}" height="{h}" rx="10" fill="var(--tint)" stroke="var(--line)" stroke-width="1"/>'
                          f'<text x="{x + 12}" y="{y + 18}" font-size="11" font-weight="700" letter-spacing="1.2" fill="var(--dim)">{e(title.upper())}</text>')

    def arrow(self, pts, label=None, lx=None, ly=None, dashed=False, color="var(--axis)"):
        d = "M" + " L".join(f"{a} {b}" for a, b in pts)
        da = ' stroke-dasharray="5 4"' if dashed else ""
        self.parts.append(f'<path d="{d}" fill="none" stroke="{color}" stroke-width="1.4"{da} marker-end="url(#arr)"/>')
        if label:
            self.parts.append(f'<text x="{lx}" y="{ly}" font-size="10.5" fill="var(--dim)">{e(label)}</text>')

    def text(self, x, y, s, size=11, color="var(--dim)", weight=400, anchor="start"):
        self.parts.append(f'<text x="{x}" y="{y}" font-size="{size}" font-weight="{weight}" fill="{color}" text-anchor="{anchor}">{e(s)}</text>')

    def raw(self, s):
        self.parts.append(s)

    def svg(self):
        return (f'<svg class="dia" viewBox="0 0 {self.w} {self.h}" xmlns="http://www.w3.org/2000/svg">'
                '<defs><marker id="arr" viewBox="0 0 10 10" refX="9" refY="5" markerWidth="7" markerHeight="7" orient="auto-start-reverse">'
                '<path d="M0 0L10 5L0 10z" fill="var(--axis)"/></marker></defs>' + "".join(self.parts) + "</svg>")


exec(open(HERE / "deck_content.py").read())

# ---------------- render ----------------
CSS = (HERE / "deck.css").read_text()
toc_rows = "".join(f'<tr><td><a href="#s{p}">{e(n)}</a></td><td class="r"><a href="#s{p}">{p}</a></td></tr>' for n, p in SECTIONS)
pages = []
for i, (sec, title, body, kicker, cls) in enumerate(SLIDES, 1):
    body = body.replace("%%TOC%%", f'<table class="toc">{toc_rows}</table>')
    head = "" if "cover" in cls else (f'<div class="hd"><div class="sec">{e(sec)}</div><h2>{title}</h2>'
                                       + (f'<div class="kick">{kicker}</div>' if kicker else "") + "</div>")
    foot = "" if "cover" in cls else (f'<div class="ft"><span>Nike stock movement · solution architecture · {DATE}</span>'
                                       f'<span>GMG Sports analytics</span><span class="pg">{i}</span></div>')
    pages.append(f'<section class="slide {cls}" id="s{i}">{head}<div class="bd">{body}</div>{foot}</section>')
doc = (f'<!doctype html><html><head><meta charset="utf-8"><title>Nike Stock Movement Solution Architecture</title>'
       '<link rel="stylesheet" href="https://fonts.googleapis.com/css2?family=Source+Sans+3:wght@400;600;700&family=Source+Serif+4:opsz,wght@8..60,600&family=JetBrains+Mono:wght@400;500&display=swap">'
       f'<style>{CSS}</style></head><body>' + "".join(pages) + "</body></html>")
out = HERE / "nike_stock_movement_solution_architecture.html"
out.write_text(doc)
print("slides:", len(SLIDES), "->", out)
