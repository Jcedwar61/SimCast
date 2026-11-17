"""Driver mode: the booth keeps quiet while the driver is busy, and catches up on the straights.

Busy means braking into a corner, a car within a second either side, or a moment off the track. The booth
doesn't start anything new then, and when a car gets alongside, a side conversation that is playing stops.
Whatever happened meanwhile is still called once it's quiet, if it's still news. Stream mode is the full show.
"""
from __future__ import annotations

BRAKE_ON = 0.12            # pedal travel that counts as braking
BRAKE_HOLD = 0.8           # seconds after the brake comes off before the booth talks again (turn-in, apex)
CLOSE_IN, CLOSE_OUT = 0.7, 1.0   # seconds to the next car: busy under CLOSE_IN, free again over CLOSE_OUT
OFF_HOLD = 2.5             # seconds of quiet after a trip off the track
CATCH_UP_MAX = 15.0        # a moment held back this long at most is still worth calling afterwards
LONG_FIGHT = 15.0          # close to a car this long: a fight, not a moment; race calls go out between the corners


class DriverLoad:
    def __init__(self):
        self.brake_until = -1e9
        self.off_until = -1e9
        self.close = False
        self.close_since = None
        self.last_t = None

    def update(self, frame, now, tracker=None):
        """From each telemetry frame: the pedal, the gaps either side and whether the car is off the track."""
        self.last_t = now
        if (getattr(frame, "brake", 0.0) or 0.0) >= BRAKE_ON and (getattr(frame, "speed", 0.0) or 0.0) > 8.0:
            self.brake_until = now + BRAKE_HOLD
        if getattr(frame, "off_track", False):
            self.off_until = now + OFF_HOLD
        gaps = [g for g in (getattr(frame, "gap_ahead", None), getattr(frame, "gap_behind", None))
                if g is not None and g >= 0]
        if not gaps and tracker is not None:
            gaps = _tracker_gaps(tracker)
        near = min(gaps) if gaps else None
        if near is None:
            self.close = False
        elif self.close:
            self.close = near < CLOSE_OUT
        else:
            self.close = near < CLOSE_IN
        if getattr(frame, "in_pit", False):
            self.close = False                     # the pit lane is a quiet place to talk
        if self.close:
            self.close_since = now if self.close_since is None else self.close_since
        else:
            self.close_since = None

    def busy(self, now):
        """Why the driver is busy right now ('close', 'braking', 'off'), or '' when the booth can talk."""
        if self.last_t is None or now - self.last_t > 2.0:
            return ""                              # no telemetry: nothing to hold for
        if self.close:
            if self.close_since is not None and now - self.close_since > LONG_FIGHT and now >= self.brake_until \
                    and now >= self.off_until:
                return "fight"                     # a long fight, on a straight: race calls only, no chat
            return "close"
        if now < self.off_until:
            return "off"
        if now < self.brake_until:
            return "braking"
        return ""


def _tracker_gaps(tr):
    """Gaps to the cars either side of the viewer from the race tracker, for sims whose frame doesn't carry them."""
    try:
        me = tr.player()
        if me is None or me.gone:
            return []
        out = []
        if me.interval is not None:
            out.append(me.interval)
        b = tr.at(tr.pos_of(me.id) + 1)
        if b is not None and b.interval is not None:
            out.append(b.interval)
        return out
    except Exception:
        return []
