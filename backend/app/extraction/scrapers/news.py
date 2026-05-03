"""
Fetches news articles about a leader from NewsAPI and Google News RSS.
Returns a list of article dicts: {title, text, url, published_at, source_type}
"""

import logging
from datetime import datetime, timedelta

import feedparser
import httpx

from app.config import settings

logger = logging.getLogger(__name__)

GOOGLE_NEWS_RSS = "https://news.google.com/rss/search?q={query}&hl=en-IN&gl=IN&ceid=IN:en"


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
        feed = feedparser.parse(url)
        articles = []
        for entry in feed.entries[:max_articles]:
            articles.append(
                {
                    "title": entry.get("title", ""),
                    "text": entry.get("summary", ""),
                    "url": entry.get("link", ""),
                    "published_at": entry.get("published"),
                    "source_type": "news_article",
                }
            )
        return articles
    except Exception as e:
        logger.error("Google News RSS error: %s", e)
        return []


def fetch_news_articles(leader_name: str, max_articles: int = 20) -> list[dict]:
    articles = fetch_newsapi(leader_name, max_articles)
    if not articles:
        articles = fetch_google_news_rss(leader_name, max_articles)
    return articles
