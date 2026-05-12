"""GitHub Actions metrics analysis library."""

from .dashboard import DashboardConfig, RelatedRepository, build_dashboard, load_dashboard_config

__all__ = [
    "DashboardConfig",
    "RelatedRepository",
    "build_dashboard",
    "load_dashboard_config",
]
