"""Copy the daily CSV files to the owner's Google Drive folder, overwriting the copies already there.

    python scan/upload_drive.py site/

Runs at the end of every GitHub Actions scan (both daily scans and manual runs). Needs two Actions secrets:
    GDRIVE_SERVICE_ACCOUNT_JSON   the Google service account's key file (the whole JSON text)
    GDRIVE_FOLDER_ID              the Drive folder "Claude Projects/Moonshot Scanner", shared with that
                                  service account as Editor

A service account on a personal Gmail Drive can replace a file's contents but cannot create new files,
so every file below must already exist in the folder. Each file keeps its name, link and version history.
This repo and its run logs are public: never print a file ID, folder ID or link. Exits 1 (red run) if any
file was not copied.
"""
from __future__ import annotations

import json
import os
import sys
import time

FILES = [
    "track-record.csv", "price-tracking.csv", "daily-lists.csv",
    "near-miss-log.csv", "narrative-log.csv", "holder-log.csv",
]
API = "https://www.googleapis.com/drive/v3/files"
UPLOAD = "https://www.googleapis.com/upload/drive/v3/files"


def hide(text: str, secrets: list[str]) -> str:
    # Google's error messages can echo IDs back; strip them before anything reaches the public log
    for s in secrets:
        if s:
            text = text.replace(s, "<hidden>")
    return text[:300]


def call(session, method: str, url: str, **kw):
    # retry Google's busy/temporary errors; anything else is returned for the caller to report
    for attempt in range(4):
        try:
            r = session.request(method, url, timeout=60, **kw)
        except Exception as e:  # network hiccup
            err = f"network error: {type(e).__name__}"
        else:
            if r.status_code not in (429, 500, 502, 503, 504):
                return r, None
            err = f"HTTP {r.status_code}"
        time.sleep(2 ** attempt * 2)
    return None, err


def main() -> int:
    src = sys.argv[1]
    key, folder = os.environ.get("GDRIVE_SERVICE_ACCOUNT_JSON", ""), os.environ.get("GDRIVE_FOLDER_ID", "").strip()
    if not key or not folder:
        print("upload_drive: secret GDRIVE_SERVICE_ACCOUNT_JSON or GDRIVE_FOLDER_ID is not set - nothing copied")
        return 1
    try:
        info = json.loads(key)
    except ValueError:
        print("upload_drive: GDRIVE_SERVICE_ACCOUNT_JSON is not valid JSON - paste the whole key file into the secret")
        return 1

    from google.oauth2 import service_account
    from google.auth.transport.requests import AuthorizedSession

    creds = service_account.Credentials.from_service_account_info(
        info, scopes=["https://www.googleapis.com/auth/drive"])
    session = AuthorizedSession(creds)
    secrets = [folder]

    r, err = call(session, "GET", API, params={
        "q": f"'{folder}' in parents and trashed = false",
        "fields": "files(id,name)", "pageSize": 1000,
    })
    if r is None or r.status_code != 200:
        msg = err or f"HTTP {r.status_code} {hide(r.text, secrets)}"
        print(f"upload_drive: could not list the Drive folder ({msg}). "
              "Check the folder ID and that the folder is shared with the service account as Editor.")
        return 1
    in_drive: dict[str, list[str]] = {}
    for f in r.json().get("files", []):
        in_drive.setdefault(f["name"], []).append(f["id"])
        secrets.append(f["id"])

    failed = []
    for name in FILES:
        path = os.path.join(src, name)
        if not os.path.isfile(path):
            failed.append(name)
            print(f"  FAILED  {name}: not made by this scan, so nothing to copy (Drive keeps the older copy)")
            continue
        ids = in_drive.get(name, [])
        if len(ids) != 1:
            failed.append(name)
            why = "is missing from the Drive folder - upload one copy by hand once" if not ids else \
                f"appears {len(ids)} times in the Drive folder - delete the extra copies"
            print(f"  FAILED  {name}: {why}")
            continue
        with open(path, "rb") as fh:
            body = fh.read()
        r, err = call(session, "PATCH", f"{UPLOAD}/{ids[0]}", params={"uploadType": "media"},
                      data=body, headers={"Content-Type": "text/csv"})
        if r is None or r.status_code != 200:
            failed.append(name)
            print(f"  FAILED  {name}: {err or f'HTTP {r.status_code} {hide(r.text, secrets)}'}")
            continue
        print(f"  copied  {name} ({len(body):,} bytes)")

    if failed:
        print(f"upload_drive: {len(failed)} of {len(FILES)} files not copied: {', '.join(failed)}")
        return 1
    print(f"upload_drive: all {len(FILES)} files copied to Google Drive")
    return 0


if __name__ == "__main__":
    sys.exit(main())
