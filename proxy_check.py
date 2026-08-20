"""Verify the proxy's real egress IP + ASN/type (residential vs datacenter)."""
import os
import json
from curl_cffi import requests as curl_requests

PROXY = os.environ.get("PROXY_URL", "")

def show(name, url, **kw):
    try:
        r = curl_requests.get(url, timeout=20, impersonate="chrome131", **kw)
        print(f"[{name}] status={r.status_code}")
        print(json.dumps(r.json(), ensure_ascii=False, indent=2))
    except Exception as e:
        print(f"[{name}] ERROR: {e}")

# Direct egress
print("=== DIRECT ===")
show("direct", "https://ipinfo.io/json")

# Via proxy
if PROXY:
    px = PROXY
    if not px.startswith("http"):
        px = "http://" + px
    print(f"=== VIA PROXY {px.split('@')[-1]} ===")
    show("proxy", "https://ipinfo.io/json", proxies={"http": px, "https": px})

    # also scrape a known "is this a datacenter" signal
    show("proxy-cloudflare-ip", "https://www.cloudflare.com/cdn-cgi/trace")
else:
    print("no PROXY_URL set")