#!/usr/bin/env python3
"""Internex Daily Sync Script
Pulls Collection & Payable data from Lark Base -> POSTs to Google Apps Script -> Google Sheets
"""
import os
import json
import argparse
import requests
from datetime import datetime, timezone

# CONFIG
LARK_APP_ID     = os.environ.get("LARK_APP_ID")
LARK_APP_SECRET = os.environ.get("LARK_APP_SECRET")
LARK_BASE_URL   = "https://open.larksuite.com/open-apis"

WIKI_TOKEN           = "VucmwMQKSiZ6ORkll0SlIZaPgGc"
BITABLE_APP_TOKEN    = WIKI_TOKEN
COLLECTION_TABLE_ID  = "tble0T0MhVBQd8vV"
PAYABLE_TABLE_ID     = "tblH7M0OX1YoGwEa"

APPS_SCRIPT_URL = os.environ.get(
    "APPS_SCRIPT_URL",
    "https://script.google.com/macros/s/AKfycbzyzj8ka6FcQi6KZRX28CAFaDOuYbDwLs1eTiasiuySqLJmQKd51B2Pd0YZR2C5T5Or/exec")
SECRET_TOKEN = os.environ.get("APPS_SCRIPT_TOKEN", "internex_sync_2026")

MIN_MONTH = "26-01"

COLLECTION_FIELDS = [
    ("Record ID",                "_record_id"),
    ("Bank Collection Date",     "\u25bc Bank Collection Date"),
    ("Payer Name",               "Payer Name"),
    ("Collection Amount (RM)",   "Collection Amount (RM)"),
    ("Collection Main Category", "\u25bc Collection Main Category"),
    ("YY-MM",                    "YY-MM"),
    ("Collection Company Name",  "Collection Company Name"),
    ("Remarks",                  "Remarks"),
]

PAYABLE_FIELDS = [
    ("Record ID",              "_record_id"),
    ("Payable Date",           "\u2666\ufe0f\u25bc Payable Date"),
    ("Payee Name",             "Payee Name"),
    ("Company Name",           "\u25bc Company Name"),
    ("Payable Main Category",  "\u25bc Payable Main Category"),
    ("Category",               "Category"),
    ("Particular",             "\u2666\ufe0f Particular"),
    ("Payable Amount (RM)",    "\u2666\ufe0fPayable Amount (RM)"),
    ("PD Amount (RM)",         "PD Amount (RM)"),
    ("Payment Amount (RM)",    "Payment Amount (RM)"),
    ("Payable (YY-MM)",        "Payable (YY-MM)"),
    ("Approval Form Status",   "Approval Form Status"),
]

def get_tenant_token():
    url = f"{LARK_BASE_URL}/auth/v3/tenant_access_token/internal"
    resp = requests.post(url, json={"app_id": LARK_APP_ID, "app_secret": LARK_APP_SECRET})
    resp.raise_for_status()
    data = resp.json()
    if data.get("code") != 0:
        raise RuntimeError(f"Lark auth failed: {data}")
    return data["tenant_access_token"]

def get_all_records(tenant_token, app_token, table_id):
    url = f"{LARK_BASE_URL}/bitable/v1/apps/{app_token}/tables/{table_id}/records"
    headers = {"Authorization": f"Bearer {tenant_token}"}
    records = []
    page_token = None
    while True:
        params = {"page_size": 500}
        if page_token:
            params["page_token"] = page_token
        resp = requests.get(url, headers=headers, params=params)
        resp.raise_for_status()
        data = resp.json()
        if data.get("code") != 0:
            raise RuntimeError(f"Get records failed: {data}")
        items = data["data"].get("items", [])
        records.extend(items)
        if not data["data"].get("has_more"):
            break
        page_token = data["data"].get("page_token")
    return records

def extract_value(field_value):
    if field_value is None:
        return ""
    if isinstance(field_value, list):
        if not field_value:
            return ""
        if isinstance(field_value[0], dict) and "text" in field_value[0]:
            return " ".join(item.get("text", "") for item in field_value).strip()
        return str(field_value[0]) if len(field_value) == 1 else str(field_value)
    if isinstance(field_value, int) and field_value > 1_000_000_000_000:
        return datetime.fromtimestamp(field_value / 1000, tz=timezone.utc).strftime("%Y-%m-%d")
    if isinstance(field_value, str):
        try:
            return float(field_value)
        except ValueError:
            return field_value
    return field_value

def build_row(record, field_spec):
    raw = record.get("fields", {})
    row = []
    for _header, lark_name in field_spec:
        if lark_name == "_record_id":
            row.append(record.get("record_id", ""))
        else:
            row.append(extract_value(raw.get(lark_name)))
    return row

def passes_date_filter(row, field_spec, min_month):
    for i, (header, _) in enumerate(field_spec):
        if "YY-MM" in header:
            val = str(row[i]) if row[i] else ""
            return val >= min_month
    return True

def post_to_sheets(collection_rows, payable_rows, now_str):
    payload = {
        "token":           SECRET_TOKEN,
        "collection_rows": collection_rows,
        "payable_rows":    payable_rows,
        "synced_at":       now_str,
    }
    print(f"POSTing to Apps Script...")
    resp = requests.post(APPS_SCRIPT_URL, json=payload, timeout=120)
    resp.raise_for_status()
    result = resp.json()
    if result.get("success"):
        synced = result.get("synced", {})
        print(f"Google Sheets updated! Collection: {synced.get('collection',0)}, Payable: {synced.get('payable',0)}")
    else:
        raise RuntimeError(f"Apps Script error: {result}")

def main():
    if not LARK_APP_ID or not LARK_APP_SECRET:
        raise RuntimeError("LARK_APP_ID and LARK_APP_SECRET env vars are required")
    print("Getting Lark token...")
    tenant_token = get_tenant_token()
    print("Fetching Collection records...")
    col_raw = get_all_records(tenant_token, BITABLE_APP_TOKEN, COLLECTION_TABLE_ID)
    col_headers = [h for h, _ in COLLECTION_FIELDS]
    col_data = [build_row(r, COLLECTION_FIELDS) for r in col_raw]
    col_filtered = [r for r in col_data if passes_date_filter(r, COLLECTION_FIELDS, MIN_MONTH)]
    print(f"Collection: {len(col_raw)} total, {len(col_filtered)} after filter")
    print("Fetching Payable records...")
    pay_raw = get_all_records(tenant_token, BITABLE_APP_TOKEN, PAYABLE_TABLE_ID)
    pay_headers = [h for h, _ in PAYABLE_FIELDS]
    pay_data = [build_row(r, PAYABLE_FIELDS) for r in pay_raw]
    pay_filtered = [r for r in pay_data if passes_date_filter(r, PAYABLE_FIELDS, MIN_MONTH)]
    print(f"Payable: {len(pay_raw)} total, {len(pay_filtered)} after filter")
    now_str = datetime.now(timezone.utc).isoformat()
    post_to_sheets([col_headers] + col_filtered, [pay_headers] + pay_filtered, now_str)
    print(f"Done! Synced at {now_str}")

if __name__ == "__main__":
    main()
