from __future__ import annotations

from math import asin, cos, radians, sin, sqrt

from rapidfuzz import fuzz

from .models import FinderEntry, Venue


def haversine_m(lat1, lon1, lat2, lon2) -> float:
    r = 6371000.0
    dlat, dlon = radians(lat2 - lat1), radians(lon2 - lon1)
    a = sin(dlat / 2) ** 2 + cos(radians(lat1)) * cos(radians(lat2)) * sin(dlon / 2) ** 2
    return 2 * r * asin(sqrt(a))


def match_entry(entry: FinderEntry, venues: list[Venue],
                name_threshold: int = 85, max_dist_m: float = 120) -> Venue | None:
    # Venues span all of Germany, so a name-only entry can collide with a
    # same-named venue in another city — a tie at the best score means we can't
    # tell them apart and the entry stays unmatched. Entries with coordinates
    # are disambiguated by the distance filter before names are compared.
    best, best_score, tied = None, -1.0, False
    for v in venues:
        if entry.lat is not None and entry.lon is not None:
            if haversine_m(entry.lat, entry.lon, v.lat, v.lon) > max_dist_m:
                continue
        score = fuzz.token_sort_ratio(entry.name.lower(), v.name.lower())
        if score < name_threshold:
            continue
        if score > best_score:
            best, best_score, tied = v, score, False
        elif score == best_score:
            tied = True
    return None if tied else best


def match_entries(entries, venues):
    matched, unmatched = [], []
    for e in entries:
        v = match_entry(e, venues)
        (matched.append((e, v)) if v else unmatched.append(e))
    return matched, unmatched
