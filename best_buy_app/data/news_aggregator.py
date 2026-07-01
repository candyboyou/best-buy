#!/usr/bin/env python3
# -*- coding: utf-8 -*-

import json
import re
import time
import xml.etree.ElementTree as ET
from datetime import datetime, timedelta, timezone
from email.utils import parsedate_to_datetime
from html import unescape
from urllib.parse import quote, urlencode
from urllib.request import Request, urlopen

from best_buy_app.data.market_data import UA


HEADERS = {
    "User-Agent": UA,
    "Accept": "text/html,application/xhtml+xml,application/xml;q=0.9,application/json;q=0.8,*/*;q=0.7",
}

RSS_SOURCES = {
    "bbc_top": ("BBC Top News", "https://feeds.bbci.co.uk/news/rss.xml", 24),
    "bbc_world": ("BBC World", "https://feeds.bbci.co.uk/news/world/rss.xml", 24),
    "bbc_chinese": ("BBC Chinese", "https://feeds.bbci.co.uk/zhongwen/simp/rss.xml", 24),
    "guardian_world": ("The Guardian World", "https://www.theguardian.com/world/rss", 24),
    "aljazeera": ("Al Jazeera", "https://www.aljazeera.com/xml/rss/all.xml", 24),
    "france24": ("France 24", "http://www.france24.com/en/rss", 24),
    "reuters": ("Reuters (Google News fallback)", "https://news.google.com/rss/search?q=site%3Areuters.com%20when%3A1d&hl=en-US&gl=US&ceid=US%3Aen", 24),
    "producthunt": ("Product Hunt", "https://www.producthunt.com/feed", None),
    "lobsters": ("Lobsters", "https://lobste.rs/rss", None),
    "devto": ("Dev.to", "https://dev.to/feed", None),
    "sspai": ("少数派", "https://sspai.com/feed", None),
    "infoq_cn": ("InfoQ 中文", "https://www.infoq.cn/feed.xml", None),
    "aihot": ("AIHOT", "https://aihot.virxact.com/rss", 24),
    "tldr_ai": ("TLDR AI", "https://tldr.tech/api/rss/ai", 48),
    "import_ai": ("Import AI", "https://importai.substack.com/feed", 168),
    "interconnects": ("Interconnects", "https://www.interconnects.ai/feed", None),
    "oneusefulthing": ("One Useful Thing", "https://www.oneusefulthing.org/feed", None),
    "chinai": ("ChinAI", "https://chinai.substack.com/feed", None),
    "memia": ("Memia", "https://memia.substack.com/feed", None),
    "kdnuggets": ("KDnuggets", "https://www.kdnuggets.com/feed", None),
}

SOURCE_GROUPS = {
    "international": ["bbc_top", "bbc_world", "guardian_world", "aljazeera", "france24", "reuters"],
    "finance": ["wallstreetcn", "reuters", "bbc_world"],
    "tech": ["hackernews", "github_trending", "producthunt", "lobsters", "devto"],
    "ai": ["aihot", "tldr_ai", "import_ai", "interconnects", "oneusefulthing", "kdnuggets"],
    "ai_newsletters": ["interconnects", "oneusefulthing", "chinai", "memia", "kdnuggets"],
    "chinese": ["wallstreetcn", "sspai", "infoq_cn"],
}


def fetch_text(url, timeout=15):
    req = Request(url, headers=HEADERS)
    try:
        with urlopen(req, timeout=timeout) as res:
            raw = res.read()
            charset = res.headers.get_content_charset() or "utf-8"
            return raw.decode(charset, errors="replace")
    except Exception:
        return ""


def fetch_json(url, timeout=15):
    text = fetch_text(url, timeout)
    if not text:
        return {}
    try:
        return json.loads(text)
    except json.JSONDecodeError:
        return {}


def clean_text(text):
    if text is None:
        return ""
    text = unescape(str(text))
    text = re.sub(r"<[^>]+>", " ", text)
    text = re.sub(r"\s+", " ", text).strip()
    text = re.sub(r"^\s*<!\[CDATA\[|\]\]>\s*$", "", text).strip()
    return text


def child_text(entry, names):
    for name in names:
        found = entry.find(name)
        if found is not None and found.text:
            return clean_text(found.text)
    for child in entry:
        tag = child.tag.rsplit("}", 1)[-1].lower()
        if tag in names and child.text:
            return clean_text(child.text)
    return ""


def child_link(entry):
    link = entry.find("link")
    if link is not None:
        if link.attrib.get("href"):
            return link.attrib["href"]
        if link.text:
            return clean_text(link.text)
    for child in entry:
        tag = child.tag.rsplit("}", 1)[-1].lower()
        if tag == "link":
            if child.attrib.get("href"):
                return child.attrib["href"]
            if child.text:
                return clean_text(child.text)
        if tag == "guid" and child.text and child.text.strip().startswith("http"):
            return clean_text(child.text)
    return ""


def parse_rss_content(content, source_name, limit=10):
    if not content:
        return []
    try:
        root = ET.fromstring(content.encode("utf-8") if isinstance(content, str) else content)
    except ET.ParseError:
        return []
    entries = []
    for node in root.iter():
        tag = node.tag.rsplit("}", 1)[-1].lower()
        if tag in ("item", "entry"):
            entries.append(node)
    items = []
    for entry in entries:
        title = child_text(entry, ["title"])
        if not title:
            continue
        summary = child_text(entry, ["description", "summary", "content", "encoded"])
        pub_time = child_text(entry, ["pubdate", "published", "updated", "date"])
        heat = child_text(entry, ["comments"])
        items.append({
            "source": source_name,
            "title": title,
            "url": child_link(entry),
            "time": pub_time or "Unknown Time",
            "heat": f"{heat} comments" if heat else "",
            "summary": summary[:300],
        })
        if len(items) >= limit:
            break
    return items


def parse_time(value):
    if not value:
        return None
    try:
        dt = parsedate_to_datetime(str(value))
        if dt.tzinfo is None:
            dt = dt.replace(tzinfo=timezone.utc)
        return dt
    except Exception:
        return None


def filter_by_hours(items, hours):
    if not hours:
        return items
    cutoff = datetime.now(timezone.utc) - timedelta(hours=hours)
    result = []
    for item in items:
        dt = parse_time(item.get("time"))
        if dt is None or dt >= cutoff:
            result.append(item)
    return result


def expand_keyword(keyword):
    if not keyword:
        return None
    parts = [p.strip() for p in str(keyword).split(",") if p.strip()]
    expanded = []
    for part in parts:
        if part.lower() in ("ai", "人工智能"):
            expanded.extend(["AI", "LLM", "GPT", "Claude", "Agent", "RAG", "DeepSeek"])
        else:
            expanded.append(part)
    return ",".join(dict.fromkeys(expanded))


def filter_items(items, keyword=None):
    keyword = expand_keyword(keyword)
    if not keyword:
        return items
    keys = [k.strip() for k in keyword.split(",") if k.strip()]
    if not keys:
        return items
    pattern = re.compile("|".join(re.escape(k) for k in keys), re.I)
    return [
        item for item in items
        if pattern.search(item.get("title", "")) or pattern.search(item.get("summary", ""))
    ]


def fetch_rss_source(source_key, limit=10, keyword=None):
    source_name, url, hours = RSS_SOURCES[source_key]
    items = parse_rss_content(fetch_text(url), source_name, max(limit * 3, 20))
    items = filter_by_hours(items, hours)
    return filter_items(items, keyword)[:limit]


def fetch_hackernews(limit=10, keyword=None):
    if keyword:
        timestamp_24h = int(time.time() - 24 * 3600)
        query = " OR ".join(expand_keyword(keyword).split(","))
        url = (
            "https://hn.algolia.com/api/v1/search_by_date?"
            f"tags=story&numericFilters=created_at_i>{timestamp_24h}&hitsPerPage={limit * 2}&query={quote(query)}"
        )
        data = fetch_json(url, timeout=10)
        hits = data.get("hits") or []
        items = []
        for hit in hits:
            object_id = hit.get("objectID")
            items.append({
                "source": "Hacker News",
                "title": hit.get("title") or hit.get("story_title") or "",
                "url": hit.get("url") or f"https://news.ycombinator.com/item?id={object_id}",
                "discussion_url": f"https://news.ycombinator.com/item?id={object_id}",
                "heat": f"{hit.get('points', 0)} points",
                "time": hit.get("created_at") or "Today",
                "summary": "",
            })
        return items[:limit]
    return fetch_rss_generic("https://hnrss.org/frontpage", "Hacker News", limit, keyword)


def fetch_github_trending(limit=10, keyword=None):
    url = "https://mshibanami.github.io/GitHubTrendingRSS/daily/all.xml"
    return fetch_rss_generic(url, "GitHub Trending", limit, keyword)


def fetch_wallstreetcn(limit=10, keyword=None):
    url = "https://api-one.wallstcn.com/apiv1/content/information-flow?channel=global-channel&accept=article&limit=30"
    data = fetch_json(url)
    items = []
    for item in (data.get("data", {}).get("items") or []):
        res = item.get("resource") or {}
        title = res.get("title") or res.get("content_short")
        if not title:
            continue
        ts = res.get("display_time")
        time_text = datetime.fromtimestamp(ts).strftime("%Y-%m-%d %H:%M") if ts else "Unknown Time"
        items.append({
            "source": "Wall Street CN",
            "title": clean_text(title),
            "url": res.get("uri") or "",
            "time": time_text,
            "heat": "",
            "summary": clean_text(res.get("content_short") or "")[:300],
        })
    return filter_items(items, keyword)[:limit]


def fetch_weibo(limit=10, keyword=None):
    data = fetch_json("https://weibo.com/ajax/side/hotSearch")
    items = []
    for item in (data.get("data", {}).get("realtime") or []):
        title = item.get("note") or item.get("word") or ""
        if not title:
            continue
        items.append({
            "source": "Weibo Hot Search",
            "title": title,
            "url": f"https://s.weibo.com/weibo?q={quote(title)}&Refer=top",
            "time": "Real-time",
            "heat": str(item.get("num") or ""),
            "summary": "",
        })
    return filter_items(items, keyword)[:limit]


def fetch_rss_generic(url, source_name, limit=10, keyword=None):
    return filter_items(parse_rss_content(fetch_text(url), source_name, max(limit * 2, limit)), keyword)[:limit]


def source_keys_for_request(sources):
    if not sources:
        return ["international"]
    if isinstance(sources, str):
        sources = [s.strip() for s in sources.split(",") if s.strip()]
    result = []
    for source in sources:
        key = str(source).strip().lower()
        if key == "all":
            result.extend(["hackernews", "github_trending", "wallstreetcn", "weibo", "international"])
        elif key in SOURCE_GROUPS:
            result.extend(SOURCE_GROUPS[key])
        else:
            result.append(key)
    return list(dict.fromkeys(result))


def fetch_news(sources=None, limit=10, keyword=None):
    keys = source_keys_for_request(sources)
    per_source = max(1, min(limit, limit // max(len(keys), 1) + 1))
    all_items = []
    errors = []
    for key in keys:
        try:
            if key in RSS_SOURCES:
                items = fetch_rss_source(key, per_source, keyword)
            elif key == "hackernews":
                items = fetch_hackernews(per_source, keyword)
            elif key == "github_trending":
                items = fetch_github_trending(per_source, keyword)
            elif key == "wallstreetcn":
                items = fetch_wallstreetcn(per_source, keyword)
            elif key == "weibo":
                items = fetch_weibo(per_source, keyword)
            else:
                errors.append(f"unsupported source: {key}")
                continue
            all_items.extend(items)
        except Exception as exc:
            errors.append(f"{key}: {exc}")
    return {
        "skill": "news-aggregator-skill",
        "sources": keys,
        "keyword": keyword,
        "limit": limit,
        "items": all_items[:limit],
        "errors": errors,
    }


def compact_news_for_ai(news_data):
    return {
        "skill": news_data.get("skill"),
        "sources": news_data.get("sources"),
        "keyword": news_data.get("keyword"),
        "errors": news_data.get("errors", []),
        "items": [
            {
                "source": item.get("source"),
                "title": item.get("title"),
                "url": item.get("url"),
                "time": item.get("time") or "Unknown Time",
                "heat": item.get("heat"),
                "summary": item.get("summary"),
            }
            for item in (news_data.get("items") or [])[:20]
        ],
    }
