"""
Fetches news articles about a leader from NewsAPI, Google News RSS, and GDELT.
Returns a list of article dicts: {title, text, url, published_at, source_type}
"""

import logging
import xml.etree.ElementTree as ET
from datetime import datetime, timedelta, timezone

import time

import httpx

from app.config import settings

logger = logging.getLogger(__name__)

GOOGLE_NEWS_RSS = "https://news.google.com/rss/search?q={query}&hl=en-IN&gl=IN&ceid=IN:en"
GDELT_API = "https://api.gdeltproject.org/api/v2/doc/doc"


def fetch_newsapi(leader_name: str, max_articles: int = 20) -> list[dict]:
    if not settings.news_api_key:
        logger.warning("NEWS_API_KEY not set; skipping NewsAPI")
        return []

    from_date = (datetime.utcnow() - timedelta(days=30)).strftime("%Y-%m-%d")
    url = "https://newsapi.org/v2/everything"
    params = {
        "q": f'"{leader_name}" promise OR pledges OR announces OR commits',
        "language": "en",
        "from": from_date,
        "sortBy": "relevancy",
        "pageSize": max_articles,
        "apiKey": settings.news_api_key,
    }

    try:
        r = httpx.get(url, params=params, timeout=15)
        r.raise_for_status()
        articles = r.json().get("articles", [])
        return [
            {
                "title": a["title"],
                "text": (a.get("description") or "") + "\n\n" + (a.get("content") or ""),
                "url": a["url"],
                "published_at": a.get("publishedAt"),
                "source_type": "news_article",
            }
            for a in articles
            if a.get("title") and a.get("url")
        ]
    except httpx.HTTPError as e:
        logger.error("NewsAPI error: %s", e)
        return []


def fetch_google_news_rss(leader_name: str, max_articles: int = 20) -> list[dict]:
    query = f"{leader_name} promise OR pledge OR commit OR announce"
    url = GOOGLE_NEWS_RSS.format(query=query.replace(" ", "+"))

    try:
        resp = httpx.get(url, timeout=15, headers={"User-Agent": "Mozilla/5.0"}, follow_redirects=True)
        resp.raise_for_status()
        root = ET.fromstring(resp.text)
        channel = root.find("channel")
        if channel is None:
            return []
        articles = []
        for item in channel.findall("item")[:max_articles]:
            title = item.findtext("title") or ""
            link = item.findtext("link") or ""
            pub_date = item.findtext("pubDate")
            description = item.findtext("description") or ""
            articles.append({
                "title": title,
                "text": description,
                "url": link,
                "published_at": pub_date,
                "source_type": "news_article",
            })
        return articles
    except Exception as e:
        logger.error("Google News RSS error: %s", e)
        return []


def fetch_gdelt_articles(
    leader_name: str,
    max_articles: int = 20,
    since_date: datetime | None = None,
    until_date: datetime | None = None,
) -> list[dict]:
    """
    Query GDELT for historical news. Free, no API key, covers back to 2013.
    Fetches article metadata from GDELT then scrapes full text from each URL.
    """
    now = datetime.now(timezone.utc).replace(tzinfo=None)
    start = (since_date or (now - timedelta(days=30))).strftime("%Y%m%d%H%M%S")
    end = (until_date or now).strftime("%Y%m%d%H%M%S")

    query = (
        f'"{leader_name}" '
        f'(promise OR pledges OR announces OR commits OR guarantee '
        f'OR committed OR aims OR target OR intend)'
    )
    params = {
        "query": query,
        "mode": "artlist",
        "maxrecords": min(max_articles * 2, 250),
        "startdatetime": start,
        "enddatetime": end,
        "sourcelang": "english",
        "sourcecountry": "India",
        "format": "json",
    }

    data = None
    for attempt in range(3):
        try:
            r = httpx.get(GDELT_API, params=params, timeout=20)
            if r.status_code == 429:
                wait = 15 * (attempt + 1)
                logger.warning("GDELT rate limited (429), retrying in %ds (attempt %d/3)", wait, attempt + 1)
                time.sleep(wait)
                continue
            r.raise_for_status()
            data = r.json()
            break
        except httpx.HTTPError as e:
            logger.error("GDELT API error (attempt %d/3): %s", attempt + 1, e)
            if attempt < 2:
                time.sleep(10)
    if data is None:
        return []

    raw_articles = data.get("articles") or []
    if not raw_articles:
        logger.info("GDELT returned 0 articles for %s (%s–%s)", leader_name, start[:8], end[:8])
        return []

    results = []
    for item in raw_articles[:max_articles]:
        url = item.get("url", "")
        title = item.get("title", "")
        seen = item.get("seendate", "")
        pub_date = _gdelt_date(seen)
        text = _fetch_article_text(url) or item.get("socialimage", "")
        results.append({
            "title": title,
            "text": text or title,
            "url": url,
            "published_at": pub_date,
            "source_type": "news_article",
        })

    logger.info("GDELT: fetched %d articles for %s", len(results), leader_name)
    return results


def _gdelt_date(seen: str) -> str | None:
    """Convert GDELT seendate '20180315T120000Z' → '2018-03-15'."""
    try:
        return datetime.strptime(seen[:8], "%Y%m%d").strftime("%Y-%m-%d")
    except Exception:
        return None


def _fetch_article_text(url: str) -> str | None:
    """Best-effort full text extraction from an article URL."""
    if not url:
        return None
    try:
        r = httpx.get(url, timeout=10, headers={"User-Agent": "Mozilla/5.0"}, follow_redirects=True)
        if r.status_code != 200:
            return None
        from bs4 import BeautifulSoup
        soup = BeautifulSoup(r.text, "lxml")
        # Remove nav/header/footer noise
        for tag in soup(["script", "style", "nav", "header", "footer", "aside"]):
            tag.decompose()
        article = soup.find("article") or soup.find("main") or soup.find("body")
        return article.get_text(separator="\n", strip=True)[:8000] if article else None
    except Exception:
        return None


def fetch_news_articles(
    leader_name: str,
    max_articles: int = 20,
    since_date: datetime | None = None,
    until_date: datetime | None = None,
) -> list[dict]:
    # Use GDELT when a historical date range is requested (older than 30 days)
    cutoff = datetime.now(timezone.utc).replace(tzinfo=None) - timedelta(days=30)
    if since_date and since_date < cutoff:
        articles = fetch_gdelt_articles(leader_name, max_articles, since_date, until_date)
        if articles:
            return articles

    articles = fetch_newsapi(leader_name, max_articles)
    if not articles:
        articles = fetch_google_news_rss(leader_name, max_articles)
    return articles
