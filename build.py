"""Build the On Purpose reading room from Gage Cass Woodle's Substack feed.

Every run:
  1. Reads the feed (or a local file passed as the first argument, for testing).
  2. Cleans each piece: no subscribe or share buttons, no comment links,
     no Substack image chrome, no tracking parameters.
  3. Downloads each image so readers never load anything from Substack.
  4. Saves each piece to posts/<slug>.json. Saved pieces are kept forever,
     so a piece that ages out of the feed stays on the site.
  5. Writes the finished site into site/.

Standard library plus BeautifulSoup. No trackers, no scripts on the pages.
"""

import email.utils
import hashlib
import html
import json
import re
import shutil
import sys
import urllib.parse
import urllib.request
import xml.etree.ElementTree as ET
from datetime import datetime, timezone
from pathlib import Path

from bs4 import BeautifulSoup, Comment

FEED_URL = "https://gagecasswoodle.substack.com/feed"
SUBSTACK_HOST = "gagecasswoodle.substack.com"
DOMAIN = "words.woodle.quest"
SITE_TITLE = "On Purpose"
AUTHOR = "Gage Cass Woodle"
# The piece every new reader should meet first. Change the slug to re-pin.
START_HERE = "the-scarlet-letter"
TAGLINE = "Writing by Gage Cass Woodle. No account, no app, no feed. Just the words."

ROOT = Path(__file__).resolve().parent
POSTS = ROOT / "posts"
IMAGES = ROOT / "images"
MEDIA = ROOT / "media"
STATIC = ROOT / "static"
OUT = ROOT / "site"

NS = {"content": "http://purl.org/rss/1.0/modules/content/",
      "dc": "http://purl.org/dc/elements/1.1/"}
UA = "Mozilla/5.0 (compatible; OnPurposeReadingRoom/1.0; +https://words.woodle.quest)"

VIDEO_NOTE = "A video goes with this piece. For now it plays only on Substack."


# ---------- fetching ----------

def fetch(url, binary=False):
    accept = "image/avif,image/webp,image/*,*/*" if binary else "application/rss+xml, application/xml;q=0.9, */*;q=0.8"
    req = urllib.request.Request(url, headers={"User-Agent": UA, "Accept": accept})
    with urllib.request.urlopen(req, timeout=60) as r:
        data = r.read()
        return (data, r.headers.get("Content-Type", "")) if binary else data.decode("utf-8")


def read_feed(source):
    if source and Path(source).exists():
        return Path(source).read_text(encoding="utf-8")
    return fetch(FEED_URL)


# ---------- cleaning ----------

def strip_tracking(url):
    p = urllib.parse.urlsplit(url)
    q = [(k, v) for k, v in urllib.parse.parse_qsl(p.query, keep_blank_values=True)
         if not k.lower().startswith("utm_") and k not in ("action", "r", "triedRedirect")]
    return urllib.parse.urlunsplit((p.scheme, p.netloc, p.path, urllib.parse.urlencode(q), p.fragment))


def own_post_slug(url):
    p = urllib.parse.urlsplit(url)
    if p.netloc == SUBSTACK_HOST:
        m = re.match(r"^/p/([^/?#]+)", p.path)
        if m:
            return m.group(1)
    return None


def image_ext(content_type, url):
    ct = content_type.split(";")[0].strip().lower()
    known = {"image/jpeg": ".jpg", "image/png": ".png", "image/webp": ".webp",
             "image/gif": ".gif", "image/avif": ".avif", "image/svg+xml": ".svg"}
    if ct in known:
        return known[ct]
    m = re.search(r"\.(jpe?g|png|webp|gif)(?:$|[?#])", url, re.I)
    return "." + m.group(1).lower().replace("jpeg", "jpg") if m else ".jpg"


def localize_image(src):
    """Download an image once; return its path on our site, or None on failure."""
    key = hashlib.sha256(src.encode()).hexdigest()[:16]
    IMAGES.mkdir(exist_ok=True)
    existing = list(IMAGES.glob(key + ".*"))
    if existing:
        return "/images/" + existing[0].name
    try:
        data, ctype = fetch(src, binary=True)
    except Exception as e:  # keep building; a missing image should not sink a piece
        print(f"  image failed: {src[:80]}... ({e})")
        return None
    name = key + image_ext(ctype, src)
    (IMAGES / name).write_bytes(data)
    return "/images/" + name


def clean(body_html, known_slugs):
    soup = BeautifulSoup(body_html, "html.parser")

    for c in soup.find_all(string=lambda s: isinstance(s, Comment)):
        c.extract()

    # Substack's calls to action and widgets.
    selectors = [
        ".subscription-widget-wrap-editor", ".subscription-widget-wrap",
        ".subscription-widget", "p.button-wrapper", "[data-component-name=ButtonCreateButton]",
        ".image-link-expand", ".captioned-button-wrap", ".share-dialog",
        "form", "button", "svg", "input", "script", "style", "iframe",
    ]
    for sel in selectors:
        for el in soup.select(sel):
            el.decompose()

    # Native videos: play our own copy from media/<upload id>.mp4 when we have
    # one; otherwise say plainly that the video lives on Substack.
    for el in soup.select(".native-video-embed, [data-component-name=VideoPlaceholder]"):
        try:
            upload_id = json.loads(el.get("data-attrs", "{}")).get("mediaUploadId", "")
        except ValueError:
            upload_id = ""
        if upload_id and (MEDIA / f"{upload_id}.mp4").exists():
            video = soup.new_tag("video", controls="", preload="none", playsinline="")
            video["src"] = f"/media/{upload_id}.mp4"
            if (MEDIA / f"{upload_id}.jpg").exists():
                video["poster"] = f"/media/{upload_id}.jpg"
            if (MEDIA / f"{upload_id}.vtt").exists():
                track = soup.new_tag("track", kind="captions", srclang="en", label="English")
                track["src"] = f"/media/{upload_id}.vtt"
                video.append(track)
            video.append("Your browser cannot play this video.")
            el.replace_with(video)
            transcript = MEDIA / f"{upload_id}.txt"
            if transcript.exists():
                details = soup.new_tag("details")
                details["class"] = "transcript"
                summary = soup.new_tag("summary")
                summary.string = "Read the transcript"
                details.append(summary)
                label = soup.new_tag("p")
                label["class"] = "note"
                label.string = "Transcribed by machine from the video. Where a word matters, the video is the record."
                details.append(label)
                for para in transcript.read_text(encoding="utf-8").split("\n\n"):
                    if para.strip():
                        tp = soup.new_tag("p")
                        tp.string = para.strip()
                        details.append(tp)
                video.insert_after(details)
        else:
            note = soup.new_tag("p")
            note["class"] = "note"
            note.string = VIDEO_NOTE
            el.replace_with(note)

    # Images: unwrap the lightbox link, flatten <picture>, keep one local copy.
    for a in soup.select("a.image-link"):
        a.unwrap()
    for div in soup.select(".image2-inset, .captioned-image-container"):
        div.unwrap()
    for pic in soup.find_all("picture"):
        img = pic.find("img")
        if img:
            pic.replace_with(img)
        else:
            pic.decompose()
    for img in soup.find_all("img"):
        src = img.get("src", "")
        local = localize_image(src) if src.startswith("http") else None
        if not local:
            img.decompose()
            continue
        alt = img.get("alt", "")
        w, h = img.get("width"), img.get("height")
        img.attrs = {"src": local, "alt": alt, "loading": "lazy", "decoding": "async"}
        if w and h:
            img.attrs.update({"width": w, "height": h})
    for fig in soup.find_all("figure"):
        if not fig.find("img"):
            fig.decompose()  # an image that could not be saved takes its caption with it

    # Links: own pieces point inside the room; everything else loses tracking.
    for a in soup.find_all("a"):
        href = a.get("href", "")
        slug = own_post_slug(href)
        if slug and slug in known_slugs:
            frag = urllib.parse.urlsplit(href).fragment
            a.attrs = {"href": f"/{slug}/" + (f"#{frag}" if frag else "")}
        elif urllib.parse.urlsplit(href).netloc == SUBSTACK_HOST:
            # Subscribe, comment and share links. A link standing alone in its
            # paragraph is a button in disguise: remove it. Inside a sentence,
            # keep the words and drop the door.
            parent = a.parent
            if parent is not None and parent.name == "p" and parent.get_text(strip=True) == a.get_text(strip=True):
                parent.decompose()
            else:
                a.unwrap()
        elif href.startswith("http"):
            a.attrs = {"href": strip_tracking(href), "rel": "noopener"}
        elif href.startswith("#"):
            keep = {"href": href}
            if a.get("id"):
                keep["id"] = a["id"]
            if "footnote-anchor" in (a.get("class") or []):
                keep["class"] = "footnote-anchor"
            a.attrs = keep

    # Bare web addresses in the text become links.
    url_re = re.compile(r"https?://[^\s<>()]+[^\s<>().,;:!?'\"]")
    for node in list(soup.find_all(string=url_re)):
        if node.find_parent("a"):
            continue
        parts, last = [], 0
        for m in url_re.finditer(node):
            parts.append(node[last:m.start()])
            link = soup.new_tag("a", href=strip_tracking(m.group(0)), rel="noopener")
            link.string = m.group(0)
            parts.append(link)
            last = m.end()
        parts.append(node[last:])
        for part in parts:
            node.insert_before(part)
        node.extract()

    # Drop Substack's attributes everywhere else.
    for el in soup.find_all(True):
        if el.name in ("img", "a", "video", "track"):
            continue
        keep = {}
        if el.get("id"):
            keep["id"] = el["id"]
        cls = el.get("class") or []
        if isinstance(cls, str):
            cls = cls.split()
        classes = [c for c in cls if c in ("note", "footnote", "footnote-content", "transcript")]
        if classes:
            keep["class"] = " ".join(classes)
        el.attrs = keep

    # Empty paragraphs and wrappers.
    for p in soup.find_all(["p", "div"]):
        if not p.get_text(strip=True) and not p.find(["img", "hr", "br"]):
            p.decompose()

    return str(soup).strip()


# ---------- feed parsing ----------

def parse_feed(xml_text):
    root = ET.fromstring(xml_text)
    items = []
    for it in root.iter("item"):
        link = (it.findtext("link") or "").strip()
        slug = own_post_slug(link)
        if not slug:
            continue
        pub = it.findtext("pubDate") or ""
        dt = email.utils.parsedate_to_datetime(pub) if pub else datetime.now(timezone.utc)
        items.append({
            "slug": slug,
            "title": html.unescape((it.findtext("title") or "").strip()),
            "subtitle": html.unescape((it.findtext("description") or "").strip()),
            "date": dt.astimezone(timezone.utc).isoformat(),
            "source": link,
            "raw": it.findtext("content:encoded", namespaces=NS) or "",
        })
    return items


# ---------- rendering ----------

def esc(s):
    return html.escape(s, quote=True)


def nice_date(iso):
    d = datetime.fromisoformat(iso)
    return f"{d.strftime('%B')} {d.day}, {d.year}"


def words(body):
    return len(BeautifulSoup(body, "html.parser").get_text(" ").split())


def page(title, description, inner, canonical):
    return f"""<!doctype html>
<html lang="en">
<head>
<meta charset="utf-8">
<meta name="viewport" content="width=device-width, initial-scale=1">
<title>{esc(title)}</title>
<meta name="description" content="{esc(description)}">
<meta name="referrer" content="no-referrer">
<link rel="canonical" href="https://{DOMAIN}{canonical}">
<link rel="stylesheet" href="/style.css">
<link rel="icon" href="/favicon.svg" type="image/svg+xml">
</head>
<body>
{inner}
</body>
</html>
"""


def render_index(posts):
    pinned = next((p for p in posts if p["slug"] == START_HERE), None)
    start = ""
    if pinned:
        start = f"""  <a class="start" href="/{esc(pinned['slug'])}/">
    <span class="label">Start here</span>
    <span class="t">{esc(pinned['title'])}</span>
    {f'<span class="s">{esc(pinned["subtitle"])}</span>' if pinned['subtitle'] else ''}
  </a>
"""
    rows = []
    for p in posts:
        if p is pinned:
            continue
        rows.append(f"""    <li>
      <a href="/{esc(p['slug'])}/">
        <span class="t">{esc(p['title'])}</span>
        {f'<span class="s">{esc(p["subtitle"])}</span>' if p['subtitle'] else ''}
        <time datetime="{p['date'][:10]}">{nice_date(p['date'])}</time>
      </a>
    </li>""")
    inner = f"""<main class="wrap">
  <header class="masthead">
    <p class="kicker">words.woodle.quest</p>
    <h1>{SITE_TITLE}</h1>
    <p class="tagline">{esc(TAGLINE)}</p>
  </header>
{start}  <ol class="pieces">
{chr(10).join(rows)}
  </ol>
  <footer class="foot">
    <p>Human for the seeing, the seeding and the conducting. Lumen for the research and words.</p>
    <p>The process behind each piece is public: <a href="https://ethicsbuild.github.io/Receipts/published/" rel="noopener">the receipts</a>.</p>
  </footer>
</main>"""
    return page(f"{SITE_TITLE} | {AUTHOR}", TAGLINE, inner, "/")


def render_post(p, prev_p, next_p):
    nav = []
    if next_p:
        nav.append(f'<a class="newer" href="/{esc(next_p["slug"])}/"><span>Newer</span>{esc(next_p["title"])}</a>')
    if prev_p:
        nav.append(f'<a class="older" href="/{esc(prev_p["slug"])}/"><span>Older</span>{esc(prev_p["title"])}</a>')
    minutes = max(1, round(words(p["body"]) / 230))
    inner = f"""<main class="wrap">
  <nav class="top"><a href="/">{SITE_TITLE}</a></nav>
  <article>
    <header class="piece-head">
      <h1>{esc(p['title'])}</h1>
      {f'<p class="sub">{esc(p["subtitle"])}</p>' if p['subtitle'] else ''}
      <p class="meta">{AUTHOR} <span aria-hidden="true">&middot;</span> <time datetime="{p['date'][:10]}">{nice_date(p['date'])}</time> <span aria-hidden="true">&middot;</span> {minutes} min read</p>
    </header>
    <div class="body">
{p['body']}
    </div>
  </article>
  <nav class="pager">{''.join(nav)}</nav>
  <footer class="foot"><p><a href="/">All pieces</a></p></footer>
</main>"""
    return page(f"{p['title']} | {SITE_TITLE}", p["subtitle"] or p["title"], inner, f"/{p['slug']}/")


# ---------- main ----------

def main():
    source = sys.argv[1] if len(sys.argv) > 1 else None
    POSTS.mkdir(exist_ok=True)

    try:
        feed_items = parse_feed(read_feed(source))
    except Exception as e:
        # Substack refuses some servers. Copies of the feed saved by hand go in seed/.
        print(f"FEED UNREACHABLE: {e}")
        feed_items = []
        for f in sorted((ROOT / "seed").glob("*.xml")):
            feed_items += parse_feed(f.read_text(encoding="utf-8"))
            print(f"Using saved copy: {f.name}")
        if not feed_items and not any(POSTS.glob("*.json")):
            raise SystemExit("No saved pieces and no feed. Nothing to publish.")
    saved = {f.stem: json.loads(f.read_text()) for f in POSTS.glob("*.json")}
    known = set(saved) | {i["slug"] for i in feed_items}

    for item in feed_items:
        body = clean(item.pop("raw"), known)
        record = {**item, "body": body}
        if saved.get(item["slug"]) != record:
            (POSTS / f"{item['slug']}.json").write_text(json.dumps(record, indent=1, ensure_ascii=False))
            print(f"  updated: {item['title']}")
        saved[item["slug"]] = record

    posts = sorted(saved.values(), key=lambda p: p["date"], reverse=True)

    if OUT.exists():
        shutil.rmtree(OUT)
    OUT.mkdir()
    for f in STATIC.iterdir():
        shutil.copy(f, OUT / f.name)
    if IMAGES.exists():
        shutil.copytree(IMAGES, OUT / "images")
    if MEDIA.exists():
        shutil.copytree(MEDIA, OUT / "media")
    (OUT / "CNAME").write_text(DOMAIN + "\n")
    (OUT / "index.html").write_text(render_index(posts))
    for i, p in enumerate(posts):
        d = OUT / p["slug"]
        d.mkdir()
        older = posts[i + 1] if i + 1 < len(posts) else None
        newer = posts[i - 1] if i > 0 else None
        (d / "index.html").write_text(render_post(p, older, newer))
    print(f"Built {len(posts)} pieces into {OUT}")


if __name__ == "__main__":
    main()
