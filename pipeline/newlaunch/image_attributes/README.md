# Image attributes for new-article look-alikes (experiment, 2026-11-05)

Men's tees only. Not wired into the production forecast yet.

1. `embed_pool.py` — download past men's tee launch images (from `dim_product_image`) as thumbnails.
2. `tagger.py` / `tag_pool.py` / `tag_deck.py` — Databricks vision endpoint `databricks-claude-sonnet-5-5` tags each image:
   fit, pattern, graphic size, text graphic, logo size, style, base colour (fixed value lists). Deck PNGs are
   composited on white first.
3. `learn_weights.py` — learns look-alike weights (coordinate search) on tees launched Mar–Oct 2025, each forecast
   only from launches fully observed before it; validates on Spring 2026 tees; re-forecasts the deck's SP27 tees.

Result (validation, 301 Spring 2026 men's tees): current weights 86% error / 38% within 2× / ranking 0.42;
learned master-only 84% / 40% / 0.48; learned master + image 79% / 45% / 0.48, bias +10%.
Learned importance: sport 21%, silhouette 21%, logo size 21%, price 12%, name family 8%, pattern / text graphic /
base colour 4% each, life cycle 2%, fit 2%; colour from the colourway code, graphic size, style → 0.
