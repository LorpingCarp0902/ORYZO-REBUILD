#!/usr/bin/env python3
"""
Website crawler/mirror for https://oryzo.ai/
Downloads all HTML pages, images, documents, CSS, JS, and other assets.
"""

import os
import re
import time
import urllib.parse
from collections import deque
from pathlib import Path

import requests
from bs4 import BeautifulSoup

BASE_URL = "https://oryzo.ai"
OUTPUT_DIR = Path(r"C:\Users\admin\Desktop\oryzo\site")
MAX_RETRIES = 3
DELAY = 0.3

HEADERS = {
    "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
                  "(KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36",
    "Accept": "text/html,application/xhtml+xml,application/xml;q=0.9,image/webp,*/*;q=0.8",
    "Accept-Language": "en-US,en;q=0.5",
}

DOWNLOAD_EXTENSIONS = {
    ".pdf", ".docx", ".doc", ".xlsx", ".xls", ".pptx", ".ppt",
    ".csv", ".txt", ".zip", ".rar",
    ".png", ".jpg", ".jpeg", ".gif", ".svg", ".webp", ".ico", ".bmp", ".tiff",
    ".mp4", ".mp3", ".webm", ".ogg", ".mov", ".avi",
    ".css", ".js", ".mjs", ".woff", ".woff2", ".ttf", ".eot", ".otf",
    ".json", ".xml", ".map",
}

DOCUMENT_EXTENSIONS = {
    ".pdf", ".docx", ".doc", ".xlsx", ".xls", ".pptx", ".ppt",
    ".csv", ".zip", ".rar", ".txt",
}

visited_urls = set()
downloaded_files = set()
failed_urls = []
stats = {"pages": 0, "assets": 0, "errors": 0, "skipped": 0}


def normalize_url(url, base=None):
    if not url:
        return None
    url = url.strip()
    if url.startswith("//"):
        url = "https:" + url
    if url.startswith("#") or url.startswith("mailto:") or url.startswith("tel:") or url.startswith("javascript:") or url.startswith("data:"):
        return None
    if base:
        url = urllib.parse.urljoin(base, url)
    parsed = urllib.parse.urlparse(url)
    if not parsed.scheme.startswith("http"):
        return None
    base_parsed = urllib.parse.urlparse(BASE_URL)
    # allow same domain and www/non-www variants
    netloc = parsed.netloc.lower()
    base_netloc = base_parsed.netloc.lower()
    if netloc and netloc != base_netloc and netloc != "www." + base_netloc and "www." + netloc != base_netloc:
        return None
    url = urllib.parse.urlunparse(parsed._replace(fragment=""))
    return url


def url_to_filepath(url):
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
    assets = set()

    for tag in soup.find_all(["img", "source"]):
        for attr in ["src", "srcset", "data-src", "data-lazy-src", "data-srcset"]:
            val = tag.get(attr, "")
            if val:
                for u in val.split(","):
                    u = u.strip().split(" ")[0]
                    norm = normalize_url(u, page_url)
                    if norm:
                        assets.add(norm)

    for tag in soup.find_all("link", rel=lambda r: r and "stylesheet" in r):
        href = tag.get("href")
        norm = normalize_url(href, page_url)
        if norm:
            assets.add(norm)

    for tag in soup.find_all("link"):
        href = tag.get("href", "")
        if href:
            norm = normalize_url(href, page_url)
            if norm:
                ext = Path(urllib.parse.urlparse(norm).path).suffix.lower()
                if ext in DOWNLOAD_EXTENSIONS:
                    assets.add(norm)

    for tag in soup.find_all("script", src=True):
        src = tag.get("src")
        norm = normalize_url(src, page_url)
        if norm:
            assets.add(norm)

    for tag in soup.find_all(["video", "audio"]):
        for attr in ["src", "poster"]:
            val = tag.get(attr)
            if val:
                norm = normalize_url(val, page_url)
                if norm:
                    assets.add(norm)

    for tag in soup.find_all(True):
        for attr in ["data-src", "data-bg", "data-background", "data-image",
                     "data-lazy", "data-original", "data-url", "data-poster"]:
            val = tag.get(attr)
            if val and (val.startswith("http") or val.startswith("/")):
                norm = normalize_url(val, page_url)
                if norm:
                    assets.add(norm)

    for tag in soup.find_all(style=True):
        matches = re.findall(r'url\(["\']?([^"\')\s]+)["\']?\)', tag["style"])
        for m in matches:
            norm = normalize_url(m, page_url)
            if norm:
                assets.add(norm)

    for tag in soup.find_all("style"):
        matches = re.findall(r'url\(["\']?([^"\')\s]+)["\']?\)', tag.string or "")
        for m in matches:
            norm = normalize_url(m, page_url)
            if norm:
                assets.add(norm)

    for tag in soup.find_all("a", href=True):
        href = tag["href"]
        norm = normalize_url(href, page_url)
        if norm:
            ext = Path(urllib.parse.urlparse(norm).path).suffix.lower()
            if ext in DOCUMENT_EXTENSIONS or ext in {".png", ".jpg", ".jpeg", ".gif", ".svg", ".webp", ".ico"}:
                assets.add(norm)

    return assets


def extract_links(soup, page_url):
    links = set()
    for tag in soup.find_all("a", href=True):
        href = tag["href"]
        norm = normalize_url(href, page_url)
        if norm and norm not in visited_urls:
            ext = Path(urllib.parse.urlparse(norm).path).suffix.lower()
            if not ext or ext in {".html", ".htm", ".php", ".asp", ".aspx"}:
                links.add(norm)
    return links


def crawl():
    OUTPUT_DIR.mkdir(parents=True, exist_ok=True)
    session = requests.Session()
    session.headers.update(HEADERS)

    start_urls = [BASE_URL + "/", BASE_URL + "/sitemap.xml", BASE_URL + "/robots.txt"]
    queue = deque([BASE_URL + "/"])
    visited_urls.add(BASE_URL + "/")

    # Try to seed from sitemap
    try:
        sm = session.get(BASE_URL + "/sitemap.xml", headers=HEADERS, timeout=15)
        if sm.status_code == 200:
            (OUTPUT_DIR / "sitemap.xml").write_bytes(sm.content)
            urls_in_sm = re.findall(r"<loc>([^<]+)</loc>", sm.text)
            for u in urls_in_sm:
                norm = normalize_url(u)
                if norm and norm not in visited_urls:
                    visited_urls.add(norm)
                    queue.append(norm)
            print(f"Seeded {len(urls_in_sm)} URLs from sitemap.xml")
    except Exception as e:
        print(f"Sitemap fetch failed: {e}")

    try:
        rb = session.get(BASE_URL + "/robots.txt", headers=HEADERS, timeout=15)
        if rb.status_code == 200:
            (OUTPUT_DIR / "robots.txt").write_bytes(rb.content)
    except Exception:
        pass

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

        assets = extract_assets(soup, url)
        print(f"  Found {len(assets)} assets")
        for asset_url in assets:
            if asset_url not in downloaded_files:
                download_file(asset_url, session)
                time.sleep(0.1)

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
                                download_file(norm, session)
                                time.sleep(0.05)
                    except Exception:
                        pass

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
