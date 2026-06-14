"""Schema v2 Web adapter for the project registry."""

from .server import RegistryWebServer, build_parser, main

__all__ = ["RegistryWebServer", "build_parser", "main"]
