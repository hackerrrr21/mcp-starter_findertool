# mcp_places_server.py
import asyncio
import os
import time
import json
from typing import Annotated, Optional
from dotenv import load_dotenv
from fastmcp import FastMCP
from fastmcp.server.auth.providers.bearer import BearerAuthProvider, RSAKeyPair
from mcp import ErrorData, McpError
from mcp.types import TextContent, ImageContent, INVALID_PARAMS, INTERNAL_ERROR
from mcp.server.auth.provider import AccessToken
from pydantic import BaseModel, Field, AnyUrl

import httpx
import markdownify
import readabilipy

# Load env
load_dotenv()
TOKEN = os.environ.get("AUTH_TOKEN")
MY_NUMBER = os.environ.get("MY_NUMBER")
GOOGLE_API_KEY = os.environ.get("GOOGLE_API_KEY")
YELP_API_KEY = os.environ.get("YELP_API_KEY")

assert TOKEN is not None, "Please set AUTH_TOKEN in .env"
assert MY_NUMBER is not None, "Please set MY_NUMBER in .env"

# --- Simple bearer auth provider (kept from your code) ---
class SimpleBearerAuthProvider(BearerAuthProvider):
    def __init__(self, token: str):
        k = RSAKeyPair.generate()
        super().__init__(public_key=k.public_key, jwks_uri=None, issuer=None, audience=None)
        self.token = token

    async def load_access_token(self, token: str) -> Optional[AccessToken]:
        if token == self.token:
            return AccessToken(token=token, client_id="puch-client", scopes=["*"], expires_at=None)
        return None

# --- Utility: HTML extraction (kept from your code) ---
class Fetch:
    USER_AGENT = "Puch/1.0 (Autonomous)"

    @classmethod
    async def fetch_url(cls, url: str, user_agent: str = USER_AGENT, force_raw: bool = False) -> tuple[str, str]:
        async with httpx.AsyncClient(timeout=30) as client:
            try:
                resp = await client.get(url, follow_redirects=True, headers={"User-Agent": user_agent})
            except httpx.HTTPError as e:
                raise McpError(ErrorData(code=INTERNAL_ERROR, message=f"Failed to fetch {url}: {e!r}"))
            if resp.status_code >= 400:
                raise McpError(ErrorData(code=INTERNAL_ERROR, message=f"Failed to fetch {url} - status {resp.status_code}"))

            page_raw = resp.text
            content_type = resp.headers.get("content-type", "")
            is_page_html = "text/html" in content_type

            if is_page_html and not force_raw:
                return cls.extract_content_from_html(page_raw), ""
            return page_raw, f"Content type {content_type} could not be simplified; raw content:\n"

    @staticmethod
    def extract_content_from_html(html: str) -> str:
        ret = readabilipy.simple_json.simple_json_from_html_string(html, use_readability=True)
        if not ret or not ret.get("content"):
            return "<error>Page failed to be simplified from HTML</error>"
        content = markdownify.markdownify(ret["content"], heading_style=markdownify.ATX)
        return content

# --- In-memory TTL cache (very small, not for production) ---
class TTLCache:
    def __init__(self, ttl_seconds: int = 120):
        self.ttl = ttl_seconds
        self.store: dict[str, tuple[float, any]] = {}

    def get(self, key: str):
        item = self.store.get(key)
        if not item:
            return None
        ts, value = item
        if time.time() - ts > self.ttl:
            del self.store[key]
            return None
        return value

    def set(self, key: str, value: any):
        self.store[key] = (time.time(), value)

cache = TTLCache(ttl_seconds=180)

# --- Normalized result models (simple dict shapes) ---
def normalize_place_basic(name, place_id, lat, lon, distance_m=None, rating=None, source="unknown", categories=None):
    return {
        "id": place_id,
        "name": name,
        "lat": lat,
        "lon": lon,
        "distance_m": distance_m,
        "rating": rating,
        "source": source,
        "categories": categories or [],
    }

# --- Provider integrations ---

# Google Places (Nearby Search + Details)
async def google_places_nearby(lat: float, lon: float, keyword: str, radius: int = 1000, limit: int = 10):
    if not GOOGLE_API_KEY:
        return []
    cache_key = f"google_nearby:{lat:.5f}:{lon:.5f}:{keyword}:{radius}:{limit}"
    cached = cache.get(cache_key)
    if cached:
        return cached

    url = "https://maps.googleapis.com/maps/api/place/nearbysearch/json"
    params = {
        "location": f"{lat},{lon}",
        "radius": radius,
        "keyword": keyword,
        "key": GOOGLE_API_KEY,
    }
    results = []
    async with httpx.AsyncClient(timeout=15) as client:
        try:
            resp = await client.get(url, params=params)
            resp.raise_for_status()
            data = resp.json()
        except Exception as e:
            # treat network errors gracefully
            return []

    for item in data.get("results", [])[:limit]:
        geom = item.get("geometry", {}).get("location", {})
        result = normalize_place_basic(
            name=item.get("name"),
            place_id=item.get("place_id"),
            lat=geom.get("lat"),
            lon=geom.get("lng"),
            rating=item.get("rating"),
            source="google",
            categories=item.get("types", []),
        )
        results.append(result)

    cache.set(cache_key, results)
    return results

async def google_place_details(place_id: str):
    if not GOOGLE_API_KEY:
        return {}
    cache_key = f"google_details:{place_id}"
    cached = cache.get(cache_key)
    if cached:
        return cached

    url = "https://maps.googleapis.com/maps/api/place/details/json"
    params = {
        "place_id": place_id,
        "fields": "name,formatted_address,formatted_phone_number,opening_hours,geometry,photos,review,rating,reviews",
        "key": GOOGLE_API_KEY,
    }
    async with httpx.AsyncClient(timeout=15) as client:
        try:
            resp = await client.get(url, params=params)
            resp.raise_for_status()
            data = resp.json()
        except Exception as e:
            return {}

    result = data.get("result", {})
    # Normalize reviews (if present)
    reviews = []
    for r in result.get("reviews", [])[:10]:
        reviews.append({
            "author": r.get("author_name"),
            "rating": r.get("rating"),
            "text": r.get("text"),
            "time": r.get("time"),
            "source": "google",
        })

    details = {
        "id": place_id,
        "name": result.get("name"),
        "address": result.get("formatted_address"),
        "phone": result.get("formatted_phone_number"),
        "lat": result.get("geometry", {}).get("location", {}).get("lat"),
        "lon": result.get("geometry", {}).get("location", {}).get("lng"),
        "rating": result.get("rating"),
        "reviews": reviews,
        "source": "google",
    }
    cache.set(cache_key, details)
    return details

# Yelp Fusion (search + reviews)
async def yelp_search(lat: float, lon: float, term: str, radius: int = 1000, limit: int = 10):
    if not YELP_API_KEY:
        return []
    cache_key = f"yelp_search:{lat:.5f}:{lon:.5f}:{term}:{radius}:{limit}"
    cached = cache.get(cache_key)
    if cached:
        return cached

    url = "https://api.yelp.com/v3/businesses/search"
    headers = {"Authorization": f"Bearer {YELP_API_KEY}"}
    params = {"term": term, "latitude": lat, "longitude": lon, "radius": radius, "limit": limit}
    async with httpx.AsyncClient(timeout=15) as client:
        try:
            resp = await client.get(url, headers=headers, params=params)
            resp.raise_for_status()
            data = resp.json()
        except Exception:
            return []

    results = []
    for b in data.get("businesses", []):
        results.append(normalize_place_basic(
            name=b.get("name"),
            place_id=b.get("id"),
            lat=b.get("coordinates", {}).get("latitude"),
            lon=b.get("coordinates", {}).get("longitude"),
            rating=b.get("rating"),
            source="yelp",
            categories=[c.get("alias") for c in b.get("categories", [])],
        ))
    cache.set(cache_key, results)
    return results

async def yelp_get_reviews(business_id: str, limit: int = 3):
    if not YELP_API_KEY:
        return []
    cache_key = f"yelp_reviews:{business_id}:{limit}"
    cached = cache.get(cache_key)
    if cached:
        return cached

    url = f"https://api.yelp.com/v3/businesses/{business_id}/reviews"
    headers = {"Authorization": f"Bearer {YELP_API_KEY}"}
    async with httpx.AsyncClient(timeout=15) as client:
        try:
            resp = await client.get(url, headers=headers)
            resp.raise_for_status()
            data = resp.json()
        except Exception:
            return []

    reviews = []
    for r in data.get("reviews", [])[:limit]:
        reviews.append({
            "author": r.get("user", {}).get("name"),
            "rating": r.get("rating"),
            "text": r.get("text"),
            "time": r.get("time_created"),
            "source": "yelp",
        })
    cache.set(cache_key, reviews)
    return reviews

# Overpass / OpenStreetMap fallback (free)
async def overpass_search(lat: float, lon: float, amenity_keyword: str, radius: int = 1000, limit: int = 25):
    # Map categories to common OSM tags
    amenity_map = {
        "doctor": '["amenity"="clinic"],["amenity"="doctors"],["amenity"="hospital"]',
        "restaurant": '["amenity"="restaurant"],["amenity"="fast_food"]',
        "cafe": '["amenity"="cafe"]',
        "grocery": '["shop"="supermarket"],["shop"="grocery"]',
        "pharmacy": '["amenity"="pharmacy"]',
    }
    tags = amenity_map.get(amenity_keyword.lower(), '["amenity"="restaurant"]')

    cache_key = f"osm:{lat:.5f}:{lon:.5f}:{amenity_keyword}:{radius}"
    cached = cache.get(cache_key)
    if cached:
        return cached

    # Build Overpass QL
    q = f"""
    [out:json][timeout:25];
    (
      node(around:{radius},{lat},{lon}){tags};
      way(around:{radius},{lat},{lon}){tags};
      relation(around:{radius},{lat},{lon}){tags};
    );
    out center {limit};
    """
    url = "https://overpass-api.de/api/interpreter"
    async with httpx.AsyncClient(timeout=30) as client:
        try:
            resp = await client.post(url, data=q)
            resp.raise_for_status()
            data = resp.json()
        except Exception:
            return []

    results = []
    for el in data.get("elements", [])[:limit]:
        if el.get("type") == "node":
            lat_e = el.get("lat")
            lon_e = el.get("lon")
        else:
            center = el.get("center", {})
            lat_e = center.get("lat")
            lon_e = center.get("lon")

        results.append(normalize_place_basic(
            name=el.get("tags", {}).get("name", "unknown"),
            place_id=str(el.get("id")),
            lat=lat_e,
            lon=lon_e,
            rating=None,
            source="osm",
            categories=[v for k,v in el.get("tags", {}).items() if k in ("amenity","shop")]
        ))
    cache.set(cache_key, results)
    return results

# --- Lightweight review summarizer (heuristic) ---
POSITIVE_WORDS = {"good","great","excellent","friendly","clean","fast","helpful","recommend","best","professional"}
NEGATIVE_WORDS = {"bad","terrible","dirty","slow","rude","expensive","don't","worst","avoid"}

def summarize_texts_simple(reviews: list[dict]) -> dict:
    if not reviews:
        return {"summary": "No reviews available", "sentiment": "neutral", "counts": {"positive":0,"negative":0}}
    pos = neg = 0
    snippets = []
    for r in reviews:
        text = (r.get("text") or "").lower()
        if any(w in text for w in POSITIVE_WORDS):
            pos += 1
        if any(w in text for w in NEGATIVE_WORDS):
            neg += 1
        snippets.append((r.get("author"), (r.get("text") or "")[:200]))
    sentiment = "positive" if pos > neg else ("negative" if neg > pos else "neutral")
    summary = f"Found {len(reviews)} reviews — {pos} positive hint(s), {neg} negative hint(s). Top snippets:\n"
    for a,s in snippets[:3]:
        summary += f"- {a}: {s}\n"
    return {"summary": summary, "sentiment": sentiment, "counts": {"positive":pos,"negative":neg}}

# --- MCP Server setup (your original pattern) ---
mcp = FastMCP("Places MCP Server", auth=SimpleBearerAuthProvider(TOKEN))

# validate tool
@mcp.tool
async def validate() -> str:
    return MY_NUMBER

# Tool: find_nearby
@mcp.tool(description=json.dumps({
    "description":"Find nearby places (doctor, cafe, restaurant, grocery, pharmacy).",
    "use_when":"Use when the user wants nearby POIs given coordinates.",
    "side_effects":"Queries external place APIs (Google/Yelp/OSM) and returns normalized results with source attribution."
}))
async def find_nearby(
    lat: Annotated[float, Field(description="Latitude")],
    lon: Annotated[float, Field(description="Longitude")],
    category: Annotated[str, Field(description="Category: doctor, cafe, restaurant, grocery, pharmacy")] = "restaurant",
    radius_m: Annotated[int, Field(description="Search radius in meters")] = 1000,
    limit: Annotated[int, Field(description="Max results")] = 10,
) -> dict:
    """
    Multi-source search:
    - Try Google (if key present)
    - Enrich with Yelp (if key) for additional results
    - Fallback to OpenStreetMap Overpass if no paid API
    Returns a normalized dict with results and attribution.
    """
    # minimal validation
    if not (-90 <= lat <= 90 and -180 <= lon <= 180):
        raise McpError(ErrorData(code=INVALID_PARAMS, message="Invalid latitude/longitude"))

    key = f"find_nearby:{lat:.5f}:{lon:.5f}:{category}:{radius_m}:{limit}"
    cached = cache.get(key)
    if cached:
        return cached

    results = []
    # Prefer Google first
    g = await google_places_nearby(lat, lon, category, radius=radius_m, limit=limit)
    if g:
        results.extend(g)

    # Add Yelp results if available
    y = await yelp_search(lat, lon, category, radius=radius_m, limit=limit)
    # merge dedup (by lat/lon or id)
    existing_ids = {r["id"] for r in results}
    for r in y:
        if r["id"] not in existing_ids:
            results.append(r)
            existing_ids.add(r["id"])

    # If still empty, fallback to OSM
    if not results:
        o = await overpass_search(lat, lon, category, radius=radius_m, limit=limit)
        results.extend(o)

    # Shorten list to requested limit and calculate approximate distance if possible
    def dist_m(a_lat, a_lon):
        # Haversine approximate (in meters)
        from math import radians, cos, sin, asin, sqrt
        R = 6371000.0
        dlat = radians(a_lat - lat)
        dlon = radians(a_lon - lon)
        alat = radians(lat)
        alat2 = radians(a_lat)
        a = sin(dlat/2)**2 + cos(alat)*cos(alat2)*sin(dlon/2)**2
        c = 2 * asin(min(1, sqrt(a)))
        return R * c

    for r in results:
        try:
            if r.get("lat") is not None and r.get("lon") is not None:
                r["distance_m"] = int(dist_m(r["lat"], r["lon"]))
        except Exception:
            r["distance_m"] = None

    results = sorted(results, key=lambda x: (x.get("distance_m") or 999999))[:limit]

    out = {"results": results, "attribution": {
        "google": bool(GOOGLE_API_KEY),
        "yelp": bool(YELP_API_KEY),
        "osm": True
    }}
    cache.set(key, out)
    return out

# Tool: get_details
@mcp.tool(description=json.dumps({
    "description":"Get detailed info for a place id (supports google place_id or yelp id or osm id).",
    "use_when":"User asks for full address, phone, hours, and basic reviews.",
    "side_effects":"Calls provider APIs."
}))
async def get_details(
    place_id: Annotated[str, Field(description="Place id returned by find_nearby")],
    source: Annotated[str, Field(description="google | yelp | osm")] = "google",
) -> dict:
    if not place_id:
        raise McpError(ErrorData(code=INVALID_PARAMS, message="place_id is required"))
    if source == "google":
        details = await google_place_details(place_id)
        if not details:
            raise McpError(ErrorData(code=INTERNAL_ERROR, message="Google details unavailable or API key missing"))
        # Attribution requirement: indicate source
        details["attribution"] = "Results provided by Google Places"
        return details
    elif source == "yelp":
        # Yelp does not have a single details endpoint like Google (but business search returns many fields)
        # We'll attempt to fetch reviews and return minimal shape.
        reviews = await yelp_get_reviews(place_id, limit=5)
        return {"id": place_id, "reviews": reviews, "attribution": "Yelp Fusion"}
    elif source == "osm":
        # Overpass: get element by id (simple approach)
        # OSM id may be numeric; use Overpass to find element center
        try:
            eid = int(place_id)
        except Exception:
            raise McpError(ErrorData(code=INVALID_PARAMS, message="OSM place_id must be numeric"))
        q = f"""
        [out:json][timeout:25];
        (
          node({eid});
          way({eid});
          relation({eid});
        );
        out center;
        """
        url = "https://overpass-api.de/api/interpreter"
        async with httpx.AsyncClient(timeout=20) as client:
            try:
                resp = await client.post(url, data=q)
                resp.raise_for_status()
                data = resp.json()
            except Exception as e:
                raise McpError(ErrorData(code=INTERNAL_ERROR, message=f"Overpass error: {e}"))
        el = (data.get("elements") or [None])[0]
        if not el:
            raise McpError(ErrorData(code=INTERNAL_ERROR, message="OSM element not found"))
        tags = el.get("tags", {})
        return {
            "id": place_id,
            "name": tags.get("name"),
            "address": ", ".join(filter(None, [tags.get("addr:street"), tags.get("addr:housenumber"), tags.get("addr:city")])),
            "lat": el.get("lat") or el.get("center", {}).get("lat"),
            "lon": el.get("lon") or el.get("center", {}).get("lon"),
            "source": "osm",
            "attribution": "Data from OpenStreetMap",
        }
    else:
        raise McpError(ErrorData(code=INVALID_PARAMS, message="Unsupported source"))

# Tool: get_reviews
@mcp.tool(description=json.dumps({
    "description":"Fetch reviews for a place from the provider.",
    "use_when":"User wants to see user reviews for a place.",
    "side_effects":"Calls provider APIs and returns short review snippets."
}))
async def get_reviews(
    place_id: Annotated[str, Field(description="Place id")],
    source: Annotated[str, Field(description="google | yelp | osm")] = "google",
    limit: Annotated[int, Field(description="max reviews")] = 5,
) -> dict:
    if source == "google":
        details = await google_place_details(place_id)
        reviews = details.get("reviews", [])[:limit]
        return {"reviews": reviews, "attribution": "Google Places"}
    elif source == "yelp":
        reviews = await yelp_get_reviews(place_id, limit=limit)
        return {"reviews": reviews, "attribution": "Yelp Fusion"}
    elif source == "osm":
        return {"reviews": [], "attribution": "OpenStreetMap has no user review feed"}
    else:
        raise McpError(ErrorData(code=INVALID_PARAMS, message="Unsupported source"))

# Tool: summarize_reviews
@mcp.tool(description=json.dumps({
    "description":"Summarize and give quick sentiment for reviews of a place.",
    "use_when":"User wants an aggregated view of pros/cons from reviews.",
    "side_effects":"Reads multiple reviews and returns a short summary (heuristic)."
}))
async def summarize_reviews(
    place_id: Annotated[str, Field(description="Place id")],
    source: Annotated[str, Field(description="google | yelp | osm")] = "google",
    limit: Annotated[int, Field(description="max reviews to summarize")] = 10,
) -> dict:
    reviews_resp = await get_reviews(place_id=place_id, source=source, limit=limit)
    reviews = reviews_resp.get("reviews", [])
    summary = summarize_texts_simple(reviews)
    # Always attach provider attribution and a privacy note
    summary["attribution"] = reviews_resp.get("attribution")
    summary["privacy_note"] = "I only used public reviews from the provider. I do not store your coordinates beyond short-lived caching unless you opt in."
    return summary

# Keep your image tools and job_finder from original code (adapted)
# Minimal echo + job_finder and image tool
@mcp.tool(description="Echo text back")
async def echo(text: Annotated[str, Field(description="Text to echo")]) -> str:
    return f"Echo: {text}"

# Your convert image tool (keeps same signature)
MAKE_IMG_BLACK_AND_WHITE_DESCRIPTION = {
    "description":"Convert an image to black and white and save it.",
    "use_when":"Use this tool when the user provides an image URL and requests it to be converted to black and white.",
    "side_effects":"The image will be processed and saved in a black and white format."
}

@mcp.tool(description=json.dumps(MAKE_IMG_BLACK_AND_WHITE_DESCRIPTION))
async def make_img_black_and_white(
    puch_image_data: Annotated[str, Field(description="Base64-encoded image data")] = None,
) -> list[TextContent | ImageContent]:
    import base64, io
    from PIL import Image
    if not puch_image_data:
        raise McpError(ErrorData(code=INVALID_PARAMS, message="No image data provided"))
    try:
        image_bytes = base64.b64decode(puch_image_data)
        image = Image.open(io.BytesIO(image_bytes))
        bw = image.convert("L")
        buf = io.BytesIO()
        bw.save(buf, format="PNG")
        bw_b = buf.getvalue()
        bw_b64 = base64.b64encode(bw_b).decode("utf-8")
        return [ImageContent(type="image", mimeType="image/png", data=bw_b64)]
    except Exception as e:
        raise McpError(ErrorData(code=INTERNAL_ERROR, message=str(e)))

# Job finder tool (keeps simple behavior from your code)
JobFinderDescription = {
    "description":"Smart job tool: analyze descriptions, fetch URLs, or search jobs based on free text.",
    "use_when":"Use this to evaluate job descriptions or search for jobs using freeform goals.",
    "side_effects":"Returns insights, fetched job descriptions, or relevant job links.",
}

@mcp.tool(description=json.dumps(JobFinderDescription))
async def job_finder(
    user_goal: Annotated[str, Field(description="The user's goal")],
    job_description: Annotated[Optional[str], Field(description="Full job description text")] = None,
    job_url: Annotated[Optional[AnyUrl], Field(description="A URL to fetch a job description from")] = None,
    raw: Annotated[bool, Field(description="Return raw HTML content if True")] = False,
) -> str:
    if job_description:
        return f"📝 Job Analysis\n\n{job_description}\n\nGoal: {user_goal}"
    if job_url:
        content, _ = await Fetch.fetch_url(str(job_url), Fetch.USER_AGENT, force_raw=raw)
        return f"🔗 Fetched content from {job_url}\n\n{content}"
    # fallback search via DuckDuckGo HTML scrapping (limited)
    if "find" in user_goal.lower() or "look for" in user_goal.lower():
        ddg_url = f"https://html.duckduckgo.com/html/?q={user_goal.replace(' ', '+')}"
        async with httpx.AsyncClient() as client:
            resp = await client.get(ddg_url, headers={"User-Agent": Fetch.USER_AGENT})
            if resp.status_code != 200:
                return "Search failed"
            from bs4 import BeautifulSoup
            soup = BeautifulSoup(resp.text, "html.parser")
            links = []
            for a in soup.find_all("a", class_="result__a", href=True)[:5]:
                links.append(a["href"])
            return "Search results:\n" + "\n".join(links)
    raise McpError(ErrorData(code=INVALID_PARAMS, message="Provide job_description or job_url or a search-oriented user_goal"))

# Run server
async def main():
    print("🚀 Starting MCP server on http://0.0.0.0:8086 (dev). Puch requires HTTPS in production!")
    await mcp.run_async("streamable-http", host="0.0.0.0", port=8086)

if __name__ == "__main__":
    asyncio.run(main())
