"""Constants for the ProScan Client integration."""

from __future__ import annotations

DOMAIN = "proscan"

CONF_STREAM_NAME = "stream_name"
CONF_STREAM_PASSWORD = "stream_password"
CONF_MONITOR_PASSWORD = "monitor_password"
CONF_IDLE_TIMEOUT = "idle_timeout"

DEFAULT_STREAM_NAME = "radio"
DEFAULT_IDLE_TIMEOUT = 60  # seconds the display socket stays open after the last viewer

# How many recent display frames to keep for "B,<checksum>" repeat references.
FRAME_CACHE_SIZE = 20

# Seconds a snapshot request waits for the first frame after connecting.
FIRST_FRAME_TIMEOUT = 8

# Consecutive connection failures before the camera reports unavailable.
MAX_FAILURES = 3
