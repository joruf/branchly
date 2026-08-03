"""
Data model for sidebar categories.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any

UNCATEGORIZED_ORDER = 10_000


@dataclass(slots=True)
class Category:
    """
    A user-defined group of repositories in the sidebar.

    Attributes:
        name: Display name. An empty name is the implicit "uncategorized" group.
        collapsed: Whether the group is folded shut.
        order: Position among the categories.
    """

    name: str
    collapsed: bool = False
    order: int = 0

    @property
    def is_uncategorized(self) -> bool:
        """
        Returns whether this is the implicit catch-all group.

        Returns:
            bool: True for the unnamed group.
        """

        return not self.name

    @property
    def sort_key(self) -> tuple[int, int, str]:
        """
        Returns the ordering key that keeps the catch-all group last.

        Returns:
            tuple[int, int, str]: Sort key for category lists.
        """

        return (1 if self.is_uncategorized else 0, self.order, self.name.lower())

    def to_dict(self) -> dict[str, Any]:
        """
        Serializes the category for ``repos.json``.

        Returns:
            dict[str, Any]: JSON-compatible mapping.
        """

        return {"name": self.name, "collapsed": self.collapsed, "order": self.order}

    @classmethod
    def from_dict(cls, data: Any) -> Category | None:
        """
        Rebuilds a category from stored data.

        Args:
            data: Mapping read from ``repos.json``.

        Returns:
            Category | None: Restored category, or None when malformed.
        """

        if not isinstance(data, dict):
            return None
        name = data.get("name")
        if not isinstance(name, str):
            return None
        order = data.get("order")
        return cls(
            name=name,
            collapsed=bool(data.get("collapsed")),
            order=order if isinstance(order, int) else 0,
        )


def normalize_category_name(name: str | None) -> str:
    """
    Cleans a category name coming from user input.

    Collapsing whitespace matters because the name doubles as the key that ties
    repositories to their category.

    Args:
        name: Raw name from a dialog or a config file.

    Returns:
        str: Trimmed single-line name, empty for the catch-all group.
    """

    if not isinstance(name, str):
        return ""
    return " ".join(name.split())
