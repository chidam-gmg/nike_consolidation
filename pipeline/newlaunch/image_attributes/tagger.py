"""Tag product images with structured attributes via a Databricks-served vision model (REST, OAuth token)."""
import base64, io, json, subprocess, time
import requests
from PIL import Image

HOST = "https://dbc-74c28e5e-272e.cloud.databricks.com"
FIELDS = {
    "fit": ["slim", "regular", "loose", "oversized"],
    "pattern": ["solid", "heather", "colour-block", "all-over print", "placement graphic", "stripe"],
    "graphic_size": ["none", "small logo", "medium", "large", "all-over"],
    "text_graphic": ["yes", "no"],
    "logo_size": ["small", "medium", "large"],
    "style": ["performance", "lifestyle", "statement"],
    "base_colour": ["black", "white", "grey", "navy", "blue", "red", "green", "pink", "purple", "orange", "yellow", "brown", "beige", "multi"],
}
PROMPT = ("Tag this sportswear product image for demand forecasting. Return ONLY JSON with exactly these keys, each value "
          "chosen from its list: " + json.dumps(FIELDS) + ". Judge the product only, ignore the model and background.")


def token():
    return json.loads(subprocess.run(["databricks", "auth", "token", "-p", "gmg-sso"], capture_output=True, text=True).stdout)["access_token"]


def tag(img: Image.Image, tok: str, endpoint: str, retries: int = 3) -> dict:
    im = img.convert("RGB"); im.thumbnail((384, 384))
    buf = io.BytesIO(); im.save(buf, format="JPEG", quality=85)
    body = {"messages": [{"role": "user", "content": [{"type": "text", "text": PROMPT},
            {"type": "image_url", "image_url": {"url": "data:image/jpeg;base64," + base64.b64encode(buf.getvalue()).decode()}}]}],
            "max_tokens": 200}
    for a in range(retries):
        r = requests.post(f"{HOST}/serving-endpoints/{endpoint}/invocations", headers={"Authorization": f"Bearer {tok}"}, json=body, timeout=90)
        if r.status_code == 429:
            time.sleep(2 + 3 * a); continue
        r.raise_for_status()
        txt = r.json()["choices"][0]["message"]["content"]
        if isinstance(txt, list):
            txt = "".join(p.get("text", "") for p in txt if isinstance(p, dict))
        d = json.loads(txt[txt.find("{"): txt.rfind("}") + 1])
        return {k: (str(d.get(k)).lower() if str(d.get(k)).lower() in v else None) for k, v in FIELDS.items()}
    raise RuntimeError("rate limited")
