import sqlite3
import xml.etree.ElementTree as ET
from pathlib import Path

from src.data import news

SCHEMA = Path(__file__).resolve().parents[1] / "db" / "schema.sql"

RSS = """<rss><channel>
<item><title>台積電法說 - 經濟日報</title><link>https://news.google.com/a</link>
<pubDate>Mon, 05 Oct 2026 23:00:00 GMT</pubDate></item>
<item><title>無媒體標題</title><link>https://news.google.com/b</link>
<pubDate>Tue, 06 Oct 2026 01:00:00 GMT</pubDate></item>
<item><title>壞日期</title><link>x</link><pubDate>garbage</pubDate></item>
</channel></rss>"""


def test_parse():
    items = news.parse_feed(ET.fromstring(RSS), "q")
    assert len(items) == 2
    assert items[0]["title"] == "台積電法說" and items[0]["publisher"] == "經濟日報"
    assert items[0]["published_at"] == "2026-10-05T23:00:00Z"
    assert items[1]["publisher"] is None


def test_url_encoded():
    assert "%E5%8F%B0" in news.build_url("台")


def test_fetch_failure_degrades():
    def boom(url):
        raise OSError("down")
    assert news.fetch_news("x", fetch=boom) == []


def test_fetch_ok_and_save_dedup():
    items = news.fetch_news("q", fetch=lambda u: ET.fromstring(RSS))
    conn = sqlite3.connect(":memory:")
    conn.executescript(SCHEMA.read_text(encoding="utf-8"))
    assert news.save_news(conn, items) == 2
    assert news.save_news(conn, items) == 0
    assert conn.execute("SELECT COUNT(*) FROM news").fetchone()[0] == 2
