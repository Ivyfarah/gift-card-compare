#!/usr/bin/env python3
"""Fetch public gift-card deal headlines and create a conservative, source-linked JSON feed.

No shopping API, sponsored placements, or invented coupons. RSS entries do not
guarantee that an offer is still redeemable. This parser deliberately excludes
cashback, points, expired posts and ambiguous claims.
"""
from __future__ import annotations

import calendar
import datetime as dt
import email.utils
import hashlib
import html
import json
import logging
import re
import sys
import urllib.request
import xml.etree.ElementTree as ET
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
OUTPUT = ROOT / "public" / "data" / "deals.json"
NOW = dt.datetime.now(dt.timezone.utc)
MAX_AGE_DAYS = 21
SOURCES = [
    ("Doctor of Credit", "https://www.doctorofcredit.com/category/gift-card-deals/feed/"),
    ("Slickdeals", "https://slickdeals.net/newsearch.php?mode=frontpage&searcharea=deals&searchin=first&rss=1"),
    ("Slickdeals", "https://slickdeals.net/newsearch.php?mode=popdeals&searcharea=deals&searchin=first&rss=1"),
]
BRANDS = [
    ("DoorDash", "food"), ("Uber Eats", "food"), ("Uber", "travel"),
    ("Instacart", "shopping"), ("Starbucks", "food"), ("Chipotle", "food"),
    ("Dunkin", "food"), ("Panera", "food"), ("Domino", "food"),
    ("Subway", "food"), ("Olive Garden", "food"), ("Chili", "food"),
    ("Target", "shopping"), ("Walmart", "shopping"), ("Costco", "shopping"),
    ("Best Buy", "shopping"), ("Home Depot", "shopping"), ("Lowe", "shopping"),
    ("Amazon", "shopping"), ("Apple", "shopping"), ("Nordstrom", "shopping"),
    ("Macy", "shopping"), ("Sephora", "shopping"), ("Ulta", "shopping"),
    ("Nike", "shopping"), ("Adidas", "shopping"), ("Bath & Body Works", "shopping"),
    ("Disney", "entertainment"), ("Roblox", "entertainment"), ("PlayStation", "entertainment"),
    ("Xbox", "entertainment"), ("Steam", "entertainment"), ("Nintendo", "entertainment"),
    ("Netflix", "entertainment"), ("Hulu", "entertainment"),
    ("Delta", "travel"), ("Southwest", "travel"), ("Amtrak", "travel"),
    ("Airbnb", "travel"), ("Hotels.com", "travel"), ("Marriott", "travel"),
    ("Lyft", "travel"), ("StubHub", "entertainment"),
]
GIFT_WORD = re.compile(r"\b(?:gift[\s-]*cards?|e[\s-]*gift[\s-]*cards?|giftcards?)\b", re.I)
REJECT = re.compile(
    r"\b(?:expired|unconfirmed|rumor|scam|cashback|cash back|"
    r"shopping portal|rewards? points?|fuel points?|bonus points?|"
    r"credit card offers?|statement credits?|rebate|"
    r"gift cards? sale banned)\b", re.I)
MONEY = r"\$([\d,]+(?:\.\d{1,2})?)"
PAIRS = [
    re.compile(MONEY + r".{0,85}?\bfor\s+" + MONEY, re.I),
    re.compile(r"\bbuy\s+" + MONEY + r".{0,90}?\bfor\s+" + MONEY, re.I),
]
PERCENT = re.compile(r"(?<!\w)(\d{1,2}(?:\.\d+)?)\s*%\s*(?:off|discount|savings?)", re.I)
PERCENT_RANGE = re.compile(r"(\d{1,2}(?:\.\d+)?)\s*[-–]\s*(\d{1,2}(?:\.\d+)?)\s*%\s*(?:off|discount)", re.I)
SAVINGS = re.compile(r"\bsav(?:e|ings?)\s+(?:up to\s+)?(\d{1,2}(?:\.\d+)?)\s*%", re.I)
UP_TO = re.compile(r"\bup to\s+(\d{1,2}(?:\.\d+)?)\s*%", re.I)
ENDS = re.compile(
    r"(?:valid\s+(?:through|until)|ends?|expires?|through|thru)\s*"
    r"(?:(?:on|at)\s*)?(\d{1,2})/(\d{1,2})(?:/(\d{2,4}))?", re.I
)
NON_EXACT = re.compile(r"\b(?:up to|select accounts?|ymmv|targeted|members? only|coupon|code:)\b", re.I)


def clean(value: str) -> str:
    value = re.sub(r"<[^>]+>", " ", value or "")
    value = html.unescape(value)
    return re.sub(r"\s+", " ", value).strip()


def category_brand(title: str):
    found = [(brand, category) for brand, category in BRANDS
             if re.search(r"(?<!\w)" + re.escape(brand) + r"(?!\w)", title, re.I)]
    # A retailer selling a third-party card is not necessarily the card brand.
    for name in ("Costco", "Best Buy", "Walmart", "Target", "Amazon", "Newegg"):
        if len(found) > 1:
            found = [x for x in found if x[0] != name]
    if not found:
        return "Other", "shopping"
    if len(found) >= 2:
        return " / ".join(b for b, _ in found[:2]), found[0][1]
    return found[0]


def parse_published(entry) -> dt.datetime | None:
    stamp = entry.findtext("pubDate") or entry.findtext("{http://www.w3.org/2005/Atom}updated")
    if not stamp:
        return None
    try:
        date = email.utils.parsedate_to_datetime(stamp)
        return date.astimezone(dt.timezone.utc) if date.tzinfo else date.replace(tzinfo=dt.timezone.utc)
    except (ValueError, TypeError):
        try:
            return dt.datetime.fromisoformat(stamp.replace("Z", "+00:00")).astimezone(dt.timezone.utc)
        except ValueError:
            return None


def expiration_in_past(text: str, published: dt.datetime) -> bool:
    # Only explicit expiration phrases; a historical update date is not an expiration.
    for month, day, year in ENDS.findall(text):
        if not year:
            year = published.year
            # If post appeared in December and end date is January, choose following year.
            if published.month == 12 and int(month) <= 2:
                year += 1
        else:
            year = int(year)
            if year < 100:
                year += 2000
        try:
            expiry = dt.datetime(year, int(month), int(day), 23, 59, tzinfo=dt.timezone.utc)
            if NOW > expiry + dt.timedelta(days=1):
                return True
        except ValueError:
            pass
    return False


def extract_offer(title: str, description: str):
    # Read amount/discount from headline only; summaries can refer to expired old promotions.
    for rx in PAIRS:
        match = rx.search(title)
        if not match:
            continue
        face, price = (float(v.replace(",", "")) for v in match.groups())
        if not (10 <= face <= 2000 and 0 < price < face):
            continue
        saving = (face - price) / face * 100
        if saving < 2 or saving > 60:
            continue
        return round(saving, 1), "price", round(face, 2), round(price, 2)

    range_match = PERCENT_RANGE.search(title)
    if range_match:
        low, high = map(float, range_match.groups())
        if 2 <= low <= high <= 60:
            return low, "range", None, None
    # "save 10%" and "10% discount" explicit, but exclude "up to 10%"
    match = PERCENT.search(title) or SAVINGS.search(title)
    if match and not UP_TO.search(title):
        p = float(match.group(1))
        if 2 <= p <= 60:
            return p, "percentage", None, None
    return None


def get_feed(name: str, url: str):
    req = urllib.request.Request(
        url,
        headers={"User-Agent": "GiftCardCompare/0.1 (personal non-commercial deal index)",
                 "Accept": "application/rss+xml, application/xml, text/xml, */*"},
    )
    with urllib.request.urlopen(req, timeout=22) as response:
        raw = response.read(2_000_000)
    feed = ET.fromstring(raw)
    items = feed.findall(".//item")
    # Some websites use Atom rather than RSS.
    if not items:
        items = feed.findall(".//{http://www.w3.org/2005/Atom}entry")
    return name, items


def main():
    offers = {}
    ok_sources = []
    errors = []
    for source, url in SOURCES:
        try:
            src, items = get_feed(source, url)
            ok_sources.append(src)
        except Exception as exc:
            logging.warning("%s feed unavailable: %s", source, exc)
            errors.append(f"{source}: unavailable")
            continue
        for item in items:
            title = clean(item.findtext("title") or item.findtext("{http://www.w3.org/2005/Atom}title") or "")
            description = clean(item.findtext("description") or item.findtext("{http://www.w3.org/2005/Atom}summary") or "")
            link = (item.findtext("link") or "").strip()
            if not link:
                alternate = item.find("{http://www.w3.org/2005/Atom}link")
                link = alternate.get("href", "") if alternate is not None else ""
            if not link.startswith("https://") and not link.startswith("http://"):
                continue
            date = parse_published(item)
            if not date or not (-1 <= (NOW - date).total_seconds() / 86400 <= MAX_AGE_DAYS):
                continue
            if not GIFT_WORD.search(title):
                continue
            if REJECT.search(title):
                continue
            if re.search(r"\b(?:buy|get|spend)\b.{0,90}\b(?:get|earn|receive)\b.{0,30}\b(?:bonus|free)\b", title, re.I):
                # A bonus card has different redemption conditions from a cash discount.
                continue
            if expiration_in_past(title + " " + description[:500], date):
                continue
            offer = extract_offer(title, description)
            if not offer:
                continue
            pct, kind, face, price = offer
            brand, category = category_brand(title)
            deal_id = hashlib.sha256(link.encode()).hexdigest()[:16]
            offer_obj = {
                "id": deal_id, "brand": brand, "category": category, "title": title[:220],
                "source": source, "url": link, "published": date.isoformat(),
                "discount_pct": pct, "deal_type": kind,
                "face_value": face, "price": price,
                "restricted": bool(NON_EXACT.search(title + " " + description[:180])),
            }
            if deal_id not in offers or offers[deal_id]["published"] < offer_obj["published"]:
                offers[deal_id] = offer_obj

    output = sorted(offers.values(), key=lambda x: (-x["discount_pct"], x["published"]))
    status = "ok" if len(ok_sources) == len(SOURCES) else "partial" if ok_sources else "unavailable"
    payload = {
        "generated_at": NOW.isoformat(), "status": status,
        "source_count": len(ok_sources), "configured_sources": len(SOURCES),
        "disclaimer": "Public article RSS headlines, not live price or availability verification.",
        "deals": output[:80],
    }
    OUTPUT.parent.mkdir(parents=True, exist_ok=True)
    OUTPUT.write_text(json.dumps(payload, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    logging.info("Sources succeeded: %s/%s; qualified offers: %s; status=%s",
                 len(ok_sources), len(SOURCES), len(output), status)
    # Exit success so site can report unavailable instead of keeping old results as fresh.
    return 0


if __name__ == "__main__":
    logging.basicConfig(level=logging.INFO, format="%(levelname)s: %(message)s")
    sys.exit(main())
