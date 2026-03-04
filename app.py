import re
import time
import json
import requests
import pandas as pd
import streamlit as st

REVERB_BASE = "https://api.reverb.com/api"

st.set_page_config(page_title="Reverb Bulk Re-List", layout="wide")
st.title("Reverb Link → Bulk Re-List (v3)")

# ---------------- UI ----------------
token = st.text_input("Reverb Personal Token (Bearer)", type="password")
shipping_profile_id = st.text_input("Shipping Profile ID (اختياري ولكن مهم)")

colA, colB, colC = st.columns(3)
with colA:
    delay = st.number_input("Delay between requests (sec)", min_value=0.0, max_value=10.0, value=0.6, step=0.1)
with colB:
    dry_run = st.checkbox("Dry-run (ما كينش إنشاء)", value=False)
with colC:
    inventory = st.number_input("Inventory", min_value=0, value=1, step=1)

links_text = st.text_area(
    "Paste Reverb listing links (one per line)",
    height=160,
    placeholder="https://reverb.com/item/94336095-...\nhttps://reverb.com/item/94644803-..."
)

st.caption("⚠️ إذا بان لك 'must be in good standing' هادي من الحساب/Shop، ماشي من الصور.")

# ---------------- Helpers ----------------
def headers(tok: str) -> dict:
    return {
        "Accept": "application/hal+json",
        "Content-Type": "application/hal+json",
        "Accept-Version": "3.0",
        "Authorization": f"Bearer {tok}",
    }

def extract_listing_id(url: str):
    m = re.search(r"/item/(\d+)", (url or "").strip())
    return m.group(1) if m else None

def http_json(method: str, url: str, tok: str, payload: dict | None = None, timeout: int = 60):
    h = headers(tok)
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
    return http_json("GET", f"{REVERB_BASE}/listings/{listing_id}", tok)

def pick_photo_url(ph: dict) -> str | None:
    """
    Reverb listing photos often come as objects.
    We'll try multiple common shapes:
    - {"_links":{"full":{"href":...}}}
    - {"_links":{"original":{"href":...}}}
    - {"url":...} / {"full":...} / {"original":...} / {"large_crop":...}
    """
    if not isinstance(ph, dict):
        return None

    # Direct keys
    for k in ["url", "full", "original", "large", "large_crop", "medium", "small"]:
        v = ph.get(k)
        if isinstance(v, str) and v.startswith("http"):
            return v

    # HAL links
    links = ph.get("_links")
    if isinstance(links, dict):
        for lk in ["full", "original", "large", "large_crop"]:
            obj = links.get(lk)
            if isinstance(obj, dict):
                href = obj.get("href")
                if isinstance(href, str) and href.startswith("http"):
                    return href

    return None

def extract_photo_urls(src: dict, max_photos: int = 12) -> list[str]:
    urls: list[str] = []

    photos = src.get("photos")
    if isinstance(photos, list):
        for ph in photos:
            if isinstance(ph, str) and ph.startswith("http"):
                urls.append(ph)
            elif isinstance(ph, dict):
                u = pick_photo_url(ph)
                if u:
                    urls.append(u)

    # De-dup keep order
    seen = set()
    out = []
    for u in urls:
        if u not in seen:
            out.append(u)
            seen.add(u)
        if len(out) >= max_photos:
            break
    return out

def normalize_categories(src: dict) -> list[dict]:
    # Docs use: "categories": [{"uuid": "..."}]
    cats = []
    if isinstance(src.get("categories"), list):
        for c in src["categories"]:
            if isinstance(c, dict) and c.get("uuid"):
                cats.append({"uuid": c["uuid"]})
    # Alternative: category_uuids
    if not cats and isinstance(src.get("category_uuids"), list):
        for u in src["category_uuids"]:
            if isinstance(u, str) and u.strip():
                cats.append({"uuid": u.strip()})
    return cats

def normalize_condition(src: dict) -> dict | None:
    c = src.get("condition")
    if isinstance(c, dict) and c.get("uuid"):
        return {"uuid": c["uuid"]}
    cu = src.get("condition_uuid")
    if isinstance(cu, str) and cu.strip():
        return {"uuid": cu.strip()}
    return None

def build_create_payload(src: dict) -> tuple[dict, list[str]]:
    """
    Build payload exactly like Reverb docs:
    - make, model, categories[{uuid}], condition{uuid}
    - photos: [ "url1", "url2" ]  (IMPORTANT)
    - price {amount,currency}, title, description
    - shipping_profile_id optional
    - has_inventory/inventory
    """
    title = (src.get("title") or "").strip()
    description = (src.get("description") or "").strip()

    make = (src.get("make") or "").strip()
    model = (src.get("model") or "").strip()

    # price
    price = src.get("price") or {}
    amount = price.get("amount")
    currency = price.get("currency") or "USD"
    if amount is None:
        amount = "0.00"

    cats = normalize_categories(src)
    cond = normalize_condition(src)

    photos = extract_photo_urls(src)

    payload = {
        "title": title,
        "description": description,
        "price": {"amount": str(amount), "currency": currency},
        "has_inventory": True,
        "inventory": int(inventory),
    }

    # Reverb requires make/model for publishable drafts (docs)
    if make:
        payload["make"] = make
    if model:
        payload["model"] = model
    if cats:
        payload["categories"] = cats
    if cond:
        payload["condition"] = cond

    # THIS is the important part (photos as list of strings)
    if photos:
        payload["photos"] = photos

    if shipping_profile_id.strip():
        payload["shipping_profile_id"] = shipping_profile_id.strip()

    return payload, photos

def create_listing(tok: str, payload: dict):
    # Official create endpoint
    return http_json("POST", f"{REVERB_BASE}/listings", tok, payload=payload)

# ---------------- Parse links ----------------
raw_links = [ln.strip() for ln in links_text.splitlines() if ln.strip()]
df = pd.DataFrame([{"link": ln, "listing_id": extract_listing_id(ln)} for ln in raw_links])

st.subheader("Parsed links")
st.dataframe(df, use_container_width=True)

# ---------------- Run ----------------
if st.button("Fetch + Re-List", type="primary", disabled=not (token and len(raw_links) > 0)):
    valid = df[df["listing_id"].notna() & (df["listing_id"] != "")]
    total = len(valid)
    if total == 0:
        st.error("ما لقيتش listing_id ف الروابط (خاص /item/<digits>)")
        st.stop()

    results = []
    prog = st.progress(0)
    box = st.empty()

    for i, row in enumerate(valid.to_dict("records"), start=1):
        link = row["link"]
        listing_id = row["listing_id"]

        box.info(f"[{i}/{total}] Fetch listing {listing_id}")
        code_get, src = get_listing(token, listing_id)

        if code_get != 200:
            results.append({
                "link": link,
                "listing_id": listing_id,
                "fetch_status": code_get,
                "create_status": "",
                "new_listing_id": "",
                "new_listing_url": "",
                "photos_sent": 0,
                "error": json.dumps(src)[:3000],
            })
            prog.progress(i / total)
            time.sleep(delay)
            continue

        payload, photo_urls = build_create_payload(src)

        # Show quick debug for first item
        if i == 1:
            st.write("Debug (first item): photos extracted =", len(photo_urls))
            if len(photo_urls) > 0:
                st.write(photo_urls[:3])

        if dry_run:
            results.append({
                "link": link,
                "listing_id": listing_id,
                "fetch_status": code_get,
                "create_status": "DRY_RUN",
                "new_listing_id": "",
                "new_listing_url": "",
                "photos_sent": len(photo_urls),
                "error": "",
            })
        else:
            box.info(f"[{i}/{total}] Create draft…")
            code_post, created = create_listing(token, payload)

            new_id = created.get("id") or created.get("listing", {}).get("id") or ""
            new_url = created.get("permalink") or created.get("_links", {}).get("web", {}).get("href") or ""

            err = ""
            if code_post >= 400 or code_post == 0:
                err = json.dumps(created)[:4000]

            results.append({
                "link": link,
                "listing_id": listing_id,
                "fetch_status": code_get,
                "create_status": code_post,
                "new_listing_id": new_id,
                "new_listing_url": new_url,
                "photos_sent": len(photo_urls),
                "error": err,
            })

        prog.progress(i / total)
        time.sleep(delay)

    box.success("Done ✅")
    res_df = pd.DataFrame(results)
    st.subheader("Results")
    st.dataframe(res_df, use_container_width=True)

    st.download_button(
        "Download results CSV",
        data=res_df.to_csv(index=False).encode("utf-8"),
        file_name="reverb_bulk_results.csv",
        mime="text/csv",
    )
