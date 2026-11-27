"""Which lane each car is in, from where the sim says the cars are in the world (Assetto Corsa's CrewChiefEx app
shares every car's position). Nothing here is NASCAR-specific: it learns the track's groove from the cars and
measures each car against it.

The groove is learned as the cars drive: the track is cut into BINS slices by lap fraction, and each slice keeps
the average position of every car through it. A car's lateral offset is its distance from that average line,
measured across the track: negative is towards the inside of the track (the bottom of an oval), positive towards
the outside (the top, up by the wall). On an oval 'inside' is towards the middle of the track; on a road course
the words low and high don't mean much, so the commentary only uses them on ovals.
"""
from __future__ import annotations

import math

BINS = 200                 # slices of the lap
READY_SAMPLES = 12         # cars through a slice before its average line is trusted
ALONGSIDE_LAT = 1.8        # metres apart across the track: two lanes, not nose to tail
ALONGSIDE_LONG = 5.5       # metres apart along the track: overlapping, side by side


class LaneMap:
    def __init__(self):
        self.ref = [None] * BINS                 # the average line through each slice: (x, z)
        self.n = [0] * BINS
        self._centre = None
        self._feeds = 0

    def feed(self, pct, x, z):
        """A car at lap fraction pct, at world position x, z (metres, AC's y is up and isn't used)."""
        i = int((pct % 1.0) * BINS) % BINS
        r = self.ref[i]
        if r is None:
            self.ref[i] = (x, z)
        else:
            a = max(0.02, 1.0 / (self.n[i] + 1))
            self.ref[i] = (r[0] + (x - r[0]) * a, r[1] + (z - r[1]) * a)
        self.n[i] += 1
        self._feeds += 1
        if self._feeds % 400 == 0:
            self._centre = None

    def centre(self):
        if self._centre is None:
            pts = [r for r in self.ref if r is not None]
            if len(pts) < BINS // 3:
                return None
            self._centre = (sum(p[0] for p in pts) / len(pts), sum(p[1] for p in pts) / len(pts))
        return self._centre

    def _near(self, i, step):
        for k in range(1, 6):
            j = (i + step * k) % BINS
            if self.ref[j] is not None and self.n[j] >= READY_SAMPLES:
                return self.ref[j]
        return None

    def lateral(self, pct, x, z):
        """Metres from the average line across the track (+ towards the outside), or None while it's still being
        learned here."""
        i = int((pct % 1.0) * BINS) % BINS
        if self.ref[i] is None or self.n[i] < READY_SAMPLES:     # a slice the cars skipped: the nearest learned one
            i = next(((i + d) % BINS for d in (1, -1, 2, -2, 3, -3)
                      if self.ref[(i + d) % BINS] is not None and self.n[(i + d) % BINS] >= READY_SAMPLES), None)
            if i is None:
                return None
        r = self.ref[i]
        a, b, c = self._near(i, -1), self._near(i, 1), self.centre()
        if a is None or b is None or c is None:
            return None
        tx, tz = b[0] - a[0], b[1] - a[1]
        ln = math.hypot(tx, tz)
        if ln < 1e-6:
            return None
        nx, nz = -tz / ln, tx / ln                       # across the track
        if (r[0] - c[0]) * nx + (r[1] - c[1]) * nz < 0:  # make it point away from the middle: the outside
            nx, nz = -nx, -nz
        return (x - r[0]) * nx + (z - r[1]) * nz


def lane_word(lat_a, lat_b):
    """Where car a is against car b: 'low', 'high', or None when they're in the same lane."""
    if lat_a is None or lat_b is None:
        return None
    d = lat_a - lat_b
    if d <= -ALONGSIDE_LAT:
        return "low"
    if d >= ALONGSIDE_LAT:
        return "high"
    return None
