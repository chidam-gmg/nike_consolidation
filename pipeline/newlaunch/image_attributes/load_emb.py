import json, subprocess, sys
import numpy as np, pandas as pd
D = sys.argv[1]; FQ = "sports_analytics_test.nike_consolidation"
def q(sql):
    body = json.dumps({"warehouse_id": "1c9706d9594f1f2f", "wait_timeout": "50s", "statement": sql})
    r = json.loads(subprocess.run(["databricks", "-p", "gmg-sso", "api", "post", "/api/2.0/sql/statements", "--json", body], capture_output=True, text=True).stdout)
    if r["status"]["state"] != "SUCCEEDED": raise RuntimeError(r["status"])
E = np.vstack([np.load(f"{D}/pool_emb.npy"), np.load(f"{D}/deck_emb.npy")])
ids = pd.read_parquet(f"{D}/pool_emb_ids.parquet")["parent"].tolist() + pd.read_parquet(f"{D}/deck_emb_ids.parquet")["parent"].tolist()
q(f"""CREATE OR REPLACE TABLE {FQ}.nl_image_embeddings (parent STRING, embedding ARRAY<FLOAT>, model STRING)
      COMMENT 'Image fingerprints (CLIP ViT-B-32 LAION-2B, unit length) for visual similarity in new-article look-alikes'""")
seen = set()
for i in range(0, len(ids), 120):
    vals = []
    for p, e in zip(ids[i:i + 120], E[i:i + 120]):
        if p in seen: continue
        seen.add(p); vals.append(f"('{p}', array({','.join(f'{x:.4f}' for x in e)}), 'clip-vit-b-32-laion2b')")
    if vals: q(f"INSERT INTO {FQ}.nl_image_embeddings VALUES {', '.join(vals)}")
print("nl_image_embeddings", len(seen))
