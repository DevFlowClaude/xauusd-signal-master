"""
News Filter Module (XAUUSD)

Fetches USD high-impact news from ForexFactory and provides lookup for
FundingPips 5-hour rule compliance.

Adapted from the original hirek.py (NAS100 project). XAUUSD is primarily
driven by USD, so we filter USD events with high impact:
  - NFP (Non-Farm Payrolls)
  - CPI (Consumer Price Index)
  - Fed meetings / FOMC
  - Core PCE, Retail Sales, PPI
  - Powell speeches

ForexFactory JSON endpoint (public, no API key needed):
  https://nfs.faireconomy.media/ff_calendar_thisweek.json

Cached locally for 60 minutes to avoid hammering the endpoint.
"""

from __future__ import annotations
import json
import time
from dataclasses import dataclass
from datetime import datetime, timezone, timedelta
from pathlib import Path
from typing import List, Dict, Optional
import urllib.request
import urllib.error

from shared.logger import get_logger

log = get_logger("news_filter")

FF_URL = "https://nfs.faireconomy.media/ff_calendar_thisweek.json"
CACHE_PATH = Path("data/news_cache.json")
CACHE_MAX_AGE_SECONDS = 3600  # 1 hour


@dataclass
class NewsEvent:
    time: datetime
    currency: str
    impact: str        # "High", "Medium", "Low"
    title: str
    forecast: str = ""
    previous: str = ""

    def to_dict(self) -> Dict:
        return {
            'time': self.time.isoformat(),
            'currency': self.currency,
            'impact': self.impact,
            'title': self.title,
            'forecast': self.forecast,
            'previous': self.previous,
        }


class NewsFilter:
    def __init__(
        self,
        currencies: Optional[List[str]] = None,
        impact_levels: Optional[List[str]] = None,
        cache_path: Path = CACHE_PATH,
    ):
        self.currencies = currencies or ["USD"]
        self.impact_levels = [i.lower() for i in (impact_levels or ["High"])]
        self.cache_path = cache_path
        self.events: List[NewsEvent] = []
        self._last_fetch = 0.0

    def refresh(self, force: bool = False) -> bool:
        """Reload news from cache or fetch fresh."""
        now = time.time()

        # Use cache if fresh
        if not force and self.cache_path.exists():
            age = now - self.cache_path.stat().st_mtime
            if age < CACHE_MAX_AGE_SECONDS:
                try:
                    self._load_cache()
                    return True
                except Exception as e:
                    log.warning(f"Cache load failed: {e}")

        # Fetch fresh
        return self._fetch_fresh()

    def _fetch_fresh(self) -> bool:
        try:
            log.info(f"Fetching news from {FF_URL}")
            req = urllib.request.Request(
                FF_URL,
                headers={'User-Agent': 'XAUUSD-SignalMaster/1.0'}
            )
            with urllib.request.urlopen(req, timeout=15) as resp:
                raw = resp.read().decode('utf-8')
            data = json.loads(raw)

            events = []
            for item in data:
                currency = item.get('country', '').upper()
                if currency not in self.currencies:
                    continue
                impact = item.get('impact', '').lower()
                if impact not in self.impact_levels:
                    continue

                date_str = item.get('date', '')
                try:
                    ev_time = datetime.fromisoformat(
                        date_str.replace('Z', '+00:00')
                    )
                except Exception:
                    continue

                events.append(NewsEvent(
                    time=ev_time,
                    currency=currency,
                    impact=impact.capitalize(),
                    title=item.get('title', ''),
                    forecast=item.get('forecast', '') or '',
                    previous=item.get('previous', '') or '',
                ))

            self.events = events
            self._save_cache()
            self._last_fetch = time.time()
            log.info(
                f"Loaded {len(events)} {'/'.join(self.currencies)} "
                f"{'/'.join(self.impact_levels)} impact events"
            )
            return True
        except (urllib.error.URLError, urllib.error.HTTPError) as e:
            log.error(f"Network error fetching news: {e}")
            return False
        except Exception as e:
            log.error(f"Fetch news failed: {e}")
            return False

    def _save_cache(self) -> None:
        try:
            self.cache_path.parent.mkdir(parents=True, exist_ok=True)
            self.cache_path.write_text(json.dumps(
                [e.to_dict() for e in self.events],
                indent=2
            ))
        except Exception as e:
            log.warning(f"Could not save news cache: {e}")

    def _load_cache(self) -> None:
        data = json.loads(self.cache_path.read_text())
        self.events = []
        for item in data:
            try:
                ev_time = datetime.fromisoformat(item['time'])
                self.events.append(NewsEvent(
                    time=ev_time,
                    currency=item['currency'],
                    impact=item['impact'],
                    title=item['title'],
                    forecast=item.get('forecast', ''),
                    previous=item.get('previous', ''),
                ))
            except Exception:
                continue

    def get_upcoming_events(
        self,
        hours_ahead: int = 24,
        now: Optional[datetime] = None,
    ) -> List[NewsEvent]:
        now = now or datetime.now(timezone.utc)
        cutoff = now + timedelta(hours=hours_ahead)
        return [e for e in self.events if now <= e.time <= cutoff]

    def get_recent_events(
        self,
        hours_back: int = 5,
        now: Optional[datetime] = None,
    ) -> List[NewsEvent]:
        now = now or datetime.now(timezone.utc)
        cutoff = now - timedelta(hours=hours_back)
        return [e for e in self.events if cutoff <= e.time <= now]

    def events_for_guard_check(
        self,
        lookback_hours: int = 5,
        lookahead_hours: int = 1,
        now: Optional[datetime] = None,
    ) -> List[Dict]:
        """
        Returns events within window around 'now' in the format expected
        by FundingPipsGuards._check_news_window().
        """
        now = now or datetime.now(timezone.utc)
        before = now - timedelta(hours=lookback_hours)
        after = now + timedelta(hours=lookahead_hours)
        return [
            {
                'time': e.time,
                'currency': e.currency,
                'impact': e.impact,
                'title': e.title,
            }
            for e in self.events
            if before <= e.time <= after
        ]


if __name__ == "__main__":
    from shared.logger import setup_logger
    setup_logger("news_filter")

    nf = NewsFilter(
        currencies=["USD"],
        impact_levels=["High"],
        cache_path=Path("/tmp/news_cache_test.json")
    )

    if nf.refresh():
        upcoming = nf.get_upcoming_events(hours_ahead=168)  # Week ahead
        log.info(f"Upcoming USD High-impact events (next 7 days): {len(upcoming)}")
        for e in upcoming[:10]:
            log.info(f"  {e.time.isoformat()} | {e.title} | forecast={e.forecast} previous={e.previous}")
