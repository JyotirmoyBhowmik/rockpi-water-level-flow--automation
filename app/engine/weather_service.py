"""
Weather-Aware Irrigation Service for Smart Water Controller.
Fetches public Open-Meteo forecast (no API key required) every 3 hours via httpx with timeout.
Evaluates rain probability in the next N hours and past 24h rainfall to skip unnecessary watering.
"""

from dataclasses import dataclass
from datetime import datetime, timezone
import logging
from typing import Any, Dict, List, Optional, Tuple
import httpx

from app.config import system_config
from app.core.state_manager import state_manager
from app.hardware.rtc import get_monotonic_now

logger = logging.getLogger("smart_water.weather")


@dataclass
class WeatherForecast:
    timestamp: float
    rain_prob_max_pct: float
    rainfall_past_24h_mm: float
    current_temp_c: Optional[float]
    skip_active: bool
    skip_reason: Optional[str]


class WeatherService:
    """Manages Open-Meteo forecast caching, timeout-safe polling, and rain skips."""

    def __init__(self) -> None:
        self._last_fetch_mono: float = 0.0
        self._last_forecast: Optional[WeatherForecast] = None
        self._mock_forecast: Optional[Tuple[float, float, Optional[float]]] = None
        self._status: str = "IDLE"

    def set_mock_forecast(
        self,
        rain_prob_max_pct: float,
        rainfall_past_24h_mm: float,
        current_temp_c: Optional[float] = 25.0,
    ) -> None:
        """Sets simulated weather data for unit tests and local sandboxes."""
        self._mock_forecast = (rain_prob_max_pct, rainfall_past_24h_mm, current_temp_c)
        self._evaluate_and_apply(rain_prob_max_pct, rainfall_past_24h_mm, current_temp_c)

    def clear_mock_forecast(self) -> None:
        """Clears simulated mock data."""
        self._mock_forecast = None
        self._last_forecast = None
        self._status = "IDLE"

    def is_available(self) -> bool:
        """Returns True if weather forecasting is enabled in config."""
        return bool(system_config.weather.enabled)

    def get_status_dict(self) -> Dict[str, Any]:
        """Returns weather telemetry summary for state and API inspection."""
        cfg = system_config.weather
        if not cfg.enabled:
            return {
                "enabled": False,
                "status": "DISABLED",
                "skip_active": False,
                "skip_reason": None,
                "rain_prob_max_pct": None,
                "forecast_rain_prob_pct": None,
                "rainfall_past_24h_mm": None,
                "past_rain_24h_mm": None,
                "temperature_c": None,
                "last_fetch_mono": None,
            }

        fc = self._last_forecast
        rain_prob = fc.rain_prob_max_pct if fc else None
        past_rain = fc.rainfall_past_24h_mm if fc else None
        return {
            "enabled": True,
            "status": self._status,
            "skip_active": fc.skip_active if fc else False,
            "skip_reason": fc.skip_reason if fc else None,
            "rain_prob_max_pct": rain_prob,
            "forecast_rain_prob_pct": rain_prob,
            "rainfall_past_24h_mm": past_rain,
            "past_rain_24h_mm": past_rain,
            "temperature_c": fc.current_temp_c if fc else None,
            "last_fetch_mono": self._last_fetch_mono if fc else None,
        }

    def should_skip_watering(self) -> Tuple[bool, Optional[str]]:
        """
        Returns (skip_active, reason) if automatic irrigation should be skipped
        due to predicted upcoming rain or recent rainfall.
        """
        cfg = system_config.weather
        if not cfg.enabled:
            return False, None

        if self._mock_forecast is not None:
            prob, past, _ = self._mock_forecast
            return self._check_thresholds(prob, past)

        if self._last_forecast is not None:
            return self._last_forecast.skip_active, self._last_forecast.skip_reason

        return False, None

    def _check_thresholds(self, rain_prob_max: float, past_rain_mm: float) -> Tuple[bool, Optional[str]]:
        """Evaluates rain probability and past rainfall against configured limits."""
        cfg = system_config.weather
        if rain_prob_max > cfg.rain_prob_threshold_pct:
            reason = (
                f"Rain probability ({rain_prob_max:.0f}%) exceeds threshold "
                f"({cfg.rain_prob_threshold_pct:.0f}%) in next {cfg.forecast_lookahead_hours}h."
            )
            return True, reason

        if past_rain_mm > cfg.past_rain_threshold_mm:
            reason = (
                f"Recent 24h rainfall ({past_rain_mm:.1f} mm) exceeds threshold "
                f"({cfg.past_rain_threshold_mm:.1f} mm)."
            )
            return True, reason

        return False, None

    def _evaluate_and_apply(
        self,
        rain_prob_max: float,
        past_rain_mm: float,
        temp_c: Optional[float],
    ) -> WeatherForecast:
        """Determines skip status, updates cached forecast, and updates StateManager."""
        skip_active, skip_reason = self._check_thresholds(rain_prob_max, past_rain_mm)
        forecast = WeatherForecast(
            timestamp=get_monotonic_now(),
            rain_prob_max_pct=round(rain_prob_max, 1),
            rainfall_past_24h_mm=round(past_rain_mm, 1),
            current_temp_c=round(temp_c, 1) if temp_c is not None else None,
            skip_active=skip_active,
            skip_reason=skip_reason,
        )
        self._last_forecast = forecast
        self._last_fetch_mono = get_monotonic_now()
        self._status = "OK"

        # Update StateManager in-memory status
        state = state_manager._state.get("weather")
        if state is not None:
            state["status"] = "OK"
            state["skip_active"] = skip_active
            state["skip_reason"] = skip_reason
            state["rain_prob_max_pct"] = forecast.rain_prob_max_pct
            state["forecast_rain_prob_pct"] = forecast.rain_prob_max_pct
            state["rainfall_past_24h_mm"] = forecast.rainfall_past_24h_mm
            state["past_rain_24h_mm"] = forecast.rainfall_past_24h_mm
            state["temperature_c"] = forecast.current_temp_c
            state["last_fetch_ts"] = datetime.now(timezone.utc).timestamp()

        if skip_active:
            logger.info("WEATHER SKIP ACTIVE: %s", skip_reason)

        return forecast

    @property
    def last_poll_ts(self) -> float:
        return self._last_fetch_mono

    async def fetch_forecast_if_due(self, force: bool = False) -> Optional[WeatherForecast]:
        """
        Polls Open-Meteo forecast API every 3 hours with strict timeout.
        Respects Rule 3.1 (explicit connect/read timeouts) and Rule 2.1 (no silent failures).
        """
        cfg = system_config.weather
        if not cfg.enabled:
            return None

        if self._mock_forecast is not None:
            prob, past, temp = self._mock_forecast
            return self._evaluate_and_apply(prob, past, temp)

        now_mono = get_monotonic_now()
        poll_interval_s = cfg.poll_interval_hours * 3600.0

        if not force and self._last_forecast and (now_mono - self._last_fetch_mono < poll_interval_s):
            return self._last_forecast

        url = "https://api.open-meteo.com/v1/forecast"
        params: Dict[str, Any] = {
            "latitude": cfg.latitude,
            "longitude": cfg.longitude,
            "hourly": "precipitation_probability,precipitation,temperature_2m",
            "past_days": 1,
            "forecast_days": 2,
            "timezone": "UTC",
        }

        timeout = httpx.Timeout(cfg.timeout_seconds, connect=5.0)
        try:
            async with httpx.AsyncClient(timeout=timeout) as client:
                resp = await client.get(url, params=params)
                resp.raise_for_status()
                data = resp.json()
        except httpx.TimeoutException:
            logger.warning("Open-Meteo forecast API request timed out after %.1fs.", cfg.timeout_seconds)
            self._status = "TIMEOUT"
            state = state_manager._state.get("weather")
            if state is not None:
                state["status"] = "TIMEOUT"
            return self._last_forecast
        except Exception as exc:
            logger.warning("Open-Meteo forecast API fetch failed: %s", exc)
            self._status = "ERROR"
            state = state_manager._state.get("weather")
            if state is not None:
                state["status"] = "ERROR"
            return self._last_forecast

        # Parse hourly precipitation and probability
        try:
            hourly = data.get("hourly", {})
            times: List[str] = hourly.get("time", [])
            probs: List[float] = hourly.get("precipitation_probability", [])
            precips: List[float] = hourly.get("precipitation", [])
            temps: List[float] = hourly.get("temperature_2m", [])

            if not times or not probs or not precips:
                logger.warning("Open-Meteo returned empty hourly forecast dataset.")
                return self._last_forecast

            # Find index matching current UTC hour
            current_iso_hour = datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:00")
            curr_idx = 24  # Default to 24 (past_days=1 means first 24 hours are yesterday)
            for idx, t_str in enumerate(times):
                if t_str >= current_iso_hour:
                    curr_idx = idx
                    break

            lookahead = cfg.forecast_lookahead_hours
            future_probs = probs[curr_idx : curr_idx + lookahead]
            max_rain_prob = max(future_probs, default=0.0)

            # Sum precipitation in the past 24 hours
            past_start = max(0, curr_idx - 24)
            past_precips = precips[past_start:curr_idx]
            past_rain_24h = sum(past_precips)

            current_temp = temps[curr_idx] if curr_idx < len(temps) else None

            logger.info(
                "Open-Meteo forecast updated: max rain prob next %dh = %.1f%%, past 24h rainfall = %.1f mm",
                lookahead,
                max_rain_prob,
                past_rain_24h,
            )
            return self._evaluate_and_apply(max_rain_prob, past_rain_24h, current_temp)

        except Exception as parse_exc:
            logger.error("Failed to parse Open-Meteo forecast payload: %s", parse_exc)
            return self._last_forecast


weather_service = WeatherService()
