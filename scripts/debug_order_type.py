#!/usr/bin/env python3
"""One-off: dump full raw Sales Report rows for the app SKU across the
backfilled date range, to check whether 'Units' conflates fresh downloads
with app updates (Order Type / Product Type Identifier columns)."""
import gzip
import io
import csv
import os
import time

import jwt
import requests

APP_SKU = "EX1777909614545"
DATES = ["2026-09-02", "2026-09-03", "2026-09-04", "2026-09-05",
         "2026-09-06", "2026-09-07", "2026-09-08", "2026-09-09"]


def build_jwt(key_id, issuer_id, private_key):
    now = int(time.time())
    return jwt.encode(
        {"iss": issuer_id, "iat": now, "exp": now + 1200, "aud": "appstoreconnect-v1"},
        private_key, algorithm="ES256",
        headers={"kid": key_id, "typ": "JWT"},
    )


def main():
    key_id = os.environ["ASC_KEY_ID"].strip()
    issuer_id = os.environ["ASC_ISSUER_ID"].strip()
    private_key = os.environ["ASC_PRIVATE_KEY"].strip().replace("\\n", "\n")
    vendor_number = os.environ["ASC_VENDOR_NUMBER"].strip()
    token = build_jwt(key_id, issuer_id, private_key)

    for date in DATES:
        resp = requests.get(
            "https://api.appstoreconnect.apple.com/v1/salesReports",
            params={
                "filter[frequency]": "DAILY",
                "filter[reportDate]": date,
                "filter[reportSubType]": "SUMMARY",
                "filter[reportType]": "SALES",
                "filter[vendorNumber]": vendor_number,
            },
            headers={"Authorization": f"Bearer {token}", "Accept": "application/a-gzip"},
        )
        print(f"=== {date} status={resp.status_code} ===")
        if resp.status_code != 200:
            continue
        text = gzip.decompress(resp.content).decode("utf-8")
        reader = csv.DictReader(io.StringIO(text), delimiter="\t")
        for row in reader:
            if row.get("SKU") == APP_SKU:
                print({
                    "Units": row.get("Units"),
                    "Product Type Identifier": row.get("Product Type Identifier"),
                    "Order Type": row.get("Order Type"),
                    "Promo Code": row.get("Promo Code"),
                    "Version": row.get("Version"),
                    "Country Code": row.get("Country Code"),
                    "Customer Price": row.get("Customer Price"),
                })


if __name__ == "__main__":
    main()
