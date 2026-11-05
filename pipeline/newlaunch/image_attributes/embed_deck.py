"""CLIP fingerprints for every deck article image (composited on white), same model as the pool."""
import base64, io, sys
import numpy as np, pandas as pd, torch, open_clip
from PIL import Image
D = sys.argv[1]
deck = pd.read_parquet(f"{D}/deck_all_tags.parquet")
model, _, prep = open_clip.create_model_and_transforms("ViT-B-32", pretrained="laion2b_s34b_b79k"); model.eval()
ims = [Image.open(io.BytesIO(base64.b64decode(u.split(",", 1)[1]))).convert("RGB") for u in deck["image_data_uri"]]
with torch.no_grad():
    e = torch.cat([model.encode_image(torch.stack([prep(i) for i in ims[k:k + 64]])) for k in range(0, len(ims), 64)])
e = (e / e.norm(dim=-1, keepdim=True)).numpy()
np.save(f"{D}/deck_emb.npy", e); deck[["parent"]].to_parquet(f"{D}/deck_emb_ids.parquet"); print("deck embeddings", e.shape)
