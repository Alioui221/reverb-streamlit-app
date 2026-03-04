import re
import time
import json
import requests
import pandas as pd
import streamlit as st
from urllib.parse import urlparse

REVERB_BASE = "https://api.reverb.com/api"

st.set_page_config(page_title="Reverb Bulk Re-List", layout="wide")
st.title("Reverb Link → Bulk Re-List")

token = st.text_input("Reverb API Token", type="password")
shipping_profile_id = st.text_input("Shipping Profile ID")

links_text = st.text_area(
    "Paste Reverb links (one per line)",
    height=150
)

delay = st.number_input("Delay between requests", value=0.6)

def headers(tok):
    return {
        "Accept": "application/hal+json",
        "Accept-Version": "3.0",
        "Content-Type": "application/json",
        "Authorization": f"Bearer {tok}",
    }

def extract_listing_id(url):
    m = re.search(r"/item/(\d+)", url)
    if m:
        return m.group(1)
    return None

def get_listing_public(tok, listing_id):
    url = f"{REVERB_BASE}/listings/{listing_id}"
    r = requests.get(url, headers=headers(tok))
    try:
        data = r.json()
    except:
        data = {"raw_text": r.text}
    return r.status_code, data

def build_payload(src):
    title = src.get("title","")
    description = src.get("description","")
    condition = src.get("condition","")

    price_obj = src.get("price",{})
    amount = price_obj.get("amount",0)
    currency = price_obj.get("currency","USD")

    payload = {
        "title": title,
        "description": description,
        "condition": condition,
        "price": {
            "amount": amount,
            "currency": currency
        }
    }

    if shipping_profile_id:
        payload["shipping_profile_id"] = shipping_profile_id

    return payload

def create_listing(tok, payload):
    url = f"{REVERB_BASE}/my/listings"
    r = requests.post(url, headers=headers(tok), data=json.dumps(payload))
    try:
        data = r.json()
    except:
        data = {"raw_text": r.text}
    return r.status_code, data


raw_links = [l.strip() for l in links_text.splitlines() if l.strip()]
parsed = []

for link in raw_links:
    listing_id = extract_listing_id(link)
    parsed.append({"link": link, "listing_id": listing_id})

df_links = pd.DataFrame(parsed)

st.subheader("Parsed links")
st.dataframe(df_links)

if st.button("Fetch + Re-List"):

    results = []
    progress = st.progress(0)

    valid_rows = df_links[df_links["listing_id"].notna()]

    total = len(valid_rows)

    for i,row in enumerate(valid_rows.to_dict("records"),start=1):

        link = row["link"]
        listing_id = row["listing_id"]

        code_get, src = get_listing_public(token, listing_id)

        if code_get >= 400:

            results.append({
                "link": link,
                "listing_id": listing_id,
                "fetch_status": code_get,
                "create_status": "",
                "error": json.dumps(src)
            })

        else:

            payload = build_payload(src)

            code_post, created = create_listing(token, payload)

            new_id = created.get("id","")

            results.append({
                "link": link,
                "listing_id": listing_id,
                "fetch_status": code_get,
                "create_status": code_post,
                "new_listing_id": new_id,
                "error": ""
            })

        progress.progress(i/total)

        time.sleep(delay)

    res_df = pd.DataFrame(results)

    st.subheader("Results")
    st.dataframe(res_df)

    st.download_button(
        "Download CSV",
        data=res_df.to_csv(index=False),
        file_name="results.csv"
    )
