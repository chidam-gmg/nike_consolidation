import json, os, sys
from concurrent.futures import ThreadPoolExecutor
import pandas as pd
from PIL import Image
sys.path.insert(0, os.path.dirname(__file__)); import tagger as T
D = sys.argv[1]; tok = T.token()
items = json.load(open(f"{D}/deck_tees.json"))
def one(it):
    im = Image.open(f"{D}/deck_tees/{it['parent']}.png").convert("RGBA")
    bg = Image.new("RGBA", im.size, (255, 255, 255, 255)); bg.alpha_composite(im)     # transparent -> white
    for a in range(3):
        try:
            return {"parent": it["parent"], **T.tag(bg.convert("RGB"), tok, "databricks-claude-sonnet-5-5")}
        except Exception as e:
            err = e
    return {"parent": it["parent"]}
with ThreadPoolExecutor(8) as ex:
    rows = list(ex.map(one, items))
pd.DataFrame(rows).to_parquet(f"{D}/deck_tags.parquet")
df = pd.DataFrame(rows); print(len(df), "deck tees tagged ·", int(df["fit"].notna().sum()), "complete")
print(df[df.parent == "NKIR2969-010"].to_dict("records"))
