import re
import time
import json
import requests
import pandas as pd
import streamlit as st

REVERB_BASE = "https://api.reverb.com/api"

st.set_page_config(page_title="Reverb Bulk Re-List", layout="wide")
st.title("Reverb Link → Bulk Re-List")

token = st.text_input("Reverb Token", type="password")
shipping_profile_id = st.text_input("Shipping Profile ID")

delay = st.number_input("Delay", value=0.6)

links_text = st.text_area(
    "Paste Reverb links",
    height=150
)

def headers(tok):
    return {
        "Accept": "application/hal+json",
        "Content-Type": "application/hal+json",
        "Accept-Version": "3.0",
        "Authorization": f"Bearer {tok}",
    }

def extract_listing_id(url):
    m = re.search(r"/item/(\d+)", url)
    if m:
        return m.group(1)
    return None

def discount_20(price):
    try:
        p = float(price)
    except:
        p = 0
    new_price = p * 0.8
    return f"{new_price:.2f}"

def get_listing(tok, listing_id):

    url = f"{REVERB_BASE}/listings/{listing_id}"

    r = requests.get(url, headers=headers(tok))

    try:
        data = r.json()
    except:
        data = {}

    return r.status_code, data

def get_photo_url(ph):

    if isinstance(ph,dict):

        if "url" in ph:
            return ph["url"]

        if "_links" in ph:

            if "full" in ph["_links"]:
                return ph["_links"]["full"]["href"]

    return None

def build_payload(src):

    title = src.get("title","")
    description = src.get("description","")

    price_obj = src.get("price",{})

    amount = price_obj.get("amount",0)

    currency = price_obj.get("currency","USD")

    new_price = discount_20(amount)

    photos = []

    for p in src.get("photos",[]):

        url = get_photo_url(p)

        if url:
            photos.append(url)

    payload = {
        "title": title,
        "description": description,
        "price": {
            "amount": new_price,
            "currency": currency
        },
        "photos": photos
    }

    if shipping_profile_id:
        payload["shipping_profile_id"] = shipping_profile_id

    if "condition" in src:
        payload["condition"] = src["condition"]

    if "make" in src:
        payload["make"] = src["make"]

    if "model" in src:
        payload["model"] = src["model"]

    if "categories" in src:
        payload["categories"] = src["categories"]

    return payload

def create_listing(tok,payload):

    url = f"{REVERB_BASE}/listings"

    r = requests.post(
        url,
        headers=headers(tok),
        data=json.dumps(payload)
    )

    try:
        data = r.json()
    except:
        data = {}

    return r.status_code,data


raw_links = [l.strip() for l in links_text.splitlines() if l.strip()]

parsed = []

for link in raw_links:

    listing_id = extract_listing_id(link)

    parsed.append({
        "link":link,
        "listing_id":listing_id
    })

df = pd.DataFrame(parsed)

st.subheader("Parsed links")

st.dataframe(df)

if st.button("Fetch + Re-List"):

    results = []

    progress = st.progress(0)

    valid = df[df["listing_id"].notna()]

    total = len(valid)

    for i,row in enumerate(valid.to_dict("records"),start=1):

        listing_id = row["listing_id"]

        code_get,src = get_listing(token,listing_id)

        if code_get != 200:

            results.append({
                "listing_id":listing_id,
                "error":"fetch failed"
            })

        else:

            payload = build_payload(src)

            code_post,data = create_listing(token,payload)

            results.append({
                "listing_id":listing_id,
                "create_status":code_post
            })

        progress.progress(i/total)

        time.sleep(delay)

    res_df = pd.DataFrame(results)

    st.subheader("Results")

    st.dataframe(res_df)
