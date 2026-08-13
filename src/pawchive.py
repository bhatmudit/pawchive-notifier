from __future__ import annotations
import time
from typing import Any
import requests

BASE_URL="https://pawchive.pw/api"
PAGE_SIZE=50

class PawchiveError(RuntimeError): pass

def fetch_creator_posts(service,creator_id,*,known_ids=None,bootstrap=False,timeout=30):
    known_ids=known_ids or set()
    posts=[]; offset=0
    while True:
        url=f"{BASE_URL}/{service}/user/{creator_id}"
        try: response=requests.get(url,params={"o":offset} if offset else {},
                                  headers={"Accept":"application/json"},timeout=timeout)
        except requests.RequestException as e: raise PawchiveError(f"request failed: {e}") from e
        if response.status_code==404: raise PawchiveError(f"creator not found: {service}/{creator_id}")
        if response.status_code!=200: raise PawchiveError(f"HTTP {response.status_code}: {response.text[:300]}")
        try: page=response.json()
        except ValueError as e: raise PawchiveError("Pawchive returned invalid JSON") from e
        if not isinstance(page,list): raise PawchiveError("unexpected Pawchive response shape")
        page_posts=[p for p in page if isinstance(p,dict) and "id" in p]
        posts.extend(page_posts)
        if not page_posts or len(page_posts)<PAGE_SIZE: break
        if not bootstrap and any(str(p["id"]) in known_ids for p in page_posts): break
        offset+=PAGE_SIZE
        time.sleep(.05)
    return posts
