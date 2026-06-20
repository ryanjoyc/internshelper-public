"""Apply the user's filter booleans to a classified posting.

Pure function over a Posting's flags — works identically for active or archived
(closed) rows, since it only reads the classification flags.
"""

from __future__ import annotations

from internshelper.models import Posting


def passes(
    posting: Posting,
    *,
    require_cs: bool = True,
    require_intern_or_newgrad: bool = True,
) -> bool:
    """True if the posting satisfies the active filter requirements."""
    if require_cs and not posting.is_cs_relevant:
        return False
    if require_intern_or_newgrad and not (posting.is_internship or posting.is_newgrad):
        return False
    return True
