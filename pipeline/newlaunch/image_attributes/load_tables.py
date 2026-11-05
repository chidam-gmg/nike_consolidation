"""Load image tags (history + SP27 deck) and SP27 deck display images into the project schema."""
import json, subprocess, sys
import pandas as pd
D = sys.argv[1]; FQ = "sports_analytics_test.nike_consolidation"
TAGS = ["fit", "pattern", "graphic_size", "text_graphic", "logo_size", "style", "base_colour"]


def q(sql):
    body = json.dumps({"warehouse_id": "1c9706d9594f1f2f", "wait_timeout": "50s", "statement": sql})
    r = json.loads(subprocess.run(["databricks", "-p", "gmg-sso", "api", "post", "/api/2.0/sql/statements", "--json", body],
                                  capture_output=True, text=True).stdout)
    if r["status"]["state"] != "SUCCEEDED":
        raise RuntimeError(r["status"])


lit = lambda v: "NULL" if v is None or (isinstance(v, float) and v != v) else "'" + str(v).replace("'", "''") + "'"
pool = pd.read_parquet(f"{D}/pool_tags.parquet").drop_duplicates("parent").assign(source="Akeneo product image")
deck = pd.read_parquet(f"{D}/deck_all_tags.parquet")
tags = pd.concat([pool[["parent", "source"] + TAGS], deck[deck["fit"].notna()][["parent", "source"] + TAGS]]).drop_duplicates("parent", keep="last")
q(f"""CREATE OR REPLACE TABLE {FQ}.nl_image_tags (parent STRING, source STRING, {", ".join(f"{t} STRING" for t in TAGS)},
      tagged_by STRING, tagged_at TIMESTAMP)
      COMMENT 'Product attributes read from images by the Databricks vision endpoint databricks-claude-sonnet-5-5 (new-article look-alikes)'""")
for i in range(0, len(tags), 400):
    vals = ", ".join("(" + ", ".join(lit(r[c]) for c in ["parent", "source"] + TAGS) + ", 'databricks-claude-sonnet-5-5', current_timestamp())"
                     for _, r in tags.iloc[i:i + 400].iterrows())
    q(f"INSERT INTO {FQ}.nl_image_tags VALUES {vals}")
q(f"""CREATE OR REPLACE TABLE {FQ}.nl_target_images (parent STRING, image_data_uri STRING, source STRING)
      COMMENT 'Images for new-season styles not yet in Akeneo, taken from range decks (small JPEG data URIs for the app)'""")
for i in range(0, len(deck), 25):
    vals = ", ".join(f"({lit(r.parent)}, {lit(r.image_data_uri)}, {lit(r.source)})" for r in deck.iloc[i:i + 25].itertuples())
    q(f"INSERT INTO {FQ}.nl_target_images VALUES {vals}")
print(f"nl_image_tags {len(tags):,} rows · nl_target_images {len(deck)} rows")
