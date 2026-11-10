"""
nlagent.service - Core background daemon service and local token-authenticated API.

Provides local IPC over HTTP for CLI, shell keybinding hooks, and desktop UI,
eliminating per-call process startup overhead while preserving full fallback
to in-process execution when the daemon is not active.
"""

from __future__ import annotations

from nlagent.service.api import ServiceAPI, ServiceHTTPRequestHandler
from nlagent.service.core import (
    ServiceClient,
    ServiceDaemon,
    ServiceInfo,
    get_service_file,
    get_state_dir,
    handle_service_command,
    load_service_info,
    remove_service_file,
    save_service_info,
)

__all__ = [
    "ServiceAPI",
    "ServiceClient",
    "ServiceDaemon",
    "ServiceHTTPRequestHandler",
    "ServiceInfo",
    "get_service_file",
    "get_state_dir",
    "handle_service_command",
    "load_service_info",
    "remove_service_file",
    "save_service_info",
]

