"""Scrape ctrcustomtracks.com track listing with a headed browser.

The site sits behind a SiteGround captcha, so this opens a visible Chromium
window; solve the captcha by hand and the script carries on by itself.
The browser profile is kept in .browser-profile/ so later runs reuse the cookie.
Optional: tracks can also be downloaded by hand and dropped into tracks_in/.

Usage:
  python scripts/scrape_tracks.py probe   # dump /tracks/ html + links for inspection
  python scripts/scrape_tracks.py list    # write tracks.json from listing + track pages
  python scripts/scrape_tracks.py download  # fetch each track's patch into tracks_in/<slug>/
"""
import json
import re
import pathlib
import sys
import time

from playwright.sync_api import sync_playwright

ROOT = pathlib.Path(__file__).resolve().parent.parent
PROFILE = ROOT / ".browser-profile"
PROBE_DIR = ROOT / "raw" / "probe"
TRACKS_JSON = ROOT / "tracks.json"
BASE = "https://ctrcustomtracks.com"


def wait_past_captcha(page, timeout_s=600):
    """Block until the page is no longer the captcha interstitial."""
    start = time.time()
    warned = False
    while time.time() - start < timeout_s:
        if "sgcaptcha" not in page.url and page.query_selector("a[href*='ctrcustomtracks.com/']"):
            return
        if not warned:
            print(">>> Solve the captcha in the browser window...", flush=True)
            warned = True
        time.sleep(1)
    raise TimeoutError("captcha not solved in time")


def goto(page, url):
    page.goto(url, wait_until="domcontentloaded")
    wait_past_captcha(page)
    page.wait_for_load_state("networkidle")


def all_links(page):
    return page.eval_on_selector_all(
        "a[href]", "els => els.map(e => ({href: e.href, text: e.innerText.trim()}))")


def probe(page):
    PROBE_DIR.mkdir(parents=True, exist_ok=True)
    goto(page, BASE + "/tracks/")
    # scroll to trigger any lazy/infinite loading
    for _ in range(30):
        page.mouse.wheel(0, 4000)
        time.sleep(0.3)
    page.wait_for_load_state("networkidle")
    (PROBE_DIR / "tracks.html").write_text(page.content())
    (PROBE_DIR / "tracks_links.json").write_text(json.dumps(all_links(page), indent=1))
    print("probe saved to", PROBE_DIR)


NON_TRACK = {"", "tracks", "category", "submit", "creators", "downloads", "tutorial", "user", "about", "contact", "privacy-policy"}


def track_slugs():
    links = json.loads((PROBE_DIR / "tracks_links.json").read_text())
    seen = {}
    for l in links:
        h = l["href"].split("#")[0]
        if not h.startswith(BASE + "/") or h.count("/") != 4:
            continue
        slug = h[len(BASE) + 1:].strip("/")
        if slug in NON_TRACK or not l["text"]:
            continue
        seen.setdefault(slug, l["text"])
    return seen


def page_details(links):
    """(author, download links) from a track page's links: the creator's /author/
    link, and its "Download ..." buttons, bot (AI) patches first."""
    author = next((l["text"] for l in links if "/author/" in l["href"] and l["text"]), "")
    dls = [l for l in links if l["text"].lower().startswith("download") and "ctrcustomtracks.com" not in l["href"]
           and not re.search(r"source|\.lev", l["text"], re.I)]
    dls.sort(key=lambda l: "bot" not in l["text"].lower())
    return author, [l["href"] for l in dls]


def list_tracks(page):
    """Visit every track page, keep its title, author and content links."""
    out = json.loads(TRACKS_JSON.read_text()) if TRACKS_JSON.exists() else {}
    slugs = track_slugs()
    print(len(slugs), "track pages")
    for i, (slug, name) in enumerate(slugs.items()):
        if slug in out:
            continue
        goto(page, f"{BASE}/{slug}/")
        main = page.query_selector("main") or page.query_selector("article") or page.query_selector("body")
        links = main.eval_on_selector_all(
            "a[href]", "els => els.map(e => ({href: e.href, text: e.innerText.trim()}))")
        text = main.inner_text()
        author, dls = page_details(links)
        out[slug] = {"name": name, "page_url": f"{BASE}/{slug}/", "author": author,
                     "download_url": dls[0] if dls else None, "alt_downloads": dls[1:], "text": text[:3000]}
        TRACKS_JSON.write_text(json.dumps(out, indent=1, ensure_ascii=False))
        print(f"[{i+1}/{len(slugs)}] {slug}: {len(links)} links", flush=True)


DL_DIR = ROOT / "tracks_in"
PATCH_EXT = (".xdelta", ".vcdiff", ".zip", ".7z", ".rar", ".ppf", ".bps")


CONSENT_BUTTONS = [
    "button:has-text('Accept all')", "button:has-text('Accept All')", "button:has-text('Accept')",
    "button:has-text('I agree')", "button:has-text('Agree')", "button:has-text('AGREE')",
    "button:has-text('Consent')", "button:has-text('Got it')", "#onetrust-accept-btn-handler",
    ".fc-cta-consent", "button[mode=primary]",
]


def accept_cookies(page):
    """Dismiss cookie/consent banners (user asked to accept them), incl. inside iframes."""
    for frame in page.frames:
        for sel in CONSENT_BUTTONS:
            try:
                el = frame.query_selector(sel)
                if el and el.is_visible():
                    el.click(timeout=2000)
                    time.sleep(0.5)
                    return True
            except Exception:
                pass
    return False


def save_download(dl, dest_dir):
    """Save as tracks_in/<slug>/<slug><ext>, keeping the original name in a sidecar."""
    dest_dir.mkdir(parents=True, exist_ok=True)
    orig = dl.suggested_filename
    ext = "".join(pathlib.Path(orig).suffixes[-1:]).lower() or ".bin"
    target = dest_dir / (dest_dir.name + ext)
    (dest_dir / "original_name.txt").write_text(orig + "\n")
    dl.save_as(str(target))
    return target


def click_download(page, selector, dest_dir, timeout=120_000):
    accept_cookies(page)
    with page.expect_download(timeout=timeout) as info:
        page.click(selector)
    return save_download(info.value, dest_dir)


def fetch_one(page, url, dest_dir):
    """Resolve a host landing page to the actual file."""
    if "drive.google.com" in url:
        m = re.search(r"/d/([^/]+)|[?&]id=([^&]+)", url)
        fid = next(g for g in m.groups() if g)
        url = f"https://drive.google.com/uc?export=download&id={fid}"
    # landing pages with a download button rarely start the file themselves
    direct_wait = 5_000 if ("mediafire.com" in url or "gamebanana.com" in url) else 15_000
    try:
        with page.expect_download(timeout=direct_wait) as info:
            try:
                page.goto(url, wait_until="domcontentloaded")
            except Exception:
                pass  # goto raises when the navigation turns into a download
        return save_download(info.value, dest_dir)
    except Exception:
        pass
    wait_past_captcha_host(page)
    for sel in ["a#downloadButton", "form#download-form button, #uc-download-link, input[type=submit][value*=ownload]",
                "a[href$='.xdelta'], a[href$='.zip'], a[href$='.7z'], a[href*='/dl/'], a[href*='releases/download']"]:
        if page.query_selector(sel):
            return click_download(page, sel, dest_dir)
    raise RuntimeError(f"no download control found on {page.url}")


def wait_past_captcha_host(page):
    page.wait_for_load_state("domcontentloaded")
    try:
        page.wait_for_load_state("networkidle", timeout=15_000)  # GameBanana renders its file list late
    except Exception:
        pass
    accept_cookies(page)


def download_all(page):
    tracks = json.loads(TRACKS_JSON.read_text())
    fails = []
    for i, (slug, t) in enumerate(tracks.items()):
        dest = DL_DIR / slug
        if (dest.exists() and any(f.suffix.lower() in PATCH_EXT for f in dest.iterdir())) or not t.get("download_url"):
            continue
        try:
            f = fetch_one(page, t["download_url"], dest)
            t["file"] = str(f.relative_to(ROOT))
            print(f"[{i+1}/{len(tracks)}] {slug}: {f.name} ({f.stat().st_size} B)", flush=True)
        except Exception as e:
            fails.append((slug, t["download_url"], str(e)[:200]))
            print(f"[{i+1}/{len(tracks)}] {slug}: FAILED {e}", flush=True)
        TRACKS_JSON.write_text(json.dumps(tracks, indent=1, ensure_ascii=False))
    (DL_DIR / "failed.json").write_text(json.dumps(fails, indent=1))
    print(len(fails), "failed")


def main():
    mode = sys.argv[1] if len(sys.argv) > 1 else "probe"
    with sync_playwright() as p:
        ctx = p.chromium.launch_persistent_context(str(PROFILE), headless=False, accept_downloads=True)
        page = ctx.pages[0] if ctx.pages else ctx.new_page()
        if mode == "probe":
            probe(page)
        elif mode == "list":
            list_tracks(page)
        elif mode == "download":
            download_all(page)
        else:
            raise SystemExit(f"unknown mode {mode}")
        ctx.close()


if __name__ == "__main__":
    main()
