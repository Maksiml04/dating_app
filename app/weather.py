"""FR-07: conservative weather advisory for outdoor dates in Moscow.

Forecast is external data, never LLM generated. No forecast -> unknown,
not a fabricated 'good weather' decision. Static pilot thresholds.
"""
from dataclasses import dataclass
from datetime import datetime, timezone
import httpx

URL = 'https://api.open-meteo.com/v1/forecast'
MOSCOW = (55.7558, 37.6173)
RAIN_PROBABILITY_THRESHOLD = 65
WIND_SPEED_KMH_THRESHOLD = 55

@dataclass(frozen=True)
class WeatherAssessment:
    status: str  # favorable, adverse, unknown, not_applicable
    checked_at: datetime | None
    reason: str


def assess_outdoors(starts_at: datetime | None, ends_at: datetime | None,
                    *, transport=None, now: datetime | None = None):
    now = now or datetime.now(timezone.utc)
    if (not starts_at or not ends_at or starts_at.tzinfo is None or
        ends_at.tzinfo is None or starts_at >= ends_at):
        return WeatherAssessment('unknown', None, 'Неизвестен интервал для прогноза')
    if starts_at < now or (starts_at - now).days > 15:
        return WeatherAssessment('unknown', None, 'Прогноз на эту дату недоступен')
    try:
        with httpx.Client(timeout=4.0, transport=transport) as client:
            response = client.get(URL, params={
                'latitude': MOSCOW[0], 'longitude': MOSCOW[1],
                'hourly': 'precipitation_probability,wind_speed_10m',
                'timezone': 'UTC', 'forecast_days': 16,
            })
            response.raise_for_status()
            hours = response.json().get('hourly', {})
            dates = hours.get('time', [])
            rain = hours.get('precipitation_probability', [])
            wind = hours.get('wind_speed_10m', [])
            if not (len(dates) == len(rain) == len(wind)):
                raise ValueError('Incomplete weather response')
            relevant = []
            for stamp, probability, wind_speed in zip(dates, rain, wind):
                moment = datetime.fromisoformat(stamp).replace(tzinfo=timezone.utc)
                if starts_at <= moment < ends_at:
                    relevant.append((probability, wind_speed))
            if not relevant or any(p is None or w is None for p, w in relevant):
                return WeatherAssessment('unknown', None, 'Прогноз на нужный интервал отсутствует')
            if any(p >= RAIN_PROBABILITY_THRESHOLD or w >= WIND_SPEED_KMH_THRESHOLD
                   for p, w in relevant):
                return WeatherAssessment('adverse', now, 'Прогнозируется сильный дождь или ветер')
            return WeatherAssessment('favorable', now, 'По городскому прогнозу сильных осадков и ветра не ожидается')
    except (httpx.HTTPError, ValueError, TypeError, KeyError):
        return WeatherAssessment('unknown', None, 'Сервис погоды недоступен')
