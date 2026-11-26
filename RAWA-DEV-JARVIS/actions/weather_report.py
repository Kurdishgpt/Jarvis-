import webbrowser
from urllib.parse import quote_plus


# Kirkuk default coords (Open-Meteo — free, no API key)
_CITY_COORDS = {
    "kirkuk": (35.4681, 44.3922),
    "کەرکوک": (35.4681, 44.3922),
    "kerkuk": (35.4681, 44.3922),
}


def fetch_weather_summary(city: str = "Kirkuk") -> str:
    """
    Fetch live weather via Open-Meteo and return a plain-English fact block
    for JARVIS to speak (e.g. in the morning briefing).
    """
    import requests

    key = (city or "Kirkuk").strip().lower()
    lat, lon = _CITY_COORDS.get(key, (35.4681, 44.3922))
    label = city.strip() or "Kirkuk"

    url = "https://api.open-meteo.com/v1/forecast"
    params = {
        "latitude": lat,
        "longitude": lon,
        "current": (
            "temperature_2m,relative_humidity_2m,apparent_temperature,"
            "precipitation,weather_code,wind_speed_10m,wind_direction_10m"
        ),
        "daily": "temperature_2m_max,temperature_2m_min,precipitation_probability_max",
        "timezone": "Asia/Baghdad",
        "forecast_days": 1,
    }
    r = requests.get(url, params=params, timeout=8)
    r.raise_for_status()
    data = r.json()

    cur = data.get("current") or {}
    daily = data.get("daily") or {}

    def _first(k, default="?"):
        vals = daily.get(k) or []
        return vals[0] if vals else default

    code = cur.get("weather_code", 0)
    if code == 0:
        sky = "clear / sunny"
    elif code in (1, 2, 3):
        sky = "partly cloudy"
    elif code in (45, 48):
        sky = "foggy"
    elif code in (51, 53, 55, 61, 63, 65, 80, 81, 82):
        sky = "rainy"
    elif code in (71, 73, 75, 77, 85, 86):
        sky = "snowy"
    elif code in (95, 96, 99):
        sky = "stormy"
    else:
        sky = "mixed"

    wind = cur.get("wind_speed_10m", "?")

    lines = [
        f"City: {label}",
        f"Sky: {sky}",
        f"Current temperature: {cur.get('temperature_2m', '?')}°C",
        f"Feels like: {cur.get('apparent_temperature', '?')}°C",
        f"Humidity: {cur.get('relative_humidity_2m', '?')}%",
        f"Wind: {wind} km/h",
        f"Today high: {_first('temperature_2m_max')}°C",
        f"Today low: {_first('temperature_2m_min')}°C",
        f"Rain chance: {_first('precipitation_probability_max')}%",
        f"Precipitation now: {cur.get('precipitation', 0)} mm",
    ]
    return "\n".join(lines)


def weather_action(
    parameters: dict,
    player=None,
    session_memory=None,
) -> str:
    city = parameters.get("city")
    when = parameters.get("time", "today")

    if not city or not isinstance(city, str) or not city.strip():
        msg = "گەورەم، ناوی شارەکە بۆ کەشوهەوا نەدراوە."
        _log(msg, player)
        return msg

    city = city.strip()
    when = (when or "today").strip()

    # Prefer live numbers so the model can speak accurately in Sorani
    try:
        summary = fetch_weather_summary(city)
        msg = f"Weather data for {city}:\n{summary}"
        _log(msg, player)
        return msg
    except Exception as e:
        print(f"[Weather] Open-Meteo failed ({e}) — opening Google fallback")

    search_query = f"weather in {city} {when}"
    url = f"https://www.google.com/search?q={quote_plus(search_query)}"

    try:
        opened = webbrowser.open(url)
        if not opened:
            raise RuntimeError("webbrowser.open returned False")
    except Exception as e:
        msg = f"گەورەم، نەمتوانی کەشوهەوا بکەمەوە: {e}"
        _log(msg, player)
        return msg

    msg = f"Opened Google weather for {city}, {when}. Look up current conditions and speak them."
    _log(msg, player)

    if session_memory:
        try:
            session_memory.set_last_search(query=search_query, response=msg)
        except Exception:
            pass

    return msg


def _log(message: str, player=None) -> None:
    print(f"[Weather] {message}")
    if player:
        try:
            player.write_log(f"JARVIS: {message}")
        except Exception:
            pass
