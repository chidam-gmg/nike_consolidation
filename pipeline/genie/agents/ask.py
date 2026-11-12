"""Ask a Genie agent a question from the command line (same conversation API the app uses).
  python3 ask.py pipeline "What does step 08 do?"
Agent keys come from agents.json (pipeline, consolidation, replenishment) or pass a space id."""
import json, os, pathlib, subprocess, sys, time

STATE = json.loads((pathlib.Path(__file__).parent / "agents.json").read_text())


def call(method, path, body=None):
    env = dict(os.environ); env["PATH"] = os.path.expanduser("~/.local/bin") + ":" + env.get("PATH", "")
    cmd = ["databricks", "api", method, path, "--profile", "gmg-sso"] + (["--json", json.dumps(body)] if body else [])
    out = subprocess.run(cmd, capture_output=True, text=True, env=env)
    return json.loads(out.stdout or "{}")


def ask(space, question, timeout=240):
    sid = STATE.get(space, space)
    m = call("post", f"/api/2.0/genie/spaces/{sid}/start-conversation", {"content": question})
    cid, mid = m["conversation_id"], m["message_id"]
    t0 = time.time()
    while True:
        msg = call("get", f"/api/2.0/genie/spaces/{sid}/conversations/{cid}/messages/{mid}")
        if msg.get("status") in ("COMPLETED", "FAILED", "CANCELLED") or time.time() - t0 > timeout:
            break
        time.sleep(3)
    out = {"status": msg.get("status"), "seconds": round(time.time() - t0), "text": None, "sql": None, "rows": None}
    for a in msg.get("attachments", []) or []:
        if a.get("text"):
            out["text"] = a["text"].get("content")
        if a.get("query"):
            out["sql"] = a["query"].get("query"); out["description"] = a["query"].get("description")
            r = call("get", f"/api/2.0/genie/spaces/{sid}/conversations/{cid}/messages/{mid}/query-result/{a['attachment_id']}")
            res = (r.get("statement_response") or {}).get("result") or {}
            cols = [c["name"] for c in ((r.get("statement_response") or {}).get("manifest") or {}).get("schema", {}).get("columns", [])]
            out["rows"] = [dict(zip(cols, x)) for x in (res.get("data_array") or [])[:12]]
    if msg.get("error"):
        out["error"] = msg["error"]
    return out


if __name__ == "__main__":
    print(json.dumps(ask(sys.argv[1], sys.argv[2]), indent=1)[:4000])
