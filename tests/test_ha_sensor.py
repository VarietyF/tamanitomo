"""Tests for Home Assistant read-only presence sensor."""

import datetime as dt
import json
import unittest.mock as mock
import pytest

import companion_config as cc
import companion_sensors as sensors

UTC = dt.timezone.utc
NOW = dt.datetime(2026, 11, 27, 12, 0, tzinfo=UTC)


@pytest.fixture
def companion(tmp_path):
    token_file = tmp_path / "ha_token.txt"
    token_file.write_text("test-token-xyz\n", encoding="utf-8")
    c = cc.Companion(
        agent="Nell",
        human="Martin",
        hermes_root=tmp_path / "hermes",
        vault=tmp_path / "vault",
        timezone="UTC",
        ha_url="http://192.168.100.1:8123",
        ha_token_file=str(token_file),
        ha_person="person.martin",
    )
    return c


def test_ha_sensor_registered():
    assert "homeassistant" in sensors.REGISTRY
    assert sensors.REGISTRY["homeassistant"]["fn"] == "homeassistant"


def test_ha_sensor_missing_url(companion):
    companion.ha_url = ""
    text, extra = sensors.homeassistant(companion, NOW)
    assert text is None
    assert isinstance(extra, sensors.Broken)
    assert "ha_url empty" in str(extra)


def test_ha_sensor_missing_token(companion, tmp_path):
    companion.ha_token_file = str(tmp_path / "nonexistent.txt")
    text, extra = sensors.homeassistant(companion, NOW)
    assert text is None
    assert isinstance(extra, sensors.Broken)
    assert "token missing" in str(extra)


def test_ha_sensor_success(companion):
    mock_response = mock.MagicMock()
    mock_response.read.return_value = json.dumps({
        "state": "home",
        "attributes": {"friendly_name": "Martin", "latitude": 47.23}
    }).encode("utf-8")
    mock_response.__enter__.return_value = mock_response

    with mock.patch("urllib.request.urlopen", return_value=mock_response) as mock_urlopen:
        text, extra = sensors.homeassistant(companion, NOW)
        assert text == "Martin is currently home."
        assert isinstance(extra, dict)
        assert extra["state"] == "home"
        assert extra["entity_id"] == "person.martin"

        req = mock_urlopen.call_args[0][0]
        assert req.full_url == "http://192.168.100.1:8123/api/states/person.martin"
        assert req.headers["Authorization"] == "Bearer test-token-xyz"
