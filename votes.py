"""Blend simultaneous viewers' clicks into one camera target.

Without this, clicks fight: each one supersedes the last, so with
several people watching the camera lurches between their choices and
nobody's click survives long enough to matter.

The model, from the June design:

  * Each click is a vote for an absolute direction, not a nudge. Where
    the camera was pointing when the click arrived decides which real
    direction the clicked pixel corresponds to.

  * Votes decay exponentially with age, so a burst of old clicks cannot
    drag the camera back somewhere it has already left.

  * One active vote per viewer, replaced on each new click. Otherwise a
    single person clicking repeatedly outvotes a crowd clicking once.
    The rate limiter caps how often; this caps how much.

  * A dead zone: if the consensus has not moved meaningfully from where
    the camera is already headed, do nothing. Without it the consensus
    drifts by fractions of a degree forever and the camera never
    settles.

Consensus is a weighted MEDIAN, not a mean. The original sketch said
mean, which turns out to be the wrong tool: three viewers clicking a
bird at -10 degrees and one clicking the far corner at +50 average to
+5, pointing at nothing anybody wanted. One outlier drags everyone, and
on a public camera that outlier is the whole threat model. The median
ignores it and lands on -10. Where viewers broadly agree -- the normal
case, everyone watching the same bird -- the two give near-identical
answers, so robustness costs nothing.
"""

import math
import threading
import time


def weighted_median(pairs):
    """Median of (value, weight) pairs, interpolated at the crossing.

    Interpolating rather than picking a sample matters at low voter
    counts: with two equal votes it returns the midpoint, which is the
    intuitive answer, instead of arbitrarily choosing one of them. With
    many votes it behaves like an ordinary median and shrugs off
    outliers.
    """
    if not pairs:
        return None
    pairs = sorted(pairs)
    total = sum(w for _, w in pairs)
    if total <= 0:
        return None

    half = total / 2.0
    cumulative = 0.0
    for i, (value, weight) in enumerate(pairs):
        previous = cumulative
        cumulative += weight
        if cumulative >= half:
            # Exactly on a boundary with more samples to come: the
            # midpoint between this value and the next is the honest
            # answer (the two-equal-votes case).
            if abs(cumulative - half) < 1e-12 and i + 1 < len(pairs):
                return (value + pairs[i + 1][0]) / 2.0
            if weight <= 0:
                return value
            # How far into this sample's weight the halfway point falls.
            frac = (half - previous) / weight
            if i + 1 < len(pairs) and frac > 0.5:
                nxt = pairs[i + 1][0]
                return value + (nxt - value) * (frac - 0.5) * 2.0 * 0.5
            return value
    return pairs[-1][0]


class Vote:
    __slots__ = ("pan", "tilt", "at")

    def __init__(self, pan, tilt, at):
        self.pan = pan
        self.tilt = tilt
        self.at = at


class VoteAccumulator:
    """Time-weighted consensus over viewers' click targets.

    Thread-safe: votes arrive on HTTP handler threads while the resolver
    runs on its own.
    """

    def __init__(self, decay_seconds=4.0, max_age_seconds=12.0,
                 dead_zone_degrees=1.5):
        # Weight halves roughly every decay_seconds * ln(2). Shorter
        # makes the camera more responsive to whoever clicked last;
        # longer makes it more consensual and more sluggish.
        self.decay = decay_seconds
        self.max_age = max_age_seconds
        self.dead_zone = dead_zone_degrees

        self._votes = {}          # voter id -> Vote
        self._lock = threading.Lock()
        self._last_resolved = None

    # -- input ---------------------------------------------------------

    def add(self, voter, pan, tilt, now=None):
        """Record a vote, replacing any previous one from this voter."""
        now = time.monotonic() if now is None else now
        with self._lock:
            self._votes[voter] = Vote(pan, tilt, now)

    def clear(self):
        with self._lock:
            self._votes.clear()
            self._last_resolved = None

    # -- output --------------------------------------------------------

    def resolve(self, now=None):
        """Return (pan, tilt) to move to, or None to stay put.

        None means either nobody has voted recently, or the consensus is
        close enough to the last resolved target that moving would be
        fidgeting rather than aiming.
        """
        now = time.monotonic() if now is None else now

        with self._lock:
            # Drop votes too old to matter. Their weight would be
            # negligible anyway; this keeps the dict from growing.
            stale = [v for v, vote in self._votes.items()
                     if now - vote.at > self.max_age]
            for v in stale:
                del self._votes[v]

            if not self._votes:
                return None

            pan_pairs = []
            tilt_pairs = []
            for vote in self._votes.values():
                w = math.exp(-(now - vote.at) / self.decay)
                if w <= 0.0:
                    continue
                pan_pairs.append((vote.pan, w))
                tilt_pairs.append((vote.tilt, w))

            pan = weighted_median(pan_pairs)
            tilt = weighted_median(tilt_pairs)
            if pan is None or tilt is None:
                return None

            if self._last_resolved is not None:
                dp = pan - self._last_resolved[0]
                dt = tilt - self._last_resolved[1]
                if math.hypot(dp, dt) < self.dead_zone:
                    return None

            self._last_resolved = (pan, tilt)
            return pan, tilt

    # -- introspection -------------------------------------------------

    def stats(self, now=None):
        """Active voter count and total weight, for viewer feedback.

        Weight is more honest than a raw count: three people who clicked
        ten seconds ago carry less than one who clicked just now, and
        the display should reflect that.
        """
        now = time.monotonic() if now is None else now
        with self._lock:
            voters = 0
            weight = 0.0
            for vote in self._votes.values():
                age = now - vote.at
                if age > self.max_age:
                    continue
                voters += 1
                weight += math.exp(-age / self.decay)
            return {"voters": voters, "weight": round(weight, 2)}
