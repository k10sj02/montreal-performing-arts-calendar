from __future__ import annotations

import hashlib
import json
import re
import unicodedata
from dataclasses import asdict, dataclass
from datetime import date, datetime, timedelta
from pathlib import Path
from urllib.parse import urljoin
from zoneinfo import ZoneInfo

import httpx
from bs4 import BeautifulSoup
from icalendar import Calendar, Event

TIMEZONE = ZoneInfo("America/Toronto")
CHAPELLE = "https://www.lachapelle.org/fr/programmation/saison-2026-2027"
TANGENTE = "https://tangentedanse.ca/evenement/"
AGORA_API = "https://agoradanse.com/wp-json/wp/v2/evenement"
MAI_API = "https://m-a-i.qc.ca/wp-json/wp/v2/event"
USER_AGENT = "MontrealPerformingArtsCalendar/1.0 (+https://github.com/k10sj02/montreal-performing-arts-calendar)"
MONTHS = {
    "jan": 1, "fév": 2, "fev": 2, "mar": 3, "avr": 4, "mai": 5, "juin": 6,
    "juil": 7, "aoû": 8, "aou": 8, "sep": 9, "oct": 10, "nov": 11, "déc": 12, "dec": 12,
}


@dataclass(frozen=True)
class Performance:
    source: str
    source_id: str
    title: str
    start: str
    end: str | None
    venue: str
    address: str
    url: str
    description: str = ""
    status: str = "CONFIRMED"

    @property
    def uid(self) -> str:
        return hashlib.sha256(f"{self.source}|{self.source_id}".encode()).hexdigest()[:28] + "@montreal-performing-arts"


class Fetcher:
    def __init__(self):
        self.client = httpx.Client(timeout=20, follow_redirects=True, headers={"User-Agent": USER_AGENT, "Accept-Language": "fr-CA,fr;q=.9,en;q=.8"})

    def get(self, url: str) -> str:
        response = self.client.get(url)
        response.raise_for_status()
        return response.text


def _json_scripts(html: str) -> list[dict]:
    soup = BeautifulSoup(html, "html.parser")
    output = []
    for script in soup.select('script[type="application/ld+json"]'):
        try:
            value = json.loads(script.string or script.get_text())
        except (TypeError, json.JSONDecodeError):
            continue
        if isinstance(value, dict):
            output.append(value)
    return output


def _future(value: datetime, start: date, end: date) -> bool:
    return start <= value.astimezone(TIMEZONE).date() <= end


def la_chapelle_events(fetcher: Fetcher, start: date, end: date) -> list[Performance]:
    soup = BeautifulSoup(fetcher.get(CHAPELLE), "html.parser")
    links = {urljoin(CHAPELLE, a["href"]) for a in soup.find_all("a", href=True) if "/fr/spectacles/" in a["href"]}
    output = []
    for url in links:
        for item in _json_scripts(fetcher.get(url)):
            if item.get("@type") != "TheaterEvent" or not item.get("subEvent"):
                continue
            title = item.get("name", "Performance")
            if " - " in title:
                title = title.split(" - ", 1)[1]
            for occurrence in item["subEvent"]:
                begins = datetime.fromisoformat(occurrence["startDate"].replace("Z", "+00:00")).astimezone(TIMEZONE)
                if not _future(begins, start, end):
                    continue
                finish_value = occurrence.get("endDate")
                finish = datetime.fromisoformat(finish_value.replace("Z", "+00:00")).astimezone(TIMEZONE) if finish_value else None
                offer = occurrence.get("offers") or item.get("offers") or {}
                output.append(Performance(
                    source="la-chapelle", source_id=f"{url.rstrip('/').rsplit('/', 1)[-1]}-{begins.isoformat()}",
                    title=title, start=begins.isoformat(), end=finish.isoformat() if finish else None,
                    venue="La Chapelle Scènes Contemporaines", address="3700 rue Saint-Dominique, Montréal, QC H2X 2X8",
                    url=offer.get("url") or url, description=item.get("description", ""),
                    status="CANCELLED" if "Cancelled" in str(occurrence.get("eventStatus")) else "CONFIRMED",
                ))
    if not output:
        raise RuntimeError("No current La Chapelle performances found")
    return output


def _month(value: str) -> int | None:
    normalized = unicodedata.normalize("NFKD", value.casefold()).encode("ascii", "ignore").decode()
    return MONTHS.get(normalized[:3])


def _schedule_lines(html: str, year_hint: int) -> list[datetime]:
    text = BeautifulSoup(html, "html.parser").get_text(" ", strip=True)
    pattern = re.compile(r"((?:\d{1,2}\s*(?:,|\-|–|—|et)\s*)*\d{1,2})\s+(janvier|février|fevrier|mars|avril|mai|juin|juillet|août|aout|septembre|octobre|novembre|décembre|decembre)\s+(20\d{2})\s*(?:-|–|—)\s*(\d{1,2})\s*h\s*(\d{2})?", re.I)
    output = []
    for days, month_name, year, hour, minute in pattern.findall(text):
        month = _month(month_name)
        if not month:
            continue
        for day in re.findall(r"\d{1,2}", days):
            output.append(datetime(int(year or year_hint), month, int(day), int(hour), int(minute or 0), tzinfo=TIMEZONE))
    return output


def tangente_events(fetcher: Fetcher, start: date, end: date) -> list[Performance]:
    soup = BeautifulSoup(fetcher.get(TANGENTE), "html.parser")
    links = {urljoin(TANGENTE, a["href"]) for a in soup.find_all("a", href=True) if "/evenement/" in a["href"] and a["href"].rstrip("/") != TANGENTE.rstrip("/")}
    output = []
    for url in links:
        html = fetcher.get(url)
        detail = BeautifulSoup(html, "html.parser")
        heading = detail.find("h1")
        title = heading.get_text(" ", strip=True) if heading else url.rstrip("/").rsplit("/", 1)[-1].replace("-", " ").title()
        for begins in _schedule_lines(html, start.year):
            if _future(begins, start, end):
                output.append(Performance(
                    source="tangente", source_id=f"{url.rstrip('/').rsplit('/', 1)[-1]}-{begins.isoformat()}",
                    title=title, start=begins.isoformat(), end=None, venue="Tangente — Édifice Wilder",
                    address="1435 rue De Bleury, Montréal, QC H3A 2H7", url=url, description="Contemporary dance performance.",
                ))
    if not output:
        raise RuntimeError("No current Tangente performances found")
    return output


def agora_events(fetcher: Fetcher, start: date, end: date) -> list[Performance]:
    posts = json.loads(fetcher.get(f"{AGORA_API}?per_page=100&_fields=id,link,title"))
    output = []
    for post in posts:
        html = fetcher.get(post["link"])
        title = BeautifulSoup(post["title"]["rendered"], "html.parser").get_text(" ", strip=True)
        for begins in _schedule_lines(html, start.year):
            if _future(begins, start, end):
                output.append(Performance(
                    source="agora", source_id=f"{post['id']}-{begins.isoformat()}", title=title,
                    start=begins.isoformat(), end=None, venue="Agora de la danse",
                    address="1435 rue De Bleury, Montréal, QC H3A 2H7", url=post["link"], description="Contemporary dance performance.",
                ))
    if not output:
        raise RuntimeError("No current Agora performances found")
    return output


def mai_events(fetcher: Fetcher, start: date, end: date) -> list[Performance]:
    posts = json.loads(fetcher.get(f"{MAI_API}?per_page=30&_fields=id,link,title"))
    output = []
    for post in posts:
        html = fetcher.get(post["link"])
        soup = BeautifulSoup(html, "html.parser")
        node = soup.select_one(".event_date")
        if not node:
            continue
        text = node.get_text(" ", strip=True)
        match = re.search(r"(\d{1,2})\s*(?:—|-|–)\s*(\d{1,2})\s+([A-Za-zÀ-ÿ]+).*?(\d{1,2}):(\d{2})", text)
        if not match:
            continue
        first, last, month_name, hour, minute = match.groups()
        month = _month(month_name)
        if not month:
            continue
        year = start.year + (month < start.month - 2)
        title = BeautifulSoup(post["title"]["rendered"], "html.parser").get_text(" ", strip=True)
        if re.search(r"suspendu|annul[eé]", title, re.I):
            continue
        for day in range(int(first), int(last) + 1):
            begins = datetime(year, month, day, int(hour), int(minute), tzinfo=TIMEZONE)
            if _future(begins, start, end):
                output.append(Performance(
                    source="mai", source_id=f"{post['id']}-{begins.isoformat()}", title=title,
                    start=begins.isoformat(), end=None, venue="MAI (Montréal, arts interculturels)",
                    address="3680 rue Jeanne-Mance, Montréal, QC H2X 2K5", url=post["link"], description="Intercultural and interdisciplinary performance.",
                ))
    if not output:
        raise RuntimeError("No current MAI performances found")
    return output


def deduplicate(events: list[Performance]) -> list[Performance]:
    unique = {}
    for event in events:
        normalized = unicodedata.normalize("NFKD", event.title.casefold())
        key = ("".join(c for c in normalized if c.isalnum()), event.start[:16])
        unique.setdefault(key, event)
    return list(unique.values())


def merge_state(current: list[Performance], path: Path, now: datetime, successful: set[str]) -> list[Performance]:
    old = {}
    if path.exists():
        try:
            old = json.loads(path.read_text()).get("events", {})
        except (OSError, json.JSONDecodeError):
            pass
    merged = {event.uid: {**asdict(event), "missing_runs": 0} for event in current}
    for uid, item in old.items():
        if uid in merged:
            continue
        try:
            begins = datetime.fromisoformat(item["start"])
        except (KeyError, ValueError):
            continue
        missing = int(item.get("missing_runs", 0)) + (item.get("source") in successful)
        if begins >= now - timedelta(days=1):
            merged[uid] = {**item, "missing_runs": missing, "status": "CANCELLED" if missing >= 2 else item.get("status", "CONFIRMED")}
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps({"updated": now.isoformat(), "events": merged}, ensure_ascii=False, indent=2) + "\n")
    return [Performance(**{k: v for k, v in item.items() if k != "missing_runs"}) for item in merged.values()]


def build_calendar(events: list[Performance], generated: datetime) -> bytes:
    calendar = Calendar()
    calendar.add("prodid", "-//Montréal Independent Performing Arts Calendar//EN")
    calendar.add("version", "2.0")
    calendar.add("calscale", "GREGORIAN")
    calendar.add("method", "PUBLISH")
    calendar.add("x-wr-calname", "Montréal Independent Performing Arts")
    calendar.add("x-wr-timezone", "America/Toronto")
    calendar.add("refresh-interval", timedelta(hours=6), parameters={"VALUE": "DURATION"})
    for item in sorted(events, key=lambda e: (e.start, e.title)):
        event = Event()
        event.add("uid", item.uid)
        event.add("dtstamp", generated)
        event.add("dtstart", datetime.fromisoformat(item.start).astimezone(TIMEZONE))
        if item.end:
            event.add("dtend", datetime.fromisoformat(item.end).astimezone(TIMEZONE))
        event.add("summary", item.title)
        event.add("location", f"{item.venue}, {item.address}")
        event.add("description", "\n".join(filter(None, [item.description, f"Original listing: {item.url}"])))
        event.add("url", item.url)
        event.add("status", item.status)
        event.add("categories", "Performing Arts,Theatre,Dance,Performance")
        event.add("x-montreal-performing-arts-source", item.source)
        calendar.add_component(event)
    return calendar.to_ical()


def main() -> None:
    root = Path.cwd()
    now = datetime.now(TIMEZONE)
    final_day = now.date() + timedelta(days=120)
    fetcher = Fetcher()
    collected, successful, errors, counts = [], set(), {}, {}
    for name, loader in (("la-chapelle", la_chapelle_events), ("tangente", tangente_events), ("agora", agora_events), ("mai", mai_events)):
        try:
            found = loader(fetcher, now.date(), final_day)
            collected.extend(found)
            successful.add(name)
            counts[name] = len(found)
        except Exception as exc:
            errors[name] = str(exc)
    if not collected:
        raise RuntimeError("No current performances found; refusing to publish an empty calendar")
    events = merge_state(deduplicate(collected), root / "data/state.json", now, successful)
    public = root / "public"
    public.mkdir(exist_ok=True)
    (public / "montreal-performing-arts.ics").write_bytes(build_calendar(events, now))
    status = {"generated": now.isoformat(), "count": sum(e.status != "CANCELLED" for e in events), "cancelled": sum(e.status == "CANCELLED" for e in events), "sources": counts, "errors": errors}
    (public / "status.json").write_text(json.dumps(status, ensure_ascii=False, indent=2) + "\n")
    print(json.dumps(status, ensure_ascii=False))
