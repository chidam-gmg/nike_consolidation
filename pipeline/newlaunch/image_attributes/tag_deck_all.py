"""Tag every deck article image and keep a small white-background JPEG (as a data URI) for the app."""
import base64, io, json, os, sys
from concurrent.futures import ThreadPoolExecutor
import pandas as pd
from PIL import Image
sys.path.insert(0, os.path.dirname(__file__)); import tagger as T
D = sys.argv[1]; DECK = "SP27 - Beacon Assortment Men's RUN.TRAIN.pptx"; tok = T.token()
pairs = json.load(open(f"{D}/deck_all.json"))
def one(item):
    parent, (slide, path) = item
    im = Image.open(os.path.join(D, "..", "deck", path)).convert("RGBA")
    bg = Image.new("RGBA", im.size, (255, 255, 255, 255)); bg.alpha_composite(im); rgb = bg.convert("RGB")
    th = rgb.copy(); th.thumbnail((320, 320)); buf = io.BytesIO(); th.save(buf, format="JPEG", quality=82)
    row = {"parent": parent, "image_data_uri": "data:image/jpeg;base64," + base64.b64encode(buf.getvalue()).decode(),
           "source": f"{DECK} · slide {slide}"}
    for _ in range(3):
        try:
            return {**row, **T.tag(rgb, tok, "databricks-claude-sonnet-5-5")}
        except Exception:
            pass
    return row
with ThreadPoolExecutor(8) as ex:
    rows = list(ex.map(one, pairs.items()))
df = pd.DataFrame(rows); df.to_parquet(f"{D}/deck_all_tags.parquet")
print(len(df), "deck images ·", int(df["fit"].notna().sum()), "tagged · avg image", int(df["image_data_uri"].str.len().mean() / 1024), "KB")
