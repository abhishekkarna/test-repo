"""
Parliament and government source scrapers.

Three scrapers, all returning the same article dict shape:
  {title, text, url, published_at, source_type}

- PRSIndiaScraper  : Lok Sabha / Rajya Sabha debate summaries from prsindia.org
- SansadScraper    : Oral Q&A records from sansad.in
- PIBScraper       : Press releases from pib.gov.in
"""

import io
import logging
import re
import time
import urllib.parse
from abc import ABC, abstractmethod
from datetime import datetime

import xml.etree.ElementTree as ET
import httpx
from bs4 import BeautifulSoup

from app.config import settings
from app.extraction.scrapers.utils import clean_text, make_client, normalize_name_for_sansad, with_retry

logger = logging.getLogger(__name__)

# Attempt PDF extraction; gracefully degrade if pdfplumber not installed
_PDF_SUPPORT = False  # determined lazily on first use


# ── Base ──────────────────────────────────────────────────────────────────────

class ParliamentScraper(ABC):
    def __init__(self):
        self.client = make_client()

    @abstractmethod
    def fetch(
        self,
        leader_name: str,
        since_date: datetime | None = None,
        max_items: int = 30,
    ) -> list[dict]:
        """Return list of article dicts."""

    def _get(self, url: str, **kwargs) -> httpx.Response | None:
        return with_retry(
            lambda: self.client.get(url, **kwargs),
            label=f"GET {url[:80]}",
        )

    def _post(self, url: str, **kwargs) -> httpx.Response | None:
        return with_retry(
            lambda: self.client.post(url, **kwargs),
            label=f"POST {url[:80]}",
        )

    def _article(
        self,
        title: str,
        text: str,
        url: str,
        published_at: str | None,
        source_type: str,
    ) -> dict:
        return {
            "title": title,
            "text": clean_text(text),
            "url": url,
            "published_at": published_at,
            "source_type": source_type,
        }


# ── PRS India ─────────────────────────────────────────────────────────────────

class PRSIndiaScraper(ParliamentScraper):
    """
    Scrapes debate summaries from PRS India (prsindia.org).
    Covers both Lok Sabha and Rajya Sabha.
    Falls back to RSS-style search if HTML scraping returns nothing.
    """

    BASE = settings.prs_india_base_url
    MAX_PAGES = 5

    def fetch(
        self,
        leader_name: str,
        since_date: datetime | None = None,
        max_items: int = 30,
        house: str = "both",
    ) -> list[dict]:
        articles = []
        houses = ["loksabha", "rajyasabha"] if house == "both" else [house]

        for h in houses:
            articles.extend(self._fetch_house(leader_name, h, since_date, max_items))
            if len(articles) >= max_items:
                break

        return articles[:max_items]

    def _fetch_house(
        self,
        leader_name: str,
        house: str,
        since_date: datetime | None,
        max_items: int,
    ) -> list[dict]:
        articles = []
        encoded = urllib.parse.quote(leader_name)
        source_type = "lok_sabha" if "lok" in house else "rajya_sabha"

        for page in range(1, self.MAX_PAGES + 1):
            url = (
                f"{self.BASE}/parliamenttrack/debates"
                f"?search={encoded}&house={house}&page={page}"
            )
            resp = self._get(url)
            if not resp or resp.status_code != 200:
                break

            soup = BeautifulSoup(resp.text, "lxml")
            cards = soup.select(".debate-item, .debate-card, article.card")

            if not cards:
                # Try generic fallback selectors
                cards = soup.select("div.views-row, div.node")

            if not cards:
                break

            for card in cards:
                item = self._parse_card(card, source_type, since_date)
                if item:
                    articles.append(item)
                if len(articles) >= max_items:
                    return articles

            time.sleep(1.5)

        if not articles:
            articles = self._fallback_rss(leader_name, source_type, since_date, max_items)

        return articles

    def _parse_card(self, card, source_type: str, since_date: datetime | None) -> dict | None:
        title_el = card.select_one("h2, h3, .debate-title, .title a")
        date_el = card.select_one(".date, time, .field-date")
        link_el = card.select_one("a[href]")
        summary_el = card.select_one("p, .summary, .body")

        if not title_el:
            return None

        title = title_el.get_text(strip=True)
        date_str = date_el.get_text(strip=True) if date_el else None
        href = link_el["href"] if link_el else ""
        url = href if href.startswith("http") else self.BASE + href
        summary = summary_el.get_text(strip=True) if summary_el else ""

        # Apply date filter for incremental runs
        if since_date and date_str:
            try:
                pub = datetime.strptime(date_str, "%d %b %Y")
                if pub < since_date:
                    return None
            except ValueError:
                pass

        # Try to fetch full text from detail page or PDF
        full_text = self._fetch_detail(url) or summary

        return self._article(title, full_text or summary, url, date_str, source_type)

    def _fetch_detail(self, url: str) -> str | None:
        resp = self._get(url)
        if not resp or resp.status_code != 200:
            return None

        # If it's a PDF, extract text
        content_type = resp.headers.get("content-type", "")
        if "pdf" in content_type or url.endswith(".pdf"):
            return self._extract_pdf(resp.content)

        soup = BeautifulSoup(resp.text, "lxml")
        body = soup.select_one("article .body, .debate-body, .field-body, main article")
        return body.get_text(separator="\n", strip=True) if body else None

    def _extract_pdf(self, content: bytes, max_pages: int = 10) -> str | None:
        try:
            import pdfplumber
        except Exception:
            logger.warning("pdfplumber unavailable — skipping PDF")
            return None
        try:
            pages = []
            with pdfplumber.open(io.BytesIO(content)) as pdf:
                for page in pdf.pages[:max_pages]:
                    text = page.extract_text()
                    if text:
                        pages.append(text)
            return "\n\n".join(pages) if pages else None
        except Exception as e:
            logger.warning("PDF extraction failed: %s", e)
            return None

    def _fallback_rss(
        self,
        leader_name: str,
        source_type: str,
        since_date: datetime | None,
        max_items: int,
    ) -> list[dict]:
        query = f"site:prsindia.org {leader_name}".replace(" ", "+")
        rss_url = f"https://news.google.com/rss/search?q={query}&hl=en-IN&gl=IN&ceid=IN:en"
        articles = []
        try:
            resp = self._get(rss_url)
            if resp and resp.status_code == 200:
                root = ET.fromstring(resp.text)
                channel = root.find("channel")
                for item in (channel.findall("item") if channel is not None else [])[:max_items]:
                    articles.append(self._article(
                        item.findtext("title") or "",
                        item.findtext("description") or "",
                        item.findtext("link") or "",
                        item.findtext("pubDate"),
                        source_type,
                    ))
        except Exception as e:
            logger.error("PRS RSS fallback failed: %s", e)
        return articles


# ── Sansad.in ─────────────────────────────────────────────────────────────────

class SansadScraper(ParliamentScraper):
    """
    Fetches oral question records from sansad.in.
    Both Lok Sabha and Rajya Sabha question data.
    """

    BASE = settings.sansad_base_url
    MAX_PAGES = 3

    def fetch(
        self,
        leader_name: str,
        since_date: datetime | None = None,
        max_items: int = 30,
    ) -> list[dict]:
        sansad_name = normalize_name_for_sansad(leader_name)
        articles = []

        for house, source_type in [("ls", "lok_sabha"), ("rs", "rajya_sabha")]:
            articles.extend(
                self._fetch_house(sansad_name, leader_name, house, source_type, since_date, max_items)
            )
            if len(articles) >= max_items:
                break

        return articles[:max_items]

    def _fetch_house(
        self,
        sansad_name: str,
        display_name: str,
        house: str,
        source_type: str,
        since_date: datetime | None,
        max_items: int,
    ) -> list[dict]:
        articles = []
        encoded = urllib.parse.quote(sansad_name)

        for page in range(1, self.MAX_PAGES + 1):
            url = (
                f"{self.BASE}/{house}/questions/oral-questions"
                f"?member={encoded}&page={page}&per_page=25"
            )
            resp = self._get(url)
            if not resp or resp.status_code != 200:
                break

            soup = BeautifulSoup(resp.text, "lxml")
            rows = soup.select("table tbody tr, .question-row, .qa-item")

            if not rows:
                break

            for row in rows:
                item = self._parse_row(row, source_type, since_date)
                if item:
                    articles.append(item)
                if len(articles) >= max_items:
                    return articles

            time.sleep(1.0)

        return articles

    def _parse_row(self, row, source_type: str, since_date: datetime | None) -> dict | None:
        cells = row.select("td")
        if len(cells) < 3:
            return None

        # Typical Sansad table: Session | Date | Question | Ministry | Subject
        date_str = cells[1].get_text(strip=True) if len(cells) > 1 else None
        subject = cells[-1].get_text(strip=True) if cells else ""
        question_text = row.get_text(separator=" ", strip=True)

        link_el = row.select_one("a[href]")
        url = ""
        if link_el:
            href = link_el["href"]
            url = href if href.startswith("http") else self.BASE + href

        if since_date and date_str:
            for fmt in ("%d/%m/%Y", "%d-%m-%Y", "%d %b %Y"):
                try:
                    pub = datetime.strptime(date_str, fmt)
                    if pub < since_date:
                        return None
                    break
                except ValueError:
                    continue

        full_text = question_text
        if url:
            detail = self._fetch_qa_detail(url)
            if detail:
                full_text = detail

        return self._article(subject or "Parliamentary Q&A", full_text, url, date_str, source_type)

    def _fetch_qa_detail(self, url: str) -> str | None:
        resp = self._get(url)
        if not resp or resp.status_code != 200:
            return None
        soup = BeautifulSoup(resp.text, "lxml")
        content = soup.select_one(".question-answer, .qa-content, main .content")
        return content.get_text(separator="\n", strip=True) if content else None


# ── PIB ───────────────────────────────────────────────────────────────────────

class PIBScraper(ParliamentScraper):
    """
    Fetches press releases from pib.gov.in.
    Uses VIEWSTATE form POST for search; falls back to Google News RSS
    if VIEWSTATE extraction fails.
    """

    BASE = settings.pib_base_url
    SEARCH_URL = f"{settings.pib_base_url}/allRel.aspx"
    MAX_PAGES = 3

    def fetch(
        self,
        leader_name: str,
        since_date: datetime | None = None,
        max_items: int = 30,
    ) -> list[dict]:
        articles = self._fetch_via_viewstate(leader_name, since_date, max_items)
        if not articles:
            logger.info("PIB VIEWSTATE failed for %s — using RSS fallback", leader_name)
            articles = self._fallback_rss(leader_name, since_date, max_items)
        return articles[:max_items]

    def _fetch_via_viewstate(
        self,
        leader_name: str,
        since_date: datetime | None,
        max_items: int,
    ) -> list[dict]:
        # Initial GET to capture ASP.NET VIEWSTATE tokens
        resp = self._get(self.SEARCH_URL)
        if not resp or resp.status_code != 200:
            return []

        soup = BeautifulSoup(resp.text, "lxml")
        viewstate = self._extract_hidden(soup, "__VIEWSTATE")
        eventvalidation = self._extract_hidden(soup, "__EVENTVALIDATION")
        viewstate_gen = self._extract_hidden(soup, "__VIEWSTATEGENERATOR")

        if not viewstate:
            return []

        articles = []
        for page in range(1, self.MAX_PAGES + 1):
            data = {
                "__VIEWSTATE": viewstate,
                "__EVENTVALIDATION": eventvalidation,
                "__VIEWSTATEGENERATOR": viewstate_gen,
                "ctl00$ContentPlaceHolder1$txtSearch": leader_name,
                "ctl00$ContentPlaceHolder1$btnSearch": "Search",
            }
            if page > 1:
                data["__EVENTTARGET"] = f"ctl00$ContentPlaceHolder1$GridView1$ctl{page:02d}$lnkBtn"
                data.pop("ctl00$ContentPlaceHolder1$btnSearch", None)

            post_resp = self._post(self.SEARCH_URL, data=data)
            if not post_resp or post_resp.status_code != 200:
                break

            page_soup = BeautifulSoup(post_resp.text, "lxml")
            items = self._parse_pib_results(page_soup, since_date)
            articles.extend(items)

            # Update tokens from response for next page
            viewstate = self._extract_hidden(page_soup, "__VIEWSTATE") or viewstate
            eventvalidation = self._extract_hidden(page_soup, "__EVENTVALIDATION") or eventvalidation
            viewstate_gen = self._extract_hidden(page_soup, "__VIEWSTATEGENERATOR") or viewstate_gen

            if len(items) < 10 or len(articles) >= max_items:
                break

            time.sleep(1.5)

        return articles

    def _parse_pib_results(self, soup: BeautifulSoup, since_date: datetime | None) -> list[dict]:
        articles = []
        rows = soup.select("table#ctl00_ContentPlaceHolder1_GridView1 tr, .pressrelease-row")

        for row in rows[1:]:  # skip header row
            cells = row.select("td")
            if len(cells) < 2:
                continue

            link_el = row.select_one("a[href]")
            date_el = cells[-1] if cells else None
            title = link_el.get_text(strip=True) if link_el else cells[0].get_text(strip=True)
            date_str = date_el.get_text(strip=True) if date_el else None
            href = link_el["href"] if link_el else ""
            url = href if href.startswith("http") else self.BASE + "/" + href.lstrip("/")

            if since_date and date_str:
                for fmt in ("%d %B %Y", "%d/%m/%Y", "%B %d, %Y"):
                    try:
                        pub = datetime.strptime(date_str, fmt)
                        if pub < since_date:
                            continue
                        break
                    except ValueError:
                        continue

            full_text = self._fetch_press_release(url)
            if not full_text:
                full_text = title

            articles.append(self._article(title, full_text, url, date_str, "press_release"))

        return articles

    def _fetch_press_release(self, url: str) -> str | None:
        resp = self._get(url)
        if not resp or resp.status_code != 200:
            return None

        # Handle ISO-8859-1 encoded older PIB pages
        if "charset" not in resp.headers.get("content-type", "").lower():
            resp.encoding = "utf-8"

        soup = BeautifulSoup(resp.text, "lxml")
        content = soup.select_one(
            "#ctl00_ContentPlaceHolder1_lblPRLong, .pressrelease-body, .release-content"
        )
        return content.get_text(separator="\n", strip=True) if content else None

    def _extract_hidden(self, soup: BeautifulSoup, field: str) -> str | None:
        el = soup.find("input", {"name": field})
        return el["value"] if el and el.get("value") else None

    def _fallback_rss(
        self,
        leader_name: str,
        since_date: datetime | None,
        max_items: int,
    ) -> list[dict]:
        query = f"site:pib.gov.in {leader_name}".replace(" ", "+")
        rss_url = f"https://news.google.com/rss/search?q={query}&hl=en-IN&gl=IN&ceid=IN:en"
        articles = []
        try:
            resp = self._get(rss_url)
            if resp and resp.status_code == 200:
                root = ET.fromstring(resp.text)
                channel = root.find("channel")
                for item in (channel.findall("item") if channel is not None else [])[:max_items]:
                    articles.append(self._article(
                        item.findtext("title") or "",
                        item.findtext("description") or "",
                        item.findtext("link") or "",
                        item.findtext("pubDate"),
                        "press_release",
                    ))
        except Exception as e:
            logger.error("PIB RSS fallback failed: %s", e)
        return articles


# ── Factory ───────────────────────────────────────────────────────────────────

def get_parliament_scraper(source_type: str) -> ParliamentScraper:
    if source_type in ("lok_sabha", "rajya_sabha"):
        return PRSIndiaScraper()
    if source_type == "sansad":
        return SansadScraper()
    if source_type == "pib":
        return PIBScraper()
    raise ValueError(f"Unknown parliament source type: {source_type}")
