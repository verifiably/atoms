"""A5b -- the coordinator (design §4.1).

Nothing is exported. A5b ships no consumer-facing command: every entry point accepts a
package-private `Lease`, and authority §12.1 owns the first real consumer. `__all__`
stays empty until one exists.
"""

from __future__ import annotations

__all__ = ()
