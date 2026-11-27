"""Tests for weather-coupled wardrobe guidance and ambient context priority."""

import datetime as dt
import json
import pytest

import companion_config as cc
import companion_context as context
import companion_lifestyle as lifestyle

UTC = dt.timezone.utc
NOW = dt.datetime(2026, 11, 27, 12, 0, tzinfo=UTC)


@pytest.fixture
def companion(tmp_path):
    c = cc.Companion(
        agent="Nell",
        human="Martin",
        hermes_root=tmp_path / "hermes",
        vault=tmp_path / "vault",
        timezone="UTC",
        soul_in_vault=False,
    )
    c.soul_dir.mkdir(parents=True, exist_ok=True)
    (c.soul_dir / "ambient").mkdir(parents=True, exist_ok=True)
    return c


def test_weather_advice_cold_and_rain(companion):
    w_file = companion.soul_dir / "ambient" / "weather.json"
    w_file.write_text(
        json.dumps({"temp_c": "3", "description": "Light rain and drizzle"}),
        encoding="utf-8",
    )
    advice = lifestyle.weather_advice(companion)
    assert "cold/wet" in advice
    assert "warm knitwear/layers" in advice
    assert "outerwear/coat or rain-appropriate boots" in advice


def test_weather_advice_warm(companion):
    w_file = companion.soul_dir / "ambient" / "weather.json"
    w_file.write_text(
        json.dumps({"temp_c": "25", "description": "Sunny"}),
        encoding="utf-8",
    )
    advice = lifestyle.weather_advice(companion)
    assert "warm (25°C, sunny)" in advice
    assert "lighter clothing/breathable fabrics" in advice


def test_weather_advice_mild(companion):
    w_file = companion.soul_dir / "ambient" / "weather.json"
    w_file.write_text(
        json.dumps({"temp_c": "17", "description": "Partly cloudy"}),
        encoding="utf-8",
    )
    advice = lifestyle.weather_advice(companion)
    assert "mild (17°C, partly cloudy)" in advice
    assert "dress comfortably in layers" in advice


def test_weather_advice_missing_or_corrupt(companion):
    assert lifestyle.weather_advice(companion) == ""
    w_file = companion.soul_dir / "ambient" / "weather.json"
    w_file.write_text("invalid json", encoding="utf-8")
    assert lifestyle.weather_advice(companion) == ""


def test_ambient_context_priority_is_seven():
    import inspect
    src = inspect.getsource(context)
    assert '(7, "[Ambient context]\\n"' in src
