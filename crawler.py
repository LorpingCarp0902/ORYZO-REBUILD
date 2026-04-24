#!/usr/bin/env python3
"""
Website crawler/mirror for https://www.on.energy/
Downloads all HTML pages, images, documents, CSS, JS, and other assets.
"""

import os
import re
import time
import hashlib
import mimetypes
import urllib.parse
from collections import deque
from pathlib import Path

import requests
from bs4 import BeautifulSoup

BASE_URL = "https://www.on.energy"
OUTPUT_DIR = Path(r"C:\Users\admin\Desktop\ONENERGY\site")
MAX_RETRIES = 3
DELAY = 0.5  # seconds between requests

HEADERS = {
    "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
                  "(KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36",
    "Accept": "text/html,application/xhtml+xml,application/xml;q=0.9,image/webp,*/*;q=0.8",
    "Accept-Language": "en-US,en;q=0.5",
}

DOWNLOAD_EXTENSIONS = {
    # Documents
    ".pdf", ".docx", ".doc", ".xlsx", ".xls", ".pptx", ".ppt",
    ".csv", ".txt", ".zip", ".rar",
    # Images
    ".png", ".jpg", ".jpeg", ".gif", ".svg", ".webp", ".ico", ".bmp", ".tiff",
    # Media
    ".mp4", ".mp3", ".webm", ".ogg",
    # Web assets
    ".css", ".js", ".woff", ".woff2", ".ttf", ".eot", ".otf",
    ".json", ".xml", ".map",
}

visited_urls = set()
downloaded_files = set()
failed_urls = []
stats = {"pages": 0, "assets": 0, "errors": 0, "skipped": 0}


def normalize_url(url, base=None):
    """Normalize a URL, resolving relative URLs against base."""
    if not url:
        return None
    url = url.strip()
    if url.startswith("//"):
        url = "https:" + url
    if url.startswith("#") or url.startswith("mailto:") or url.startswith("tel:") or url.startswith("javascript:"):
        return None
    if base:
        url = urllib.parse.urljoin(base, url)
    parsed = urllib.parse.urlparse(url)
    # Only follow links within the same domain
    base_parsed = urllib.parse.urlparse(BASE_URL)
    if parsed.netloc and parsed.netloc != base_parsed.netloc:
        return None  # External domain
    # Remove fragment
    url = urllib.parse.urlunparse(parsed._replace(fragment=""))
    return url


def url_to_filepath(url):
    """Convert a URL to a local file path."""
    parsed = urllib.parse.urlparse(url)
    path = parsed.path.lstrip("/")
    query = parsed.query

    if not path or path.endswith("/"):
        path = path + "index.html"
    elif "." not in Path(path).name:
        path = path + "/index.html"

    if query:
        safe_query = re.sub(r'[^\w=&.-]', '_', query)[:50]
        stem = Path(path).stem
        suffix = Path(path).suffix or ".html"
        path = str(Path(path).parent / f"{stem}_{safe_query}{suffix}")

    return OUTPUT_DIR / path


def download_file(url, session, is_page=False):
    """Download a file and save it to the appropriate local path."""
    if url in downloaded_files:
        stats["skipped"] += 1
        return None

    filepath = url_to_filepath(url)
    if filepath.exists() and filepath.stat().st_size > 0:
        downloaded_files.add(url)
        stats["skipped"] += 1
        return filepath

    for attempt in range(MAX_RETRIES):
        try:
            response = session.get(url, headers=HEADERS, timeout=30, stream=True)
            if response.status_code == 404:
                print(f"  [404] {url}")
                stats["errors"] += 1
                return None
            if response.status_code != 200:
                print(f"  [HTTP {response.status_code}] {url}")
                if attempt < MAX_RETRIES - 1:
                    time.sleep(2)
                    continue
                stats["errors"] += 1
                return None

            filepath.parent.mkdir(parents=True, exist_ok=True)

            with open(filepath, "wb") as f:
                for chunk in response.iter_content(chunk_size=8192):
                    f.write(chunk)

            downloaded_files.add(url)
            if is_page:
                stats["pages"] += 1
            else:
                stats["assets"] += 1
            return filepath

        except requests.exceptions.SSLError:
            print(f"  [SSL Error] {url} — retrying without verify")
            try:
                response = session.get(url, headers=HEADERS, timeout=30, stream=True, verify=False)
                if response.status_code == 200:
                    filepath.parent.mkdir(parents=True, exist_ok=True)
                    with open(filepath, "wb") as f:
                        for chunk in response.iter_content(chunk_size=8192):
                            f.write(chunk)
                    downloaded_files.add(url)
                    if is_page:
                        stats["pages"] += 1
                    else:
                        stats["assets"] += 1
                    return filepath
            except Exception as e2:
                print(f"  [Error] {url}: {e2}")
                stats["errors"] += 1
                return None

        except Exception as e:
            if attempt < MAX_RETRIES - 1:
                print(f"  [Retry {attempt+1}] {url}: {e}")
                time.sleep(2)
            else:
                print(f"  [Failed] {url}: {e}")
                failed_urls.append(url)
                stats["errors"] += 1
                return None

    return None


def extract_assets(soup, page_url):
    """Extract all asset URLs from a BeautifulSoup page."""
    assets = set()

    # Images
    for tag in soup.find_all(["img", "source"]):
        for attr in ["src", "srcset", "data-src", "data-lazy-src"]:
            val = tag.get(attr, "")
            if val:
                for u in val.split(","):
                    u = u.strip().split(" ")[0]
                    norm = normalize_url(u, page_url)
                    if norm:
                        assets.add(norm)

    # CSS
    for tag in soup.find_all("link", rel=lambda r: r and "stylesheet" in r):
        href = tag.get("href")
        norm = normalize_url(href, page_url)
        if norm:
            assets.add(norm)

    # Favicons and other link rels
    for tag in soup.find_all("link"):
        href = tag.get("href", "")
        if href:
            norm = normalize_url(href, page_url)
            if norm:
                ext = Path(urllib.parse.urlparse(norm).path).suffix.lower()
                if ext in DOWNLOAD_EXTENSIONS:
                    assets.add(norm)

    # Scripts
    for tag in soup.find_all("script", src=True):
        src = tag.get("src")
        norm = normalize_url(src, page_url)
        if norm:
            assets.add(norm)

    # Videos
    for tag in soup.find_all(["video", "audio"]):
        for attr in ["src", "poster"]:
            val = tag.get(attr)
            if val:
                norm = normalize_url(val, page_url)
                if norm:
                    assets.add(norm)

    # Generic data attributes (lazy loading)
    for tag in soup.find_all(True):
        for attr in ["data-src", "data-bg", "data-background", "data-image",
                     "data-lazy", "data-original", "data-url"]:
            val = tag.get(attr)
            if val and val.startswith("http"):
                norm = normalize_url(val, page_url)
                if norm:
                    assets.add(norm)

    # Background images in style attributes
    for tag in soup.find_all(style=True):
        matches = re.findall(r'url\(["\']?([^"\')\s]+)["\']?\)', tag["style"])
        for m in matches:
            norm = normalize_url(m, page_url)
            if norm:
                assets.add(norm)

    # Inline style tags
    for tag in soup.find_all("style"):
        matches = re.findall(r'url\(["\']?([^"\')\s]+)["\']?\)', tag.string or "")
        for m in matches:
            norm = normalize_url(m, page_url)
            if norm:
                assets.add(norm)

    # Document downloads
    for tag in soup.find_all("a", href=True):
        href = tag["href"]
        norm = normalize_url(href, page_url)
        if norm:
            ext = Path(urllib.parse.urlparse(norm).path).suffix.lower()
            if ext in {".pdf", ".docx", ".doc", ".xlsx", ".xls", ".pptx", ".ppt",
                       ".csv", ".zip", ".rar"}:
                assets.add(norm)

    return assets


def extract_links(soup, page_url):
    """Extract all internal page links for crawling."""
    links = set()
    for tag in soup.find_all("a", href=True):
        href = tag["href"]
        norm = normalize_url(href, page_url)
        if norm and norm not in visited_urls:
            ext = Path(urllib.parse.urlparse(norm).path).suffix.lower()
            # Only queue HTML pages (no extension or .html)
            if not ext or ext in {".html", ".htm", ".php", ".asp", ".aspx"}:
                links.add(norm)
    return links


def crawl():
    """Main crawl loop."""
    OUTPUT_DIR.mkdir(parents=True, exist_ok=True)
    session = requests.Session()
    session.headers.update(HEADERS)

    queue = deque([BASE_URL + "/"])
    visited_urls.add(BASE_URL + "/")

    print(f"Starting crawl of {BASE_URL}")
    print(f"Saving to: {OUTPUT_DIR}")
    print("-" * 60)

    while queue:
        url = queue.popleft()
        print(f"\n[Page {stats['pages']+1}] {url}")

        filepath = download_file(url, session, is_page=True)
        if not filepath:
            continue

        try:
            with open(filepath, "rb") as f:
                content = f.read()
            soup = BeautifulSoup(content, "html.parser")
        except Exception as e:
            print(f"  [Parse Error] {e}")
            continue

        time.sleep(DELAY)

        # Download all assets on this page
        assets = extract_assets(soup, url)
        print(f"  Found {len(assets)} assets")
        for asset_url in assets:
            if asset_url not in downloaded_files:
                print(f"  -> {asset_url}")
                download_file(asset_url, session)
                time.sleep(0.2)

        # Also download CSS @import and url() references from downloaded CSS files
        for asset_url in list(assets):
            ext = Path(urllib.parse.urlparse(asset_url).path).suffix.lower()
            if ext == ".css":
                css_path = url_to_filepath(asset_url)
                if css_path.exists():
                    try:
                        css_text = css_path.read_text(encoding="utf-8", errors="ignore")
                        css_urls = re.findall(r'url\(["\']?([^"\')\s]+)["\']?\)', css_text)
                        for cu in css_urls:
                            norm = normalize_url(cu, asset_url)
                            if norm and norm not in downloaded_files:
                                print(f"  -> [CSS] {norm}")
                                download_file(norm, session)
                                time.sleep(0.1)
                    except Exception:
                        pass

        # Queue new pages
        new_links = extract_links(soup, url)
        for link in new_links:
            if link not in visited_urls:
                visited_urls.add(link)
                queue.append(link)
                print(f"  + Queued: {link}")

    print("\n" + "=" * 60)
    print("CRAWL COMPLETE")
    print(f"  Pages downloaded:  {stats['pages']}")
    print(f"  Assets downloaded: {stats['assets']}")
    print(f"  Skipped (cached):  {stats['skipped']}")
    print(f"  Errors:            {stats['errors']}")
    print(f"  Total URLs visited: {len(visited_urls)}")
    if failed_urls:
        print(f"\nFailed URLs ({len(failed_urls)}):")
        for u in failed_urls[:20]:
            print(f"  {u}")
    print(f"\nFiles saved to: {OUTPUT_DIR}")


if __name__ == "__main__":
    import urllib3
    urllib3.disable_warnings(urllib3.exceptions.InsecureRequestWarning)
    crawl()
