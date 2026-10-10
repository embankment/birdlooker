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

Consensus is a weighted mean, with outlier rejection once there are
enough voters to tell an outlier from a disagreement.

Getting here took two wrong turns. A plain mean lets one person
clicking the far corner drag everybody: three viewers on a bird at -10
plus one at +50 averages to +5, pointing at nothing anyone wanted. So
the first fix was a weighted median -- which is robust with many voters
and degenerate with few. A median over two samples lands wherever
cumulative weight crosses half, so whichever vote is marginally fresher
takes the whole thing. With real clicks, never simultaneous, that is
"last tap wins" with extra steps.

So: mean, which blends the way people expect, plus outlier rejection
that only engages at OUTLIER_MIN_VOTERS or more. Below that there is no
honest way to tell a troll from someone who simply wants to look
somewhere else, and pretending otherwise just makes two viewers fight.
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
                 dead_zone_degrees=1.5,
                 outlier_min_voters=4, outlier_gate_degrees=20.0):
        # Weight halves roughly every decay_seconds * ln(2). Shorter
        # makes the camera more responsive to whoever clicked last;
        # longer makes it more consensual and more sluggish.
        self.decay = decay_seconds
        self.max_age = max_age_seconds
        self.dead_zone = dead_zone_degrees

        # Outlier rejection needs enough votes to distinguish "one troll"
        # from "two people who disagree". Below this many voters, every
        # vote counts.
        self.outlier_min_voters = outlier_min_voters
        self.outlier_gate = outlier_gate_degrees

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

            weighted = []
            for vote in self._votes.values():
                w = math.exp(-(now - vote.at) / self.decay)
                if w > 0.0:
                    weighted.append((vote.pan, vote.tilt, w))

            if not weighted:
                return None

            kept = self._reject_outliers(weighted)
            total = sum(w for _, _, w in kept)
            if total <= 0.0:
                return None

            pan = sum(p * w for p, _, w in kept) / total
            tilt = sum(t * w for _, t, w in kept) / total

            if self._last_resolved is not None:
                dp = pan - self._last_resolved[0]
                dt = tilt - self._last_resolved[1]
                if math.hypot(dp, dt) < self.dead_zone:
                    return None

            self._last_resolved = (pan, tilt)
            return pan, tilt

    def _reject_outliers(self, weighted):
        """Drop votes far from the crowd, once there is a crowd.

        The reference point is the weighted median, which is the whole
        reason the median is still here: it is a robust centre even when
        an outlier is present, unlike the mean it is protecting.

        Rejection requires a strict majority of VOTERS, counted by head
        rather than by weight. Weight decides where to aim; it must not
        decide whose vote is legitimate. Weighing the majority test
        meant an even two-against-two split resolved to whichever pair
        had clicked marginally more recently -- their decay advantage
        pushed them past half the weight, and the other pair was
        discarded as outliers. By head count 2 is not a majority of 4,
        so that split averages, while 2 of 3 and 3 of 4 still reject.
        """
        if len(weighted) < self.outlier_min_voters:
            return weighted

        med_pan = weighted_median([(p, w) for p, _, w in weighted])
        med_tilt = weighted_median([(t, w) for _, t, w in weighted])
        if med_pan is None or med_tilt is None:
            return weighted

        scored = []
        for pan, tilt, w in weighted:
            distance = math.hypot(pan - med_pan, tilt - med_tilt)
            scored.append((distance, pan, tilt, w))

        kept = [(p, t, w) for d, p, t, w in scored if d <= self.outlier_gate]

        # Strict majority by head count: 2 of 3 and 3 of 4 reject, 2 of
        # 4 does not.
        if len(kept) * 2 <= len(scored):
            return weighted
        return kept

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
