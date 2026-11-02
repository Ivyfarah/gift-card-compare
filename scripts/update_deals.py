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
import urllib.parse
from html.parser import HTMLParser
from concurrent.futures import ThreadPoolExecutor, as_completed
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
# Target brands are classified by the card's redemption use, not the storefront
# selling a third-party card. Longest matching brands win (Uber Eats vs Uber).
BRANDS = [
    # Restaurants and delivery
    ("Uber Eats", "food"), ("DoorDash", "food"), ("Grubhub", "food"),
    ("Starbucks", "food"), ("Chipotle", "food"), ("Dunkin", "food"),
    ("Panera", "food"), ("Domino's", "food"), ("Dominos", "food"),
    ("Subway", "food"), ("Olive Garden", "food"), ("Chili's", "food"),
    ("Chili", "food"), ("The Cheesecake Factory", "food"),
    ("Taco Bell", "food"), ("McDonald's", "food"), ("Panda Express", "food"),
    ("Shake Shack", "food"), ("Burger King", "food"), ("Pizza Hut", "food"),
    ("Wendy's", "food"), ("Chick-fil-A", "food"), ("Papa Johns", "food"),
    # Grocery and warehouse clubs
    ("Instacart", "grocery"), ("Whole Foods", "grocery"),
    ("Sam's Club", "grocery"), ("Costco", "grocery"), ("Kroger", "grocery"),
    ("Safeway", "grocery"), ("Albertsons", "grocery"), ("Publix", "grocery"),
    ("Aldi", "grocery"), ("Wegmans", "grocery"), ("Meijer", "grocery"),
    # General retail and marketplaces
    ("Target", "shopping"), ("Walmart", "shopping"), ("Amazon", "shopping"),
    ("eBay", "shopping"), ("Etsy", "shopping"), ("QVC", "shopping"),
    ("REI", "shopping"), ("Barnes & Noble", "shopping"),
    ("Dick\'s Sporting Goods", "shopping"), ("Staples", "shopping"),
    ("Office Depot", "shopping"),
    # Fashion / beauty
    ("Nordstrom", "fashion"), ("Macy's", "fashion"), ("Macy", "fashion"),
    ("Sephora", "fashion"), ("Ulta Beauty", "fashion"), ("Ulta", "fashion"),
    ("Nike", "fashion"), ("Adidas", "fashion"), ("Bath & Body Works", "fashion"),
    ("Old Navy", "fashion"), ("Lululemon", "fashion"), ("Athleta", "fashion"),
    ("Kohl's", "fashion"), ("Gap", "fashion"), ("JCPenney", "fashion"),
    ("TJ Maxx", "fashion"), ("Marshalls", "fashion"),
    # Electronics / games
    ("Best Buy", "tech"), ("Apple", "tech"), ("Google Play", "tech"),
    ("Newegg", "tech"), ("GameStop", "tech"), ("Nintendo", "tech"),
    ("PlayStation", "tech"), ("Xbox", "tech"), ("Roblox", "tech"),
    ("Steam", "tech"), ("Meta Quest", "tech"),
    # Travel / transport
    ("Uber", "travel"), ("Lyft", "travel"), ("Delta", "travel"),
    ("Southwest", "travel"), ("United Airlines", "travel"),
    ("American Airlines", "travel"), ("Airbnb", "travel"),
    ("Hotels.com", "travel"), ("Marriott", "travel"), ("Hilton", "travel"),
    ("Amtrak", "travel"), ("Expedia", "travel"),
    # Entertainment / streaming
    ("Disney+", "entertainment"), ("Disney", "entertainment"),
    ("Netflix", "entertainment"), ("Hulu", "entertainment"),
    ("Spotify", "entertainment"), ("AMC Theatres", "entertainment"),
    ("Regal Cinemas", "entertainment"), ("Cinemark", "entertainment"),
    ("StubHub", "entertainment"), ("Ticketmaster", "entertainment"),
    ("Fandango", "entertainment"), ("Paramount+", "entertainment"),
    # Home / improvements
    ("Home Depot", "home"), ("Lowe's", "home"), ("Lowe", "home"),
    ("IKEA", "home"), ("Wayfair", "home"), ("Pottery Barn", "home"),
    ("Williams Sonoma", "home"), ("Crate & Barrel", "home"),
    ("West Elm", "home"), ("HomeGoods", "home"),
    ("Ace Hardware", "home"),
    # Everyday services, fuel, pets
    ("CVS", "services"), ("Walgreens", "services"), ("Petco", "services"),
    ("PetSmart", "services"), ("Chewy", "services"), ("Shell", "services"),
    ("Chevron", "services"), ("Exxon", "services"), ("Mobil", "services"),
    ("AutoZone", "services"), ("Massage Envy", "services"),
]
# Merchants commonly mentioned as the _seller_ of a different brand's card.
RETAILER_SELLERS = {
    "Costco", "Best Buy", "Walmart", "Target", "Amazon",
    "Newegg", "Sam's Club", "Kroger",
}
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
    # Match known gift-card brands; generic/unknown cards are deliberately
    # placed in Other rather than wrongly labelled as retail/shopping.
    matches = [(brand, category) for brand, category in BRANDS
               if re.search(r"(?<!\w)" + re.escape(brand) + r"(?!\w)", title, re.I)]
    # Avoid including short aliases when their longer name matched too.
    matches = [(brand, category) for brand, category in matches
               if not any(brand.lower() != other.lower()
                          and brand.lower() in other.lower()
                          for other, _ in matches)]
    if len(matches) > 1:
        non_sellers = [(brand, category) for brand, category in matches
                       if brand not in RETAILER_SELLERS]
        if non_sellers:
            matches = non_sellers
    if not matches:
        return "Other", "other"
    if len(matches) == 1:
        return matches[0]
    # Multiple recipient brands can be offered together; do not pretend the
    # article concerns exactly one. The first category is a best-effort guess.
    return " / ".join(brand for brand, _ in matches[:2]), matches[0][1]

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


# CardCash displays secondary-market card listings and an "Up to X% off"
# rate for each brand. This is a listed maximum, NOT a guaranteed quote
# for every face value. Actual selection/availability is checked at checkout.
CARDCASH_BASE = "https://www.cardcash.com/buy-gift-cards/"
CARDCASH_CATEGORIES = [
    ("food", "discounted-restaurant-cards/"),
    ("food", "discounted-coffee-cards/"),
    ("food", "discounted-food-beverage-cards/"),
    ("grocery", "discounted-grocery-cards/"),
    ("shopping", "discounted-stores-cards/"),
    ("shopping", "discounted-sports-outdoors-cards/"),
    ("fashion", "discounted-apparel-cards/"),
    ("fashion", "discounted-health-beauty-cards/"),
    ("tech", "discounted-electronics-office-supplies-cards/"),
    ("tech", "discounted-gaming-cards/"),
    ("travel", "discounted-travel-cards/"),
    ("entertainment", "discounted-entertainment-cards/"),
    ("home", "discounted-home-garden-cards/"),
    ("services", "discounted-gas-auto-cards/"),
    ("services", "discounted-pets-cards/"),
]
CARD_RATE = re.compile(r"\bup\s*to\s*(\d{1,2}(?:\.\d+)?)\s*%\s*off\b", re.I)
LEFT_RE = re.compile(r"^\s*only\s+\d+\s+left\s+", re.I)
CARD_PATH_RE = re.compile(r"^/buy-gift-cards/discount-(?!ed-)[a-z0-9-]+-cards/?$", re.I)


class CardCashAnchorParser(HTMLParser):
    """Extract only brand links and displayed rates; no inline JS parsing."""
    def __init__(self):
        super().__init__(convert_charrefs=True)
        self.anchor = None
        self.cards = []

    def handle_starttag(self, tag, attrs):
        if tag.lower() != "a":
            return
        attrs = dict(attrs)
        href = html.unescape(attrs.get("href") or "")
        path = urllib.parse.urlparse(urllib.parse.urljoin(CARDCASH_BASE, href)).path
        if CARD_PATH_RE.match(path):
            self.anchor = {"href": urllib.parse.urljoin(CARDCASH_BASE, href), "text": []}
        else:
            self.anchor = None

    def handle_data(self, value):
        if self.anchor is not None:
            self.anchor["text"].append(value)

    def handle_endtag(self, tag):
        if tag.lower() == "a" and self.anchor is not None:
            self.cards.append((self.anchor["href"], clean(" ".join(self.anchor["text"]))))
            self.anchor = None


def fetch_cardcash_page(category: str, slug: str):
    url = urllib.parse.urljoin(CARDCASH_BASE, slug)
    request = urllib.request.Request(
        url, headers={
            "User-Agent": "Mozilla/5.0 (compatible; GiftCardCompare/1.0; personal discount overview)",
            "Accept": "text/html,application/xhtml+xml",
        }
    )
    with urllib.request.urlopen(request, timeout=18) as response:
        if urllib.parse.urlparse(response.url).hostname != "www.cardcash.com":
            raise ValueError("Unexpected CardCash redirect")
        page = response.read(2_000_000).decode("utf-8", "replace")
    parser = CardCashAnchorParser()
    parser.feed(page)
    found = []
    for href, label in parser.cards:
        label = LEFT_RE.sub("", label).strip()
        match = CARD_RATE.search(label)
        if not match:
            continue
        percent = float(match.group(1))
        if not (0 < percent <= 70):
            continue
        brand = re.sub(r"\s+", " ", label[:match.start()]).strip(" -®™")
        if not (2 <= len(brand) <= 75) or brand.lower() in ("sale", "deals"):
            continue
        known_brand, known_category = category_brand(brand)
        chosen_category = known_category if known_category != "other" else category
        # Preserve special restrictions such as "In-Store Only" in the brand.
        card_id = hashlib.sha256(("cardcash:" + href).encode()).hexdigest()[:16]
        found.append({
            "id": card_id,
            "brand": brand,
            "category": chosen_category,
            "title": f"{brand} — CardCash 標示最高 {percent:g}% 折扣（轉售禮品卡）",
            "source": "CardCash",
            "source_kind": "resale_listing",
            "url": href,
            "published": NOW.isoformat(),
            "observed_at": NOW.isoformat(),
            "discount_pct": round(percent, 1),
            "deal_type": "up_to",
            "face_value": None,
            "price": None,
            "restricted": bool(re.search(r"only|app|trade credit|in.store", brand, re.I)),
        })
    if not found:
        raise ValueError("No brand/discount links found: page blocked or changed layout")
    return category, slug, found


def fetch_cardcash_all():
    found = {}
    pages_ok = 0
    # Never generate fabricated discount rates when a source is blocked.
    with ThreadPoolExecutor(max_workers=4) as pool:
        futures = {
            pool.submit(fetch_cardcash_page, cat, slug): slug
            for cat, slug in CARDCASH_CATEGORIES
        }
        for future in as_completed(futures):
            slug = futures[future]
            try:
                _, _, listings = future.result()
                pages_ok += 1
                for item in listings:
                    old = found.get(item["id"])
                    if not old or item["discount_pct"] > old["discount_pct"]:
                        found[item["id"]] = item
            except Exception as exc:
                logging.warning("CardCash page %s unavailable: %s", slug, exc)
    logging.info("CardCash pages: %s/%s, unique live listings: %s",
                 pages_ok, len(CARDCASH_CATEGORIES), len(found))
    return found, pages_ok


def main():
    offers, cardcash_pages_ok = fetch_cardcash_all()
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
                "source": source, "source_kind": "deal_article", "url": link, "published": date.isoformat(),
                "discount_pct": pct, "deal_type": kind,
                "face_value": face, "price": price,
                "restricted": bool(NON_EXACT.search(title + " " + description[:180])),
            }
            if deal_id not in offers or offers[deal_id]["published"] < offer_obj["published"]:
                offers[deal_id] = offer_obj

    output = sorted(
        offers.values(), key=lambda x: (-x["discount_pct"], x.get("brand", "").lower())
    )
    usable_sources = len(ok_sources) + cardcash_pages_ok
    total_sources = len(SOURCES) + len(CARDCASH_CATEGORIES)
    status = ("ok" if usable_sources == total_sources else
              "partial" if usable_sources else "unavailable")
    counts = {cat: 0 for cat in (
        "food", "grocery", "shopping", "fashion", "tech",
        "travel", "entertainment", "home", "services", "other"
    )}
    for item in output:
        counts[item.get("category", "other")] = counts.get(item.get("category", "other"), 0) + 1
    payload = {
        "generated_at": NOW.isoformat(),
        "status": status,
        "source_count": usable_sources,
        "configured_sources": total_sources,
        "cardcash_pages_ok": cardcash_pages_ok,
        "category_counts": counts,
        "disclaimer": "CardCash maxima are real public resale listings, NOT firm checkout prices. Recent articles are unverified. Always confirm card amount, stock, terms and final cost.",
        "deals": output[:400],
    }
    OUTPUT.parent.mkdir(parents=True, exist_ok=True)
    OUTPUT.write_text(json.dumps(payload, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    logging.info("All sources succeeded: %s/%s; listings+headlines: %s; status=%s; categories=%s",
                 usable_sources, total_sources, len(output), status, counts)
    # Exit success so site can report unavailable instead of keeping old results as fresh.
    return 0


if __name__ == "__main__":
    logging.basicConfig(level=logging.INFO, format="%(levelname)s: %(message)s")
    sys.exit(main())
