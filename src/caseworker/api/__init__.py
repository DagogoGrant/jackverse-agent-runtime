"""Caseworker API package exposing application factory and configuration."""

from __future__ import annotations

from caseworker.api.app import create_app
from caseworker.api.config import APIConfig

__all__ = ["APIConfig", "create_app"]
