"""Tag every pool image (past men's tee launches) with the vision model; resumable, saves every 200."""
import os, sys, time
from concurrent.futures import ThreadPoolExecutor, as_completed
import pandas as pd
from PIL import Image
sys.path.insert(0, os.path.dirname(__file__)); import tagger as T

D = sys.argv[1]; EP = "databricks-claude-sonnet-5-5"; OUT = f"{D}/pool_tags.parquet"
ids = pd.read_parquet(f"{D}/pool_emb_ids.parquet")["parent"].tolist()
done = pd.read_parquet(OUT) if os.path.exists(OUT) else pd.DataFrame(columns=["parent"])
todo = [p for p in ids if p not in set(done["parent"])]
print(f"{len(done)} already tagged, {len(todo)} to go", flush=True)
tok, t_tok = T.token(), time.time()
rows, t0 = [], time.time()


def one(p):
    return {"parent": p, **T.tag(Image.open(f"{D}/thumbs/{p}.jpg"), tok, EP)}


with ThreadPoolExecutor(12) as ex:
    futs = {ex.submit(one, p): p for p in todo}
    for i, f in enumerate(as_completed(futs), 1):
        try:
            rows.append(f.result())
        except Exception as e:
            print("fail", futs[f], str(e)[:80], flush=True)
        if i % 200 == 0 or i == len(todo):
            done = pd.concat([done, pd.DataFrame(rows)], ignore_index=True); rows = []
            done.to_parquet(OUT)
            print(f"{i}/{len(todo)} · {(time.time() - t0) / i:.2f}s per image", flush=True)
        if time.time() - t_tok > 2400:
            tok, t_tok = T.token(), time.time()
