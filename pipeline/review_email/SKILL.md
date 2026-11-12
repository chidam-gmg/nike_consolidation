---
name: nike-review-email
description: E-mails the exhaustive list of Nike stock movement actions that need human review (store moves and DC lines from the last completed run) as a pre-filled CSV the reviewer completes. Use when the user asks to send, e-mail or share the review list, the approvals, the actions to review, or the review CSV for the stock movement engine.
---

# E-mail the Nike stock movement review pack

Sends the reviewers one e-mail with a summary and a link to a pre-filled CSV of every action in a completed run that
needs a person. These are the same lines as the app's Approvals queue:

- store moves worth at least AED 1,000 or moving at least 6 units;
- DC lines that are full-size top-ups, or worth at least AED 1,000, or at least 6 units.

The CSV has blank **Decision (Approve / Reject)** and **Comment** columns for the reviewer. Lines already decided in the app come pre-filled.

- Source: Databricks function `sports_analytics_test.nike_consolidation.agent_review_actions_for(run)` (view `agent_review_actions` = latest completed run).
- Builder: `nike_consolidation/pipeline/review_email/build_review_pack.py`.
- Upload target: the user's OneDrive (drive `b!KjJS5Iqz7k2yVONEnyvHCBps_ZflEIBDvE5WliCfIyhTS_tvjFukQLOCaGD-cjhu`), folder **Nike stock movement reviews**.
- E-mail: the Microsoft 365 connector. Outlook drafts cannot carry attachments, so the e-mail links to the CSV.

## 1. Ask first, every time

Before building anything, ask the user, in one message:

1. **Which run?** Default: the last completed run. Offer a run ID if they want an earlier one.
2. **Which lines?** Default: every line that needs review, already-decided ones included. Option: pending only.
3. **Who receives it?** To and Cc e-mail addresses. Never guess an address. If the user names people, find their address with the Microsoft 365 people search and show it back.

Wait for the answers.

## 2. Build the pack

From the projects folder, with the Databricks CLI on PATH (`export PATH=$HOME/.local/bin:$PATH`, profile gmg-sso):

```
python3 nike_consolidation/pipeline/review_email/build_review_pack.py --run <latest|run_id> [--pending-only] --out <scratch dir>
```

If imports fail, install `nike_consolidation/build/app/requirements.txt` into a virtual environment and use its python.

- The script prints `review_pack.json`: run ID, counts, CSV path and size, subject, and the HTML body path.
- Exit code 2 means the job is running right now and its results are not archived yet. Tell the user, and offer to wait about 8 minutes or use the previous run.
- Exit code 3 means there is nothing to review. Tell the user, and send nothing.
- The CSV must be under 1 MB for the upload. If it is larger, offer `--pending-only`.

## 3. Show the user what will go out

Show:

- the subject;
- the counts by action (lines, units, value);
- the recipients;
- the file name.

Ask: **"Upload the CSV to your OneDrive and create the draft e-mail?"** Continue only after yes.

## 4. Upload the CSV

1. Find the folder with `sharepoint_folder_search` (name "Nike stock movement reviews"). If it does not exist, create it with `sharepoint_create_folder` in the OneDrive drive above.
2. Upload with `sharepoint_upload_file`: `driveId` = the OneDrive drive, `parentItemId` = the folder ID, `filename` = the CSV's name, `content` = the CSV text.
3. Use `conflictBehavior: "replace"` only when re-sending the same run; otherwise `"fail"`.
4. Keep the returned `webUrl`.

The folder must be shared with the reviewers once (OneDrive > the folder > Share), so every new file in it opens for them. If the user has not done this, remind them before sending.

## 5. Draft, then send only on a clear yes

1. Read the HTML body file. Replace `%%CSV_LINK%%` with `<p><b>CSV:</b> <a href="<webUrl>"><file name></a> (opens in Excel)</p>`.
2. Create the draft with `outlook_create_draft`: `to` / `cc` = the confirmed addresses, `subject` from review_pack.json, `bodyType: "html"`.
3. Give the user the draft's `webLink` and ask: **"Send it now?"**
4. Only on a clear yes, send with `outlook_send_draft`. Otherwise leave it in Drafts and say so.
5. Never send to anyone the user did not confirm in step 1, and never send without that final yes.

## 6. After sending

Tell the user what went out: recipients, run ID, line counts, and the CSV link.

Reviewers can decide in the CSV or directly in the app (Store consolidation tab, Approvals). Loading a returned CSV's decisions back into the app is not built yet. Say so if asked, and offer to build it: the `Line key` column is there for that.
