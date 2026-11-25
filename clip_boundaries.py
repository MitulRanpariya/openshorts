"""Deterministic clip boundary refinement and context checks (ClipForge Phase 3).

The model proposes a range; the transcript decides where it may actually cut.
Word snapping (``clip_selection.snap_clip_to_words``) lands cuts in pauses
between words, but a clip that starts on "...and that's why he left" or stops
before the answer lands is still a bad clip. This module works one level up,
on sentences:

* ``sentences``: words grouped by terminal punctuation or a long pause.
* ``refine_range``: start on a sentence start (preferring earlier, so the setup
  is in), end on a sentence end (preferring later, so the payoff is in), and
  pull in the question when the clip opens on its answer, all inside the
  duration band.
* ``context_flags``: what still looks wrong (dangling opener, mid-sentence
  ending, ends on a question) for the reviewer and the ranking penalty.
* ``dedupe_similar``: drop a clip whose words mostly repeat a better clip's.

Pure functions over the word list ``[{'w','s','e'}]``; no I/O, no model.
"""

from __future__ import annotations

import re
import unicodedata
from typing import Optional

# Sentence enders: Latin, CJK, Devanagari danda (Hindi), Arabic/Urdu.
TERMINAL = re.compile(r"[.!?…。！？।॥۔؟]['\")\]]*$")
QUESTION = re.compile(r"[?？؟]['\")\]]*$")
PAUSE_BREAK = 0.8           # s of silence that ends a sentence without punctuation
MAX_SENTENCE_SECONDS = 25.0  # run-on transcripts: force a break
REACH = 8.0                 # how far an edge may move to find a sentence boundary
QUESTION_LOOKBACK = 12.0    # the question an answer responds to must be this close

# English openers that usually mean the thought started earlier. Only applied
# when the transcript language is English; other languages get punctuation and
# pause checks only.
DANGLING_OPENERS = frozenset({
    "and", "but", "so", "because", "which", "that", "then", "also", "or",
    "he", "she", "they", "it", "this", "these", "those", "him", "her", "them",
    "anyway", "plus", "though", "yeah", "right", "exactly",
})

FLAG_PENALTY = {
    "starts_mid_thought": 6,
    "ends_mid_sentence": 8,
    "ends_on_question": 5,
    "starts_mid_sentence": 6,
    "shortened_to_fit": 3,
}


def _clean(word: str) -> str:
    """Letters, combining marks and digits only (``\\w`` drops the vowel signs of
    Devanagari and other scripts, which turned words into consonant skeletons)."""
    return "".join(ch for ch in (word or "")
                   if ch == "'" or unicodedata.category(ch)[0] in "LMN").lower()


def _letters(token: str) -> int:
    return sum(1 for ch in token if unicodedata.category(ch)[0] in "LN")


def sentences(words: list) -> list:
    """[{start, end, i0, i1, text}] over ``words`` (indices inclusive)."""
    out = []
    if not words:
        return out
    i0 = 0
    for i, w in enumerate(words):
        text = str(w.get("w", "")).strip()
        nxt = words[i + 1] if i + 1 < len(words) else None
        gap = (float(nxt["s"]) - float(w["e"])) if nxt else 0.0
        long_run = float(w["e"]) - float(words[i0]["s"]) >= MAX_SENTENCE_SECONDS
        if nxt is None or TERMINAL.search(text) or gap >= PAUSE_BREAK or long_run:
            out.append({
                "start": float(words[i0]["s"]),
                "end": float(w["e"]),
                "i0": i0, "i1": i,
                "text": " ".join(str(x.get("w", "")).strip() for x in words[i0:i + 1]),
            })
            i0 = i + 1
    return out


EDGE = 0.05   # s; a time this close to a boundary counts as on it


def _start_index(sents, t):
    """The sentence a clip starting at ``t`` opens with: the one being spoken at ``t``,
    or the next one when ``t`` falls in a pause."""
    for k, s in enumerate(sents):
        if s["end"] > t + EDGE:
            return k
    return len(sents) - 1


def _end_index(sents, t):
    """The sentence a clip ending at ``t`` closes with: the one being spoken at ``t``,
    or the previous one when ``t`` falls in a pause. A time in the gap after a sentence
    (where the model is told to end, and where "the next sentence's start" lands) must
    never pull the following sentence in."""
    for k in range(len(sents) - 1, -1, -1):
        if sents[k]["start"] < t - EDGE:
            return k
    return 0


def refine_range(start: float, end: float, sents: list, min_d: float, max_d: float,
                 reach: float = REACH) -> tuple:
    """(start, end) moved onto sentence boundaries inside [min_d, max_d].

    Returns the input when no boundary pair satisfies the band, so the caller
    can still fall back to word snapping.
    """
    if not sents or end <= start:
        return start, end
    if end - start > max_d:
        # The model ran past the longest clip allowed. The hook is at the start, so keep
        # it and end on the last whole sentence that fits; cutting at max_d exactly
        # stopped these clips in the middle of a sentence.
        k = _start_index(sents, start)
        new_s = sents[k]["start"] if abs(sents[k]["start"] - start) <= reach else start
        for ke in range(_end_index(sents, new_s + max_d), -1, -1):
            length = sents[ke]["end"] - new_s
            if length <= max_d:
                if length >= max(min_d, 0.5 * max_d):
                    return round(new_s, 3), round(sents[ke]["end"], 3)
                break
        return start, end
    k_s = _start_index(sents, start)
    # Start: the containing sentence's start; if that is too far back, the next.
    s_opts = [k for k in (k_s, k_s + 1) if k < len(sents)
              and abs(sents[k]["start"] - start) <= reach]
    k_e = _end_index(sents, end)
    # End: the containing sentence's end; if that is too far on, the previous.
    e_opts = [k for k in (k_e, k_e - 1) if k >= 0
              and abs(sents[k]["end"] - end) <= reach]

    for ks in s_opts:                    # prefer the earlier start (setup in)
        for ke in e_opts:                # prefer the later end (payoff in)
            if ke < ks:
                continue
            new_s, new_e = sents[ks]["start"], sents[ke]["end"]
            if min_d <= new_e - new_s <= max_d:
                # An answer needs its question: include it when it fits.
                if ks > 0 and QUESTION.search(sents[ks - 1]["text"]) \
                        and new_s - sents[ks - 1]["start"] <= QUESTION_LOOKBACK \
                        and new_e - sents[ks - 1]["start"] <= max_d:
                    new_s = sents[ks - 1]["start"]
                return round(new_s, 3), round(new_e, 3)
    return start, end


def context_flags(start: float, end: float, words: list, sents: list,
                  language: str = "en") -> list:
    """Problems a reviewer should see before rendering."""
    inside = [w for w in words if float(w["s"]) >= start - 0.05 and float(w["e"]) <= end + 0.05]
    if not inside:
        return []
    flags = []
    first, last = inside[0], inside[-1]
    if (language or "").lower().startswith("en") and _clean(first.get("w")) in DANGLING_OPENERS:
        flags.append("starts_mid_thought")
    k = _start_index(sents, float(first["s"]))
    if sents and float(first["s"]) - sents[k]["start"] > 0.3:
        flags.append("starts_mid_sentence")
    last_text = str(last.get("w", "")).strip()
    if QUESTION.search(last_text):
        flags.append("ends_on_question")
    elif not TERMINAL.search(last_text):
        k = _end_index(sents, float(last["e"]))
        if sents and sents[k]["end"] - float(last["e"]) > 0.3:
            flags.append("ends_mid_sentence")
    return flags


def penalty(flags: list) -> int:
    return sum(FLAG_PENALTY.get(f, 0) for f in flags)


def _tokens(start, end, words):
    return {_clean(w.get("w")) for w in words
            if float(w["s"]) >= start and float(w["e"]) <= end
            and _letters(_clean(w.get("w"))) > 3}


def dedupe_similar(shorts: list, words: list, threshold: float = 0.55) -> list:
    """Drop clips whose content words mostly repeat a better-scored clip.

    Temporal overlap is handled by ``dedupe_overlapping``; this catches the
    same anecdote told twice at different times. Order is preserved.
    """
    def score(c):
        try:
            return float(c.get("predicted_score") or 0)
        except (TypeError, ValueError):
            return 0.0

    ranked = sorted(range(len(shorts)), key=lambda i: (-score(shorts[i]), i))
    kept, kept_tokens = [], []
    for i in ranked:
        c = shorts[i]
        toks = _tokens(float(c["start"]), float(c["end"]), words)
        dup = False
        for other in kept_tokens:
            if toks and other:
                overlap = len(toks & other) / max(1, min(len(toks), len(other)))
                if overlap >= threshold:
                    dup = True
                    break
        if not dup:
            kept.append(i)
            kept_tokens.append(toks)
    return [shorts[i] for i in sorted(kept)]


def valid_proposal(clip: dict, duration: float, window_ids: Optional[set] = None) -> bool:
    """Reject model output that cannot be a clip (schema-valid but nonsense)."""
    try:
        s, e = float(clip.get("start")), float(clip.get("end"))
    except (TypeError, ValueError):
        return False
    if not (0 <= s < e <= duration + 1.0):
        return False
    if window_ids is not None and _window_number(clip.get("source_window_id")) not in \
            {_window_number(w) for w in window_ids}:
        return False
    return True


def _window_number(window_id):
    """``window_007``, ``window_7`` and ``7`` are the same window: small local models
    rarely copy the id character for character."""
    digits = re.findall(r"\d+", str(window_id if window_id is not None else ""))
    return int(digits[-1]) if digits else str(window_id)


def refine_all(shorts: list, words: list, duration: float, min_d: float, max_d: float,
               language: str = "en") -> list:
    """Sentence-refine every clip in place and attach ``context_flags``.

    The raw model range stays in ``proposed`` (set by the caller). A clip's
    ``predicted_score`` is lowered by the flag penalty and the original kept as
    ``predicted_score_model``, so ranking prefers clips that stand alone.
    """
    sents = sentences(words)
    for c in shorts:
        too_long = float(c["end"]) - float(c["start"]) > max_d + 0.5
        s, e = refine_range(float(c["start"]), float(c["end"]), sents, min_d, max_d)
        flags = context_flags(s, e, words, sents, language)
        if too_long:
            # The model wanted more than fits: the payoff may be past the new end.
            flags.append("shortened_to_fit")
        if "starts_mid_thought" in flags:
            # "And then he quit." needs the sentence before it: pull the start
            # back one sentence when that still fits the band.
            k = _start_index(sents, s)
            if k > 0 and e - sents[k - 1]["start"] <= max_d \
                    and s - sents[k - 1]["start"] <= 2 * REACH:
                s = round(sents[k - 1]["start"], 3)
                flags = context_flags(s, e, words, sents, language) + \
                    (["shortened_to_fit"] if too_long else [])
        c["start"], c["end"] = s, e
        c["context_flags"] = flags
        if flags:
            try:
                raw = int(c.get("predicted_score") or 0)
            except (TypeError, ValueError):
                raw = 0
            c.setdefault("predicted_score_model", raw)
            c["predicted_score"] = max(0, raw - penalty(flags))
    return shorts
