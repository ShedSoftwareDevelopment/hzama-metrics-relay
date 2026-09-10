#!/usr/bin/env python3
"""One-off investigation script: print raw shapes of Apple API responses
we haven't used yet, so the real fetch script can be written against the
actual schema instead of guessed column names. Not part of the daily run."""
import gzip
import io
import json
import os
import time

import jwt
import requests

BUNDLE_ID = "app.hzama"


def build_jwt(key_id, issuer_id, private_key):
    now = int(time.time())
    return jwt.encode(
        {"iss": issuer_id, "iat": now, "exp": now + 1200, "aud": "appstoreconnect-v1"},
        private_key,
        algorithm="ES256",
        headers={"kid": key_id, "typ": "JWT"},
    )


def main():
    key_id = os.environ["ASC_KEY_ID"].strip()
    issuer_id = os.environ["ASC_ISSUER_ID"].strip()
    private_key = os.environ["ASC_PRIVATE_KEY"].strip().replace("\\n", "\n")
    vendor_number = os.environ["ASC_VENDOR_NUMBER"].strip()
    token = build_jwt(key_id, issuer_id, private_key)
    headers = {"Authorization": f"Bearer {token}"}

    print("=== 1. App numeric ID ===")
    resp = requests.get(
        "https://api.appstoreconnect.apple.com/v1/apps",
        params={"filter[bundleId]": BUNDLE_ID},
        headers=headers,
    )
    print(resp.status_code)
    app_data = resp.json()
    print(json.dumps(app_data, indent=2)[:1000])
    app_id = None
    if app_data.get("data"):
        app_id = app_data["data"][0]["id"]
    print("APP_ID:", app_id)

    yesterday = time.strftime("%Y-%m-%d", time.gmtime(time.time() - 86400 * 2))

    print("\n=== 2. SUBSCRIPTION report (raw header + 3 rows) ===")
    resp = requests.get(
        "https://api.appstoreconnect.apple.com/v1/salesReports",
        params={
            "filter[frequency]": "DAILY",
            "filter[reportDate]": yesterday,
            "filter[reportSubType]": "SUMMARY",
            "filter[reportType]": "SUBSCRIPTION",
            "filter[vendorNumber]": vendor_number,
        },
        headers={**headers, "Accept": "application/a-gzip"},
    )
    print("status", resp.status_code)
    if resp.status_code == 200:
        text = gzip.decompress(resp.content).decode("utf-8")
        lines = text.splitlines()
        print("HEADER:", lines[0] if lines else "(empty)")
        for line in lines[1:4]:
            print("ROW:", line)
    else:
        print(resp.text[:500])

    print("\n=== 3. SUBSCRIPTION_EVENT report (raw header + 5 rows) ===")
    resp = requests.get(
        "https://api.appstoreconnect.apple.com/v1/salesReports",
        params={
            "filter[frequency]": "DAILY",
            "filter[reportDate]": yesterday,
            "filter[reportSubType]": "SUMMARY",
            "filter[reportType]": "SUBSCRIPTION_EVENT",
            "filter[vendorNumber]": vendor_number,
        },
        headers={**headers, "Accept": "application/a-gzip"},
    )
    print("status", resp.status_code)
    if resp.status_code == 200:
        text = gzip.decompress(resp.content).decode("utf-8")
        lines = text.splitlines()
        print("HEADER:", lines[0] if lines else "(empty)")
        for line in lines[1:6]:
            print("ROW:", line)
    else:
        print(resp.text[:500])

    if app_id:
        print("\n=== 4. Customer reviews (first page) ===")
        resp = requests.get(
            f"https://api.appstoreconnect.apple.com/v1/apps/{app_id}/customerReviews",
            params={"limit": 5, "sort": "-createdDate"},
            headers=headers,
        )
        print("status", resp.status_code)
        print(json.dumps(resp.json(), indent=2)[:2000])

        print("\n=== 5. Existing analyticsReportRequests ===")
        resp = requests.get(
            f"https://api.appstoreconnect.apple.com/v1/apps/{app_id}/analyticsReportRequests",
            headers=headers,
        )
        print("status", resp.status_code)
        print(json.dumps(resp.json(), indent=2)[:2000])


if __name__ == "__main__":
    main()
