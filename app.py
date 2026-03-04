import re
import time
import json
import requests
import pandas as pd
import streamlit as st
from urllib.parse import urlparse

REVERB_BASE = "https://api.reverb.com/api"

st.set_page_config(page_title="Reverb Link → Bulk Re-List", layout="wide")
st.title("Reverb Link → Bulk Re-List (Streamlit)")

# --- UI inputs ---
token = st.text_input("Reverb Access Token (OAuth Bearer)", type="password")
shipping_profile_id = st.text_input("Shipping Profile ID (غادي يتطبق على جميع المنتجات)", value="")

st.markdown("### Paste Reverb listing links (one per line)")
links_text = st.text_area(
    "Reverb links",
    height=180,
    placeholder="https://reverb.com/item/12345678-some-title\nhttps://reverb.com/item/98765432-another-title"
)

colA, colB, colC = st.columns(3)
with colA:
    delay = st.number_input("Delay بين الطلبات (ثواني)", min_value=0.0, max_value=10.0, value=0.6, step=0.1)
with colB:
    dry_run = st.checkbox("Dry-run (غير معاينة، بلا إنشاء)", value=True)
with colC:
    publish = st.checkbox("Publish مباشرة؟ (إذا API كيدعم)", value=False)

# --- helpers ---
def headers(tok: str) -> dict:
    return {
        "Accept": "application/hal+json",
        "Accept-Version": "3.0",
        "Content-Type": "application/json",
        "Authorization": f"Bearer {tok}",
    }

def extract_listing_id(url: str) -> str | None:
    """
    Common Reverb listing URLs:
      https://reverb.com/item/12345678-title
      https://reverb.com/item/12345678
    """
    url = url.strip()
    if not url:
        return None
    try:
        p = urlparse(url)
        path = p.path or ""
        m = re.search(r"/item/(\d+)", path)
        if m:
            return m.group(1)
    except:
        pass
    return None

def get_listing_public(tok: str, listing_id: str) -> tuple[int, dict]:
    url = f"{REVERB_BASE}/listings/{listing_id}"
    r = requests.get(url, headers=headers(tok), timeout=60)
    try:
        data = r.json()
    except:
        data = {"raw_text": r.text}
    return r.status_code, data

def build_payload_from_listing(src: dict, shipping_profile_id: str, publish: bool) -> dict:
    """
    Try to map fields from a fetched listing into a create-listing payload.
    You may need to adjust field names depending on Reverb’s exact API schema.
    """
    title = src.get("title") or ""
    description = src.get("description") or ""
    condition = src.get("condition") or src.get("condition_slug") or ""
    category_uuid = src.get("category_uuid") or (src.get("category") or {}).get("uuid") or ""

    # Price
    price_obj = src.get("price") or {}
    amount = price_obj.get("amount") or price_obj.get("amount_cents")
    currency = price_obj.get("currency") or "USD"

    # Photos (some APIs return "photos": [{"_links":..., "large_crop":..., "full":...}] or "photos": [{"url": ...}]
    photos = []
    for ph in (src.get("photos") or []):
        # prefer direct url if present
        if isinstance(ph, dict):
            if ph.get("url"):
                photos.append({"url": ph["url"]})
            else:
                # try common link keys
                u = ph.get("full") or ph.get("original") or ph.get("large_crop")
                if isinstance(u, str) and u.startswith("http"):
                    photos.append({"url": u})

    payload = {
        "title": title,
        "description": description,
        "condition": condition,
        "price": {
            "amount": float(amount) if amount is not None else None,
            "currency": currency
        },
    }

    if category_uuid:
        payload["category_uuid"] = category_uuid

    if shipping_profile_id:
        payload["shipping_profile_id"] = str(shipping_profile_id).strip()

    if photos:
        payload["photos"] = photos

    # Optional publish flag (if supported)
    if publish:
        payload["published"] = True

    return payload

def create_listing_my_account(tok: str, payload: dict) -> tuple[int, dict]:
    # Create listing endpoint (common pattern):
    url = f"{REVERB_BASE}/my/listings"
    r = requests.post(url, headers=headers(tok), data=json.dumps(payload), timeout=60)
    try:
        data = r.json()
    except:
        data = {"raw_text": r.text}
    return r.status_code, data

# --- Parse links ---
raw_links = [ln.strip() for ln in links_text.splitlines() if ln.strip()]
parsed = []
for ln in raw_links:
    listing_id = extract_listing_id(ln)
    parsed.append({"link": ln, "listing_id": listing_id})

df_links = pd.DataFrame(parsed)
st.subheader("Parsed links")
st.dataframe(df_links, use_container_width=True)

# --- Run ---
can_run = bool(token) and len(raw_links) > 0
if st.button("Fetch + Re-List", type="primary", disabled=not can_run):
    if not token:
        st.error("دخل Access Token.")
        st.stop()

    if not shipping_profile_id:
        st.warning("ما دخلتيش Shipping Profile ID. تقدر تكمل، ولكن بزاف ديال listings غادي يفشلو إلا كان ضروري.")

    results = []
    prog = st.progress(0)
    box = st.empty()

    valid_rows = df_links[df_links["listing_id"].notna() & (df_links["listing_id"] != "")]
    total = len(valid_rows)

    if total == 0:
        st.error("ما لقيتش listing_id ف الروابط. تأكد أنهم بحال: https://reverb.com/item/12345678-...")
        st.stop()

    for i, row in enumerate(valid_rows.to_dict("records"), start=1):
        link = row["link"]
        listing_id = row["listing_id"]
        box.info(f"[{i}/{total}] Fetching: {listing_id}")

       code_get, src = get_listing_public(token, listing_id)
        if code_get >= 400:
            results.append({
                "link": link,
                "listing_id": listing_id,
                "fetch_status": code_get,
                "create_status": "",
                "new_listing_id": "",
                "new_listing_url": "",
                "error": json.dumps(src)[:2000]
            })
            prog.progress(i / total)
            time.sleep(delay)
            continue

        payload = build_payload_from_listing(src, shipping_profile_id, publish)

        if dry_run:
            results.append({
                "link": link,
                "listing_id": listing_id,
                "fetch_status": code_get,
                "create_status": "DRY_RUN",
                "new_listing_id": "",
                "new_listing_url": "",
                "error": ""
            })
        else:
            box.info(f"[{i}/{total}] Creating from: {listing_id}")
            code_post, created = create_listing_my_account(token, payload)

            new_id = created.get("id") or created.get("listing", {}).get("id") or ""
            new_url = created.get("permalink") or created.get("_links", {}).get("web", {}).get("href") or ""

            err = ""
            if code_post >= 400:
                err = json.dumps(created)[:2000]

            results.append({
                "link": link,
                "listing_id": listing_id,
                "fetch_status": code_get,
                "create_status": code_post,
                "new_listing_id": new_id,
                "new_listing_url": new_url,
                "error": err
            })

            if code_post == 429:
                box.warning("Rate limit 429… كنوقف شوية.")
                time.sleep(max(delay, 2.0))

        prog.progress(i / total)
        time.sleep(delay)

    box.success("Done ✅")
    res_df = pd.DataFrame(results)
    st.subheader("Results")
    st.dataframe(res_df, use_container_width=True)

    st.download_button(
        "Download results CSV",
        data=res_df.to_csv(index=False).encode("utf-8"),
        file_name="reverb_relist_results.csv",
        mime="text/csv"

    )

