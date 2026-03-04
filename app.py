import re
import time
import json
import requests
import pandas as pd
import streamlit as st

# Official base used in Reverb docs
REVERB_BASE = "https://api.reverb.com/api"

st.set_page_config(page_title="Reverb Bulk Re-List", layout="wide")
st.title("Reverb Link → Bulk Re-List (Reverb API v3)")

st.caption("Tip: If CREATE fails, scroll to see the raw error body. It will tell us exactly what's missing.")

# ---------- UI ----------
token = st.text_input("Reverb Personal Token (Bearer)", type="password")
shipping_profile_id = st.text_input("Shipping Profile ID (optional but recommended)")

st.markdown("### Optional overrides (only if API complains)")
col1, col2, col3, col4 = st.columns(4)
with col1:
    override_make = st.text_input("Override make (optional)", value="")
with col2:
    override_model = st.text_input("Override model (optional)", value="")
with col3:
    override_category_uuid = st.text_input("Override category uuid (optional)", value="")
with col4:
    override_condition_uuid = st.text_input("Override condition uuid (optional)", value="")

colA, colB, colC = st.columns(3)
with colA:
    delay = st.number_input("Delay between requests (seconds)", min_value=0.0, max_value=10.0, value=0.6, step=0.1)
with colB:
    dry_run = st.checkbox("Dry-run (no create)", value=False)
with colC:
    inventory = st.number_input("Inventory (optional)", min_value=0, value=1, step=1)

links_text = st.text_area(
    "Paste Reverb listing links (one per line)",
    height=160,
    placeholder="https://reverb.com/item/94336095-...\nhttps://reverb.com/item/94644803-..."
)

# ---------- Helpers ----------
def headers(tok: str) -> dict:
    # Reverb API requires Accept-Version header in v3
    return {
        "Accept": "application/hal+json",
        "Content-Type": "application/hal+json",
        "Accept-Version": "3.0",
        "Authorization": f"Bearer {tok}",
    }

def extract_listing_id(url: str) -> str | None:
    if not url:
        return None
    m = re.search(r"/item/(\d+)", url.strip())
    return m.group(1) if m else None

def http_json(method: str, url: str, tok: str | None = None, payload: dict | None = None, timeout: int = 60):
    h = headers(tok) if tok else {"Accept": "application/hal+json", "Accept-Version": "3.0"}
    try:
        if method == "GET":
            r = requests.get(url, headers=h, timeout=timeout)
        elif method == "POST":
            r = requests.post(url, headers=h, data=json.dumps(payload or {}), timeout=timeout)
        else:
            raise ValueError("Unsupported method")
        try:
            data = r.json()
        except Exception:
            data = {"raw_text": r.text}
        return r.status_code, data
    except Exception as e:
        return 0, {"exception": str(e), "url": url}

def get_listing(tok: str, listing_id: str):
    # GET /listings/:id
    url = f"{REVERB_BASE}/listings/{listing_id}"
    return http_json("GET", url, tok=tok)

def normalize_categories(src: dict) -> list:
    """
    Create Listings supports categories with uuid (docs show "categories": [{"uuid": "..."}]).
    Some listing payloads may have: "categories": [{"uuid":...}], OR "category_uuids": [...]
    We'll normalize to categories:[{"uuid":...}]
    """
    cats = []

    # If already in the right structure
    if isinstance(src.get("categories"), list) and len(src["categories"]) > 0:
        for c in src["categories"]:
            if isinstance(c, dict) and c.get("uuid"):
                cats.append({"uuid": c["uuid"]})

    # Alternative field
    if not cats and isinstance(src.get("category_uuids"), list) and len(src["category_uuids"]) > 0:
        for u in src["category_uuids"]:
            if isinstance(u, str) and u.strip():
                cats.append({"uuid": u.strip()})

    return cats

def normalize_condition(src: dict) -> dict | None:
    """
    Create Listings expects condition as object with uuid:
      "condition": {"uuid": "..."}
    The fetched listing might have condition object, or condition_uuid, or something else.
    """
    c = src.get("condition")
    if isinstance(c, dict) and c.get("uuid"):
        return {"uuid": c["uuid"]}

    # Some APIs expose condition_uuid
    cu = src.get("condition_uuid")
    if isinstance(cu, str) and cu.strip():
        return {"uuid": cu.strip()}

    return None

def normalize_photos(src: dict) -> list:
    """
    Docs show photos as array of URLs.
    We'll try to pull any usable URLs from listing photos.
    If none found, we simply omit photos (API may still allow draft).
    """
    out = []
    photos = src.get("photos") or []
    if isinstance(photos, list):
        for ph in photos:
            if isinstance(ph, str) and ph.startswith("http"):
                out.append(ph)
            elif isinstance(ph, dict):
                # try common keys
                for k in ["url", "full", "original", "large_crop"]:
                    v = ph.get(k)
                    if isinstance(v, str) and v.startswith("http"):
                        out.append(v)
                        break
    # de-dup keep order
    seen = set()
    uniq = []
    for u in out:
        if u not in seen:
            uniq.append(u)
            seen.add(u)
    return uniq[:20]  # keep it reasonable

def build_create_payload(src: dict) -> dict:
    """
    Build a payload aligned with Create Listings docs:
      make, model, categories[{uuid}], condition{uuid}, description, price{amount,currency}, title, shipping_profile_id, has_inventory, inventory, photos
    Reverb requires make/model to save a product draft (they can guess from title, but better to send). (docs)
    """
    title = (src.get("title") or "").strip()
    description = (src.get("description") or "").strip()

    make = (override_make.strip() if override_make.strip() else (src.get("make") or "")).strip()
    model = (override_model.strip() if override_model.strip() else (src.get("model") or "")).strip()

    # price
    price_obj = src.get("price") or {}
    amount = price_obj.get("amount")
    currency = price_obj.get("currency") or "USD"

    # Sometimes amount might be nested/strings
    if amount is None:
        amount = src.get("price_amount") or src.get("amount")

    # categories / condition
    cats = normalize_categories(src)
    cond = normalize_condition(src)

    # overrides
    if override_category_uuid.strip():
        cats = [{"uuid": override_category_uuid.strip()}]

    if override_condition_uuid.strip():
        cond = {"uuid": override_condition_uuid.strip()}

    payload = {
        "title": title,
        "description": description,
        "price": {"amount": str(amount) if amount is not None else "0.00", "currency": currency},
        "has_inventory": True,
        "inventory": int(inventory),
    }

    # Add structured fields if available
    if make:
        payload["make"] = make
    if model:
        payload["model"] = model
    if cats:
        payload["categories"] = cats
    if cond:
        payload["condition"] = cond

    # Optional extras if present
    for k in ["finish", "year", "sku", "upc", "handmade", "offers_enabled"]:
        if k in src and src.get(k) not in [None, "", []]:
            payload[k] = src.get(k)

    # photos
    ph = normalize_photos(src)
    if ph:
        payload["photos"] = ph

    # shipping profile
    if shipping_profile_id.strip():
        # docs field name: shipping_profile_id
        payload["shipping_profile_id"] = shipping_profile_id.strip()

    return payload

def create_listing(tok: str, payload: dict):
    # POST /listings (official docs example)
    url = f"{REVERB_BASE}/listings"
    return http_json("POST", url, tok=tok, payload=payload)

# ---------- Parse links ----------
raw_links = [ln.strip() for ln in links_text.splitlines() if ln.strip()]
df = pd.DataFrame([{"link": ln, "listing_id": extract_listing_id(ln)} for ln in raw_links])

st.subheader("Parsed links")
st.dataframe(df, use_container_width=True)

# ---------- Run ----------
if st.button("Fetch + Re-List", type="primary", disabled=not (token and len(raw_links) > 0)):
    results = []
    valid = df[df["listing_id"].notna() & (df["listing_id"] != "")]
    total = len(valid)

    if total == 0:
        st.error("No valid listing_id found in links. Links must contain /item/<digits>")
        st.stop()

    prog = st.progress(0)
    status = st.empty()

    for i, row in enumerate(valid.to_dict("records"), start=1):
        link = row["link"]
        listing_id = row["listing_id"]

        status.info(f"[{i}/{total}] Fetch listing {listing_id}")
        code_get, src = get_listing(token, listing_id)

        if code_get != 200:
            results.append({
                "link": link,
                "listing_id": listing_id,
                "fetch_status": code_get,
                "create_status": "",
                "new_listing_id": "",
                "new_listing_url": "",
                "error": json.dumps(src)[:2000],
            })
            prog.progress(i / total)
            time.sleep(delay)
            continue

        payload = build_create_payload(src)

        if dry_run:
            results.append({
                "link": link,
                "listing_id": listing_id,
                "fetch_status": code_get,
                "create_status": "DRY_RUN",
                "new_listing_id": "",
                "new_listing_url": "",
                "error": "",
            })
        else:
            status.info(f"[{i}/{total}] Create draft from {listing_id}")
            code_post, created = create_listing(token, payload)

            # attempt to extract new listing ID and URL
            new_id = created.get("id") or created.get("listing", {}).get("id") or ""
            new_url = created.get("permalink") or created.get("_links", {}).get("web", {}).get("href") or ""

            err = ""
            if code_post >= 400 or code_post == 0:
                # keep useful body
                err = json.dumps(created)[:4000]

            results.append({
                "link": link,
                "listing_id": listing_id,
                "fetch_status": code_get,
                "create_status": code_post,
                "new_listing_id": new_id,
                "new_listing_url": new_url,
                "error": err,
            })

        prog.progress(i / total)
        time.sleep(delay)

    status.success("Done ✅")

    res_df = pd.DataFrame(results)
    st.subheader("Results")
    st.dataframe(res_df, use_container_width=True)

    st.download_button(
        "Download results CSV",
        data=res_df.to_csv(index=False).encode("utf-8"),
        file_name="reverb_bulk_results.csv",
        mime="text/csv",
    )
