#!/usr/bin/env python3
"""Fetch yesterday's App Store Connect sales data and write appstore-metrics.json.

Runs in GitHub Actions, which has normal outbound internet access (unlike the
Claude cloud routine sandbox that consumes this file via the GitHub Contents API).
"""
import base64
import csv
import datetime
import gzip
import io
import json
import os
import sys

import jwt
import requests

APP_SKU = "EX1777909614545"
MONTHLY_PRODUCT_ID = "app.hzama.pro.monthly"
YEARLY_PRODUCT_ID = "app.hzama.pro.annual"


def build_jwt(key_id: str, issuer_id: str, private_key: str) -> str:
    now = int(datetime.datetime.now(datetime.timezone.utc).timestamp())
    return jwt.encode(
        {"iss": issuer_id, "iat": now, "exp": now + 1200, "aud": "appstoreconnect-v1"},
        private_key,
        algorithm="ES256",
        headers={"kid": key_id, "typ": "JWT"},
    )


def fetch_sales_report(token: str, vendor_number: str, report_date: str):
    url = "https://api.appstoreconnect.apple.com/v1/salesReports"
    params = {
        "filter[frequency]": "DAILY",
        "filter[reportDate]": report_date,
        "filter[reportSubType]": "SUMMARY",
        "filter[reportType]": "SALES",
        "filter[vendorNumber]": vendor_number,
    }
    resp = requests.get(
        url,
        params=params,
        headers={
            "Authorization": f"Bearer {token}",
            "Accept": "application/a-gzip",
        },
        timeout=30,
    )
    return resp


def parse_report(raw_gzip_bytes: bytes):
    text = gzip.decompress(raw_gzip_bytes).decode("utf-8")
    reader = csv.DictReader(io.StringIO(text), delimiter="\t")
    downloads = 0
    monthly_subs = 0
    yearly_subs = 0
    revenue_by_currency: dict[str, float] = {}

    for row in reader:
        sku = row.get("SKU", "")
        units = int(row.get("Units", "0") or 0)
        if sku == APP_SKU:
            downloads += units
        elif sku == MONTHLY_PRODUCT_ID:
            monthly_subs += units
            proceeds = float(row.get("Developer Proceeds", "0") or 0)
            currency = row.get("Currency of Proceeds", "")
            revenue_by_currency[currency] = revenue_by_currency.get(currency, 0.0) + proceeds * units
        elif sku == YEARLY_PRODUCT_ID:
            yearly_subs += units
            proceeds = float(row.get("Developer Proceeds", "0") or 0)
            currency = row.get("Currency of Proceeds", "")
            revenue_by_currency[currency] = revenue_by_currency.get(currency, 0.0) + proceeds * units

    return downloads, monthly_subs, yearly_subs, revenue_by_currency


def main():
    key_id = os.environ["ASC_KEY_ID"].strip()
    issuer_id = os.environ["ASC_ISSUER_ID"].strip()
    # Tolerate the secret being pasted either as a real multi-line PEM or as
    # a single line with literal backslash-n sequences (e.g. copied straight
    # out of the JSON credentials file without unescaping).
    private_key = os.environ["ASC_PRIVATE_KEY"].strip().replace("\\n", "\n")
    vendor_number = os.environ["ASC_VENDOR_NUMBER"].strip()

    report_date = (datetime.datetime.now(datetime.timezone.utc) - datetime.timedelta(days=1)).strftime("%Y-%m-%d")

    token = build_jwt(key_id, issuer_id, private_key)
    resp = fetch_sales_report(token, vendor_number, report_date)

    result = {"report_date": report_date, "fetched_at": datetime.datetime.now(datetime.timezone.utc).isoformat()}

    if resp.status_code == 404:
        result.update({
            "status": "ok",
            "downloads": 0,
            "monthly_subs": 0,
            "yearly_subs": 0,
            "revenue": 0.0,
            "revenue_currency": None,
            "note": "No sales data for this date (normal for a quiet day).",
        })
    elif resp.status_code == 200:
        downloads, monthly_subs, yearly_subs, revenue_by_currency = parse_report(resp.content)
        if len(revenue_by_currency) <= 1:
            currency = next(iter(revenue_by_currency), None)
            revenue = revenue_by_currency.get(currency, 0.0) if currency else 0.0
            result.update({
                "status": "ok",
                "downloads": downloads,
                "monthly_subs": monthly_subs,
                "yearly_subs": yearly_subs,
                "revenue": round(revenue, 2),
                "revenue_currency": currency,
            })
        else:
            # multiple currencies in one day: report the largest, note the rest
            top_currency = max(revenue_by_currency, key=revenue_by_currency.get)
            result.update({
                "status": "ok",
                "downloads": downloads,
                "monthly_subs": monthly_subs,
                "yearly_subs": yearly_subs,
                "revenue": round(revenue_by_currency[top_currency], 2),
                "revenue_currency": top_currency,
                "note": f"Multiple currencies present: {revenue_by_currency}",
            })
    else:
        result.update({
            "status": "error",
            "error": f"HTTP {resp.status_code}: {resp.text[:500]}",
        })

    with open("appstore-metrics.json", "w") as f:
        json.dump(result, f, indent=2)
        f.write("\n")

    print(json.dumps(result, indent=2))

    if result["status"] == "error":
        sys.exit(1)


if __name__ == "__main__":
    main()
