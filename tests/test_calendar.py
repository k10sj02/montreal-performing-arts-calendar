import json
from datetime import date, datetime
from zoneinfo import ZoneInfo

from icalendar import Calendar

from montreal_performing_arts_calendar import _schedule_lines, build_calendar, la_chapelle_events


class FakeFetcher:
    def __init__(self, pages): self.pages = pages
    def get(self, url): return next(value for key, value in self.pages.items() if key in url)


def test_la_chapelle_structured_occurrences():
    listing = '<a href="/fr/spectacles/a-show">Show</a>'
    event = {"@type":"TheaterEvent","name":"Artist - A Show","description":"Theatre","subEvent":[{"startDate":"2026-10-17T00:30:00Z","endDate":"2026-10-17T01:30:00Z","offers":{"url":"https://tickets.test/show"},"eventStatus":"https://schema.org/EventScheduled"}]}
    detail = f'<script type="application/ld+json">{json.dumps(event)}</script>'
    got = la_chapelle_events(FakeFetcher({"saison-2026-2027":listing,"a-show":detail}), date(2026,10,10), date(2026,11,1))
    assert got[0].title == "A Show"
    assert got[0].start == "2026-10-16T20:30:00-04:00"
    assert got[0].url == "https://tickets.test/show"


def test_schedule_lines_supports_grouped_dates():
    page = '<p>5, 6, 7 novembre 2026 - 19h</p><p>8 novembre 2026 - 16h</p>'
    values = _schedule_lines(page, 2026)
    assert [value.day for value in values] == [5, 6, 7, 8]
    assert values[-1].hour == 16


def test_valid_timezone_calendar():
    listing = '<a href="/fr/spectacles/a-show">Show</a>'
    event = {"@type":"TheaterEvent","name":"A Show","subEvent":[{"startDate":"2026-10-17T00:30:00Z","endDate":"2026-10-17T01:30:00Z"}]}
    detail = f'<script type="application/ld+json">{json.dumps(event)}</script>'
    item = la_chapelle_events(FakeFetcher({"saison-2026-2027":listing,"a-show":detail}), date(2026,10,10), date(2026,11,1))[0]
    parsed = Calendar.from_ical(build_calendar([item], datetime(2026,10,10,12,tzinfo=ZoneInfo("America/Toronto"))))
    assert parsed.walk("VEVENT")[0].decoded("DTSTART").tzinfo.key == "America/Toronto"
