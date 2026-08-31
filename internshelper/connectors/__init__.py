"""Source connectors: one normalized fetch() per source type.

Importing this package registers every connector with the registry so
build_connector() can resolve a source type to its connector class.
"""

from internshelper.connectors.base import Connector, FetchResult, build_connector, register

# Import side effect: each module registers its connector class.
from internshelper.connectors import (  # noqa: E402,F401
    amazon,
    ashby,
    github_list,
    greenhouse,
    lever,
    markdown_list,
    workday,
)

__all__ = ["Connector", "FetchResult", "build_connector", "register"]
