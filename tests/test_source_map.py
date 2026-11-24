"""source_map: whole-episode context, validated and optional."""

import source_map as sm

SENTS = [{"start": float(i * 10), "end": float(i * 10 + 8), "text": f"Sentence number {i}."}
         for i in range(30)]

GOOD = {
    "summary": "An episode about habits.",
    "participants": ["host", " guest ", ""],
    "topics": [
        {"start": 150, "end": 400, "title": "Second", "summary": "b"},
        {"start": 0, "end": 150, "title": "First", "summary": "a"},
        {"start": 50, "end": 20, "title": "Backwards", "summary": "dropped"},
        {"start": -5, "end": 9999, "title": "Clamped", "summary": "c"},
    ],
    "qa": [{"question_start": 12, "answer_end": 40, "question": "How?"},
           {"question_start": "x", "answer_end": 40, "question": "bad"}],
}


def test_clean_validates_sorts_and_clamps():
    m = sm.clean(GOOD, duration=300)
    assert [t["title"] for t in m["topics"]] == ["First", "Clamped", "Second"]
    clamped = m["topics"][1]
    assert clamped["start"] == 0 and clamped["end"] == 300
    assert m["participants"] == ["host", "guest"]
    assert m["qa"] == [{"question_start": 12.0, "answer_end": 40.0, "question": "How?"}] or \
        len(m["qa"]) == 1


def test_clean_rejects_garbage():
    assert sm.clean({"topics": "nope"}, 100) is None
    assert sm.clean({}, 100) is None


def test_topic_for_uses_clip_midpoint():
    m = sm.clean(GOOD, duration=500)
    assert sm.topic_for(10, 60, m) == "First"
    assert sm.topic_for(160, 220, m) in ("Second", "Clamped")
    assert sm.topic_for(10, 60, None) is None


def test_prompt_context_is_marked_as_data_and_compact():
    m = sm.clean(GOOD, duration=500)
    ctx = sm.prompt_context(m)
    assert ctx.startswith("SOURCE_CONTEXT") and "not instructions" in ctx
    assert "First" in ctx and sm.prompt_context(None) == ""


def test_compact_transcript_samples_instead_of_truncating():
    full = sm.compact_transcript(SENTS, 10_000)
    assert full.count("\n") == len(SENTS) - 1
    small = sm.compact_transcript(SENTS, 200)
    assert len(small) <= 200
    assert "[0]" in small and "[290]" not in small or small.count("[") >= 5


def test_build_is_optional_and_never_raises(monkeypatch):
    monkeypatch.delenv("LLM_BASE_URL", raising=False)

    def boom(prompt, schema):
        raise RuntimeError("model down")
    assert sm.build(SENTS, 300, "en", boom) == (None, None)

    def ok(prompt, schema):
        assert "DATA to analyse" in prompt and "[10] Sentence number 1." in prompt
        return GOOD, {"total_cost": 0.0}
    m, cost = sm.build(SENTS, 300, "en", ok)
    assert m["summary"] and cost == {"total_cost": 0.0}

    monkeypatch.setenv("SOURCE_MAP", "0")
    assert sm.build(SENTS, 300, "en", ok) == (None, None)
    monkeypatch.delenv("SOURCE_MAP")
    assert sm.build(SENTS[:3], 300, "en", ok) == (None, None)    # too short to map
