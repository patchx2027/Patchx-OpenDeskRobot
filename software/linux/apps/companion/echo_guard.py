"""Bounded textual echo suspicion, never acoustic proof or an automatic veto.

The caller supplies only played assistant text and checks during an acoustic
playback/tail overlap. Genuine user repetitions require explicit confirmation.
"""
from collections import deque
import unicodedata


class EchoGuard:
    TTL = 35.0
    MAX_TEXT = 2048
    MAX_CANDIDATE = 512

    def __init__(self):
        self._recent = deque(maxlen=3)

    @staticmethod
    def _normalize(text):
        # Keep letters/numbers (including Han), discard punctuation and spacing.
        return "".join(c for c in unicodedata.normalize("NFKC", text).casefold()
                       if c.isalnum())

    def _expire(self, now):
        self._recent = deque(((stamp, text) for stamp, text in self._recent
                              if 0 <= now - stamp <= self.TTL), maxlen=3)

    def remember(self, text, now):
        if not isinstance(text, str):
            raise TypeError("Echo reference must be text")
        self._expire(now)
        # Bound input before normalization as well as stored output.
        normalized = self._normalize(text[:self.MAX_TEXT])[:self.MAX_TEXT]
        if normalized:
            self._recent = deque(((stamp, old) for stamp, old in self._recent
                                  if old != normalized), maxlen=3)
            self._recent.append((now, normalized))

    def clear(self):
        self._recent.clear()

    @staticmethod
    def _near_substring(candidate, reference, budget):
        # Semi-global Levenshtein: free reference prefix/suffix, at most two
        # substitutions/insertions/deletions inside the matching span. Runtime
        # is bounded by 512 * 2048 * 3 cells; storage by one reference row.
        previous = [0] * (len(reference) + 1)
        ceiling = budget + 1
        for i, char in enumerate(candidate, 1):
            current = [min(i, ceiling)]
            for j, other in enumerate(reference, 1):
                current.append(min(ceiling, previous[j] + 1,
                                   current[-1] + 1, previous[j - 1] + (char != other)))
            if min(current) > budget:
                return False
            previous = current
        return min(previous) <= budget

    def suspected(self, text, now):
        if not isinstance(text, str):
            raise TypeError("Echo candidate must be text")
        self._expire(now)
        # Oversized ASR results are outside this bounded heuristic. Caller must
        # retain its independent transcript validation; never truncate into a hit.
        if len(text) > self.MAX_CANDIDATE:
            return False
        candidate = self._normalize(text)
        if not candidate:
            return False
        for _, reference in self._recent:
            if candidate in reference:
                return True
        # Short fragments need exact evidence: fuzzy single-word matching has
        # too many unrelated collisions. Longer spans tolerate <= ~20% edits.
        budget = min(2, len(candidate) // 5)
        if not budget:
            return False
        return any(self._near_substring(candidate, reference, budget)
                   for _, reference in self._recent)
