"""News via Google News RSS (no token). Failures degrade to an empty list."""
import datetime as dt
import email.utils
import urllib.parse

from src.http import fetch_xml

URL = "https://news.google.com/rss/search?q={q}+when:1d&hl=zh-TW&gl=TW&ceid=TW:zh-Hant"


def build_url(query):
    return URL.format(q=urllib.parse.quote(query))


def _iso(pub):
    try:
        d = email.utils.parsedate_to_datetime(pub)
    except (TypeError, ValueError):
        return None
    if d.tzinfo is None:
        d = d.replace(tzinfo=dt.timezone.utc)
    return d.astimezone(dt.timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")


def parse_feed(root, query=None):
    items = []
    for it in root.iter("item"):
        title = (it.findtext("title") or "").strip()
        link = (it.findtext("link") or "").strip()
        pub = _iso(it.findtext("pubDate"))
        if not title or not link or not pub:
            continue
        publisher = None
        if " - " in title:
            title, publisher = title.rsplit(" - ", 1)
            title, publisher = title.strip(), publisher.strip() or None
        items.append({"published_at": pub, "title": title, "link": link,
                      "publisher": publisher, "query": query})
    return items


def fetch_news(query, fetch=fetch_xml):
    try:
        return parse_feed(fetch(build_url(query)), query)
    except Exception:
        return []


def save_news(conn, items):
    now = dt.datetime.now(dt.timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")
    before = conn.total_changes
    conn.executemany(
        "INSERT OR IGNORE INTO news (published_at, title, link, publisher, query, fetched_at) "
        "VALUES (?,?,?,?,?,?)",
        [(i["published_at"], i["title"], i["link"], i["publisher"], i["query"], now) for i in items])
    conn.commit()
    return conn.total_changes - before
