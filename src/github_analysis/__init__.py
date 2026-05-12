"""GitHub Actions metrics analysis library."""

from .dashboard import DashboardConfig, build_dashboard, load_dashboard_config

__all__ = [
    "DashboardConfig",
    "build_dashboard",
    "load_dashboard_config",
]
