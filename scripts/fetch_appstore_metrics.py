#!/usr/bin/env python3
"""Fetch yesterday's App Store Connect data and write appstore-metrics.json.

Runs in GitHub Actions, which has normal outbound internet access (unlike the
Claude cloud routine sandbox that consumes this file via the GitHub Contents API).
"""
import datetime
import gzip
import io
import csv
import json
import os
import sys
import time

import jwt
import requests

APP_ID = "6766275602"
APP_SKU = "EX1777909614545"
MONTHLY_PRODUCT_ID = "app.hzama.pro.monthly"
YEARLY_PRODUCT_ID = "app.hzama.pro.annual"
MONTHLY_SUBSCRIPTION_APPLE_ID = "6796822547"
YEARLY_SUBSCRIPTION_APPLE_ID = "6796825338"

BASE = "https://api.appstoreconnect.apple.com"


def build_jwt(key_id: str, issuer_id: str, private_key: str) -> str:
    now = int(datetime.datetime.now(datetime.timezone.utc).timestamp())
    return jwt.encode(
        {"iss": issuer_id, "iat": now, "exp": now + 1200, "aud": "appstoreconnect-v1"},
        private_key,
        algorithm="ES256",
        headers={"kid": key_id, "typ": "JWT"},
    )


class AppleClient:
    """Wraps requests with automatic JWT re-mint on 401 (tokens can be flaky/short-lived
    in practice even within the 20-minute exp window)."""

    def __init__(self, key_id, issuer_id, private_key):
        self.key_id = key_id
        self.issuer_id = issuer_id
        self.private_key = private_key
        self.token = build_jwt(key_id, issuer_id, private_key)

    def _headers(self, accept=None):
        h = {"Authorization": f"Bearer {self.token}"}
        if accept:
            h["Accept"] = accept
        return h

    def get(self, url, params=None, accept=None, retry=True):
        resp = requests.get(url, params=params, headers=self._headers(accept), timeout=30)
        if resp.status_code == 401 and retry:
            self.token = build_jwt(self.key_id, self.issuer_id, self.private_key)
            time.sleep(1)
            return self.get(url, params=params, accept=accept, retry=False)
        return resp


def fetch_sales(client: AppleClient, vendor_number: str, report_date: str):
    resp = client.get(
        f"{BASE}/v1/salesReports",
        params={
            "filter[frequency]": "DAILY",
            "filter[reportDate]": report_date,
            "filter[reportSubType]": "SUMMARY",
            "filter[reportType]": "SALES",
            "filter[vendorNumber]": vendor_number,
        },
        accept="application/a-gzip",
    )
    if resp.status_code == 404:
        return {"downloads": 0, "updates": 0, "monthly_subs": 0, "yearly_subs": 0, "revenue": 0.0, "revenue_currency": None}
    if resp.status_code != 200:
        return {"error": f"HTTP {resp.status_code}: {resp.text[:300]}"}

    text = gzip.decompress(resp.content).decode("utf-8")
    reader = csv.DictReader(io.StringIO(text), delimiter="\t")
    downloads = updates = monthly_subs = yearly_subs = 0
    revenue_by_currency: dict = {}
    for row in reader:
        sku = row.get("SKU", "")
        units = int(row.get("Units", "0") or 0)
        # Product Type Identifier "1" = a real download/install; "7" = an
        # app update being delivered to an existing user. Apple's own Sales
        # Report lumps both under the app SKU's Units column, so without
        # this check "downloads" silently includes update installs too.
        product_type = row.get("Product Type Identifier", "")
        if sku == APP_SKU:
            if product_type == "7":
                updates += units
            else:
                downloads += units
        elif sku in (MONTHLY_PRODUCT_ID, YEARLY_PRODUCT_ID):
            proceeds = float(row.get("Developer Proceeds", "0") or 0)
            currency = row.get("Currency of Proceeds", "")
            revenue_by_currency[currency] = revenue_by_currency.get(currency, 0.0) + proceeds * units
            if sku == MONTHLY_PRODUCT_ID:
                monthly_subs += units
            else:
                yearly_subs += units

    if revenue_by_currency:
        top_currency = max(revenue_by_currency, key=revenue_by_currency.get)
        revenue, revenue_currency = round(revenue_by_currency[top_currency], 2), top_currency
    else:
        revenue, revenue_currency = 0.0, None

    return {
        "downloads": downloads,
        "updates": updates,
        "monthly_subs": monthly_subs,
        "yearly_subs": yearly_subs,
        "revenue": revenue,
        "revenue_currency": revenue_currency,
    }


def fetch_active_subscribers(client: AppleClient, vendor_number: str, report_date: str):
    """SUBSCRIPTION report: a snapshot of currently-active subscriptions per plan,
    broken into many state/offer-type columns. Summing all the numeric
    active/offer/billing columns per row gives the active count for that row's
    plan+state combination."""
    resp = client.get(
        f"{BASE}/v1/salesReports",
        params={
            "filter[frequency]": "DAILY",
            "filter[reportDate]": report_date,
            "filter[reportSubType]": "SUMMARY",
            "filter[reportType]": "SUBSCRIPTION",
            "filter[vendorNumber]": vendor_number,
            "filter[version]": "1_4",
        },
        accept="application/a-gzip",
    )
    if resp.status_code == 404:
        return {"monthly": 0, "yearly": 0}
    if resp.status_code != 200:
        return {"error": f"HTTP {resp.status_code}: {resp.text[:300]}"}

    text = gzip.decompress(resp.content).decode("utf-8")
    reader = csv.DictReader(io.StringIO(text), delimiter="\t")
    non_count_columns = {
        "App Name", "App Apple ID", "Subscription Name", "Subscription Apple ID",
        "Subscription Group ID", "Standard Subscription Duration", "Subscription Offer Name",
        "Promotional Offer ID", "Customer Price", "Customer Currency", "Developer Proceeds",
        "Proceeds Currency", "Preserved Pricing", "Proceeds Reason", "Client", "Device",
        "State", "Country", "Contingent App Name",
    }
    totals = {"monthly": 0, "yearly": 0}
    for row in reader:
        sub_apple_id = row.get("Subscription Apple ID", "")
        row_total = 0
        for col, val in row.items():
            if col in non_count_columns or col is None:
                continue
            try:
                row_total += int(val or 0)
            except ValueError:
                continue
        if sub_apple_id == MONTHLY_SUBSCRIPTION_APPLE_ID:
            totals["monthly"] += row_total
        elif sub_apple_id == YEARLY_SUBSCRIPTION_APPLE_ID:
            totals["yearly"] += row_total
    return totals


def fetch_subscription_events(client: AppleClient, vendor_number: str, report_date: str):
    resp = client.get(
        f"{BASE}/v1/salesReports",
        params={
            "filter[frequency]": "DAILY",
            "filter[reportDate]": report_date,
            "filter[reportSubType]": "SUMMARY",
            "filter[reportType]": "SUBSCRIPTION_EVENT",
            "filter[vendorNumber]": vendor_number,
            "filter[version]": "1_4",
        },
        accept="application/a-gzip",
    )
    if resp.status_code == 404:
        return {}
    if resp.status_code != 200:
        return {"error": f"HTTP {resp.status_code}: {resp.text[:300]}"}

    text = gzip.decompress(resp.content).decode("utf-8")
    reader = csv.DictReader(io.StringIO(text), delimiter="\t")
    counts: dict = {}
    for row in reader:
        event = row.get("Event", "Unknown")
        qty = int(row.get("Quantity", "1") or 1)
        counts[event] = counts.get(event, 0) + qty
    return counts


def fetch_reviews(client: AppleClient):
    resp = client.get(
        f"{BASE}/v1/apps/{APP_ID}/customerReviews",
        params={"limit": 200, "sort": "-createdDate"},
    )
    if resp.status_code != 200:
        return {"error": f"HTTP {resp.status_code}: {resp.text[:300]}"}
    data = resp.json()
    total_count = data.get("meta", {}).get("paging", {}).get("total", 0)
    reviews = data.get("data", [])
    if not reviews:
        return {"total_count": total_count, "average_rating_recent": None, "latest": None}
    ratings = [r["attributes"]["rating"] for r in reviews if "rating" in r.get("attributes", {})]
    avg = round(sum(ratings) / len(ratings), 2) if ratings else None
    latest_attrs = reviews[0]["attributes"]
    return {
        "total_count": total_count,
        "average_rating_recent": avg,
        "latest": {
            "rating": latest_attrs.get("rating"),
            "title": latest_attrs.get("title"),
            "body": (latest_attrs.get("body") or "")[:300],
            "created_date": latest_attrs.get("createdDate"),
        },
    }


def main():
    key_id = os.environ["ASC_KEY_ID"].strip()
    issuer_id = os.environ["ASC_ISSUER_ID"].strip()
    private_key = os.environ["ASC_PRIVATE_KEY"].strip().replace("\\n", "\n")
    vendor_number = os.environ["ASC_VENDOR_NUMBER"].strip()

    report_date = (datetime.datetime.now(datetime.timezone.utc) - datetime.timedelta(days=1)).strftime("%Y-%m-%d")
    client = AppleClient(key_id, issuer_id, private_key)

    sales = fetch_sales(client, vendor_number, report_date)
    active_subscribers = fetch_active_subscribers(client, vendor_number, report_date)
    subscription_events = fetch_subscription_events(client, vendor_number, report_date)
    reviews = fetch_reviews(client)

    result = {
        "report_date": report_date,
        "fetched_at": datetime.datetime.now(datetime.timezone.utc).isoformat(),
        "status": "error" if "error" in sales else "ok",
    }
    result.update(sales)
    result["active_subscribers"] = active_subscribers
    result["subscription_events"] = subscription_events
    result["reviews"] = reviews

    with open("appstore-metrics.json", "w") as f:
        json.dump(result, f, indent=2)
        f.write("\n")

    print(json.dumps(result, indent=2))

    if result["status"] == "error":
        sys.exit(1)


if __name__ == "__main__":
    main()
