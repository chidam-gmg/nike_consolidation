"""Download pool images as thumbnails and compute CLIP image embeddings (ViT-B-32, LAION-2B)."""
import io, os, sys, time
from concurrent.futures import ThreadPoolExecutor
import numpy as np, pandas as pd, requests, torch, open_clip
from PIL import Image

D = sys.argv[1]
pool = pd.read_parquet(f"{D}/pool.parquet")
os.makedirs(f"{D}/thumbs", exist_ok=True)


def fetch(row):
    out = f"{D}/thumbs/{row.parent}.jpg"
    if os.path.exists(out):
        return row.parent, True
    try:
        r = requests.get(row.url, timeout=20)
        if r.status_code != 200 or not r.headers.get("content-type", "").startswith("image"):
            return row.parent, False
        im = Image.open(io.BytesIO(r.content)).convert("RGB"); im.thumbnail((256, 256)); im.save(out, quality=85)
        return row.parent, True
    except Exception:
        return row.parent, False


t = time.time()
with ThreadPoolExecutor(32) as ex:
    ok = dict(ex.map(fetch, pool.itertuples()))
print(f"downloaded {sum(ok.values()):,} of {len(pool):,} in {time.time() - t:.0f}s", flush=True)

model, _, prep = open_clip.create_model_and_transforms("ViT-B-32", pretrained="laion2b_s34b_b79k")
model.eval()
keep = [p for p in pool["parent"] if ok.get(p)]
embs, t = [], time.time()
with torch.no_grad():
    for i in range(0, len(keep), 64):
        batch = torch.stack([prep(Image.open(f"{D}/thumbs/{p}.jpg").convert("RGB")) for p in keep[i:i + 64]])
        e = model.encode_image(batch); embs.append((e / e.norm(dim=-1, keepdim=True)).numpy())
E = np.concatenate(embs)
np.save(f"{D}/pool_emb.npy", E); pd.Series(keep).to_frame("parent").to_parquet(f"{D}/pool_emb_ids.parquet")
print(f"embedded {len(keep):,} images in {time.time() - t:.0f}s · vector size {E.shape[1]}")
