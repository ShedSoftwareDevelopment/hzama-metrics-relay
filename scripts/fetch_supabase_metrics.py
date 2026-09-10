#!/usr/bin/env python3
"""Fetch app-usage metrics from Supabase and write supabase-metrics.json.

Uses the service_role (secret) key server-side only, via PostgREST, to
compute aggregates across all users (RLS would otherwise scope queries to
a single signed-in user).
"""
import datetime
import json
import os

import requests

SUPABASE_URL = "https://vvqfexgarnnmadqejiye.supabase.co"


def rest_count(client, table, extra_params=None):
    """Uses Prefer: count=exact with a HEAD-equivalent (limit 1, select id) request
    and reads the Content-Range header, which PostgREST always returns the
    full count in regardless of the row limit."""
    params = {"select": "id", "limit": 1}
    if extra_params:
        params.update(extra_params)
    resp = client.get(f"{SUPABASE_URL}/rest/v1/{table}", params=params, headers={"Prefer": "count=exact"})
    resp.raise_for_status()
    content_range = resp.headers.get("Content-Range", "")
    # Format: "0-0/123" or "*/123"
    if "/" in content_range:
        total = content_range.split("/")[-1]
        return int(total) if total.isdigit() else 0
    return 0


def rest_get_all(client, table, select, extra_params=None, page_size=1000):
    """Paginate through all rows for aggregate computation client-side
    (used for sums/distincts PostgREST can't do directly with a plain key)."""
    rows = []
    offset = 0
    while True:
        params = {"select": select, "limit": page_size, "offset": offset}
        if extra_params:
            params.update(extra_params)
        resp = client.get(f"{SUPABASE_URL}/rest/v1/{table}", params=params)
        resp.raise_for_status()
        page = resp.json()
        rows.extend(page)
        if len(page) < page_size:
            break
        offset += page_size
    return rows


def main():
    service_role_key = os.environ["SUPABASE_SERVICE_ROLE_KEY"].strip()
    session = requests.Session()
    session.headers.update({
        "apikey": service_role_key,
        "Authorization": f"Bearer {service_role_key}",
    })

    now = datetime.datetime.now(datetime.timezone.utc)
    report_date = (now - datetime.timedelta(days=1)).strftime("%Y-%m-%d")
    yesterday_start = f"{report_date}T00:00:00"
    yesterday_end = f"{report_date}T23:59:59.999999"
    seven_days_ago = (now - datetime.timedelta(days=7)).isoformat()

    result = {"report_date": report_date, "fetched_at": now.isoformat(), "status": "ok"}

    try:
        result["total_profiles"] = rest_count(session, "profiles")
        result["new_profiles_yesterday"] = rest_count(session, "profiles", {
            "and": f"(created_at.gte.{yesterday_start},created_at.lte.{yesterday_end})",
        })

        result["total_runs"] = rest_count(session, "runs")
        result["new_runs_yesterday"] = rest_count(session, "runs", {
            "and": f"(created_at.gte.{yesterday_start},created_at.lte.{yesterday_end})",
        })

        result["total_programmes"] = rest_count(session, "programmes")
        result["active_programmes"] = rest_count(session, "programmes", {"status": "eq.active"})
        result["completed_programmes"] = rest_count(session, "programmes", {"status": "eq.completed"})

        # Distinct active users (logged a run) in the last 7 days, and total distance.
        recent_runs = rest_get_all(session, "runs", "user_id,distance_km", {
            "created_at": f"gte.{seven_days_ago}",
        })
        result["active_users_7d"] = len({r["user_id"] for r in recent_runs if r.get("user_id")})

        all_runs_distance = rest_get_all(session, "runs", "distance_km")
        total_km = sum((r.get("distance_km") or 0) for r in all_runs_distance)
        result["total_distance_km"] = round(total_km, 1)

        # Stride calibration method breakdown (onboarding path).
        profiles = rest_get_all(session, "profiles", "stride_calibration_method")
        method_counts: dict = {}
        for p in profiles:
            method = p.get("stride_calibration_method") or "none"
            method_counts[method] = method_counts.get(method, 0) + 1
        result["calibration_method_breakdown"] = method_counts

    except requests.HTTPError as e:
        result["status"] = "error"
        result["error"] = f"HTTP {e.response.status_code}: {e.response.text[:300]}"
    except Exception as e:
        result["status"] = "error"
        result["error"] = str(e)

    with open("supabase-metrics.json", "w") as f:
        json.dump(result, f, indent=2)
        f.write("\n")

    print(json.dumps(result, indent=2))


if __name__ == "__main__":
    main()
