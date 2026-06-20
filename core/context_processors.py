"""
Context processor: injects `conflict_count` into every template context
so the base sidebar can display a live badge on the "Conflits" link.

The count is computed cheaply (schedule-only conflicts, O(n²) on schedules)
and cached per-request so it's called at most once per page.
"""
from django.core.cache import cache
from django.conf import settings


def conflicts_count(request):
    """
    Returns {'conflict_count': <int>} with the total number of active
    schedule conflicts (room + teacher overlaps).

    Results are cached for 30 seconds so every page load doesn't trigger
    a full scan.  The cache is invalidated by the post_save signal on
    CourseGroupSchedule (see models.py).
    """
    CACHE_KEY = 'sidebar_conflict_count'
    CACHE_TTL = getattr(settings, 'CONFLICT_CACHE_TTL', 30)  # seconds

    count = cache.get(CACHE_KEY)
    if count is None:
        try:
            from .utils import detect_all_conflicts
            data = detect_all_conflicts()
            # Only schedule conflicts matter for the sidebar badge
            count = len(data.get('schedule_conflicts', []))
        except Exception:
            count = 0
        cache.set(CACHE_KEY, count, CACHE_TTL)

    return {'conflict_count': count}
