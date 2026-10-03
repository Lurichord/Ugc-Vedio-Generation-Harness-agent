import pytest

from ugc_harness.agents.narrative_agent.brief import make_brief
from ugc_harness.agents.voice_agent.capabilities import VoiceCapabilities
from ugc_harness.agents.voice_agent.voice_select import (
    LlmVoicePicker,
    VoiceCatalogEntry,
    VoiceChoice,
    filter_catalog,
    load_catalog,
    select_voice,
)
from ugc_harness.content import ArollCharacter, ArollVoiceProfile


def test_catalog_is_seed_tts_2_explainer_voices() -> None:
    catalog = load_catalog()

    assert catalog
    assert all(item.voice_id.endswith("_uranus_bigtts") for item in catalog)
    assert any(
        item.voice_id == "zh_male_qingshuangnanda_uranus_bigtts" for item in catalog
    )
    assert any(
        item.voice_id == "zh_female_shuangkuaisisi_uranus_bigtts" for item in catalog
    )


def test_filter_catalog_keeps_female_young_chinese_voices() -> None:
    catalog = load_catalog()

    shortlist = filter_catalog(
        catalog, language="zh-CN", gender="female", age_style="young"
    )

    assert shortlist
    assert all(item.gender == "female" for item in shortlist)
    assert all(item.age_style == "young" for item in shortlist)
    assert all(item.language.startswith("zh") for item in shortlist)


def test_filter_catalog_language_selects_english_voices() -> None:
    catalog = load_catalog()

    shortlist = filter_catalog(catalog, language="en-US")

    assert shortlist
    assert all(item.language.startswith("en") for item in shortlist)


def test_select_voice_uses_persona_when_no_on_camera_host() -> None:
    brief = make_brief(topic="为什么要写测试", duration_seconds=90)
    seen: list[list[VoiceCatalogEntry]] = []

    class Picker:
        def pick(self, *, brief, character, candidates, instruction=None):
            del brief
            del instruction
            seen.append(list(candidates))
            assert character is None
            return candidates[0].voice_id

    chosen = select_voice(brief, None, picker=Picker())

    assert chosen == seen[0][0].voice_id
    assert {item.gender for item in seen[0]} == {"female", "male"}


def test_select_voice_override_skips_picker() -> None:
    brief = make_brief(topic="为什么要写测试", duration_seconds=90)

    class Boom:
        def pick(self, *, brief, character, candidates, instruction=None):
            raise AssertionError("override should not call picker")

    chosen = select_voice(
        brief,
        None,
        picker=Boom(),
        override_voice_id="forced-voice",
    )

    assert chosen == "forced-voice"


def test_voice_capability_wraps_catalog_selection() -> None:
    brief = make_brief(topic="为什么要写测试", duration_seconds=90)

    class Picker:
        def pick(self, *, brief, character, candidates, instruction=None):
            del brief, character, instruction
            return candidates[0].voice_id

    class Provider:
        def synthesize(self, **kwargs):
            raise AssertionError("select should not synthesize")

    choice = VoiceCapabilities(Provider()).select_narration_voice(
        brief, None, picker=Picker()
    )

    assert choice.voice_id
    assert choice.reason == "catalog"


def test_llm_picker_returns_shortlist_id() -> None:
    catalog = load_catalog()
    shortlist = filter_catalog(
        catalog, language="zh-CN", gender="female", age_style="young"
    )
    brief = make_brief(
        topic="端午节包粽子",
        duration_seconds=90,
        creator_persona="亲切的年轻女创作者",
    )
    character = ArollCharacter(
        character_id="host_main",
        visual_description="年轻女主播",
        voice_profile=ArollVoiceProfile(
            gender="female",
            age_style="young",
            tone="warm",
            pace="natural",
        ),
    )

    class Generator:
        def generate(self, prompt, output_type, *, system=None):
            del system
            assert output_type is VoiceChoice
            assert "zh_female_xiaohe_uranus_bigtts" in prompt
            return VoiceChoice(
                voice_id="zh_female_xiaohe_uranus_bigtts",
                reason="年轻女声，适合讲解",
            )

    chosen = LlmVoicePicker(Generator()).pick(
        brief=brief, character=character, candidates=shortlist
    )

    assert chosen == "zh_female_xiaohe_uranus_bigtts"


def test_llm_picker_rejects_id_outside_shortlist() -> None:
    brief = make_brief(topic="测试", duration_seconds=90)
    candidates = [
        VoiceCatalogEntry(
            name="小何 2.0",
            voice_id="zh_female_xiaohe_uranus_bigtts",
            gender="female",
            age_style="young",
            language="zh-CN",
        )
    ]

    class Generator:
        def generate(self, prompt, output_type, *, system=None):
            del prompt, output_type, system
            return VoiceChoice(voice_id="not-in-list", reason="x")

    with pytest.raises(ValueError, match="短名单"):
        LlmVoicePicker(Generator()).pick(
            brief=brief, character=None, candidates=candidates
        )


def test_select_voice_repair_instruction_does_not_keep_original_gender_filter() -> None:
    brief = make_brief(topic="为什么要写测试", duration_seconds=90)
    character = ArollCharacter(
        character_id="host_main",
        visual_description="年轻女主播",
        voice_profile=ArollVoiceProfile(
            gender="female",
            age_style="young",
            tone="warm",
            pace="natural",
        ),
    )
    seen: list[list[VoiceCatalogEntry]] = []

    class Picker:
        def pick(self, *, brief, character, candidates, instruction=None):
            del brief, character
            seen.append(list(candidates))
            assert instruction == "把说话人改成男性"
            return next(item.voice_id for item in candidates if item.gender == "male")

    chosen = select_voice(
        brief,
        character,
        picker=Picker(),
        instruction="把说话人改成男性",
    )

    assert seen
    assert {item.gender for item in seen[0]} >= {"female", "male"}
    assert any(item.voice_id == chosen and item.gender == "male" for item in seen[0])


def test_llm_picker_prompt_includes_repair_instruction() -> None:
    catalog = load_catalog()
    shortlist = filter_catalog(catalog, language="zh-CN")
    brief = make_brief(topic="测试", duration_seconds=90)

    class Generator:
        def generate(self, prompt, output_type, *, system=None):
            del output_type, system
            assert "把说话人改成男性" in prompt
            return VoiceChoice(voice_id=shortlist[0].voice_id, reason="x")

    chosen = LlmVoicePicker(Generator()).pick(
        brief=brief,
        character=None,
        candidates=shortlist,
        instruction="把说话人改成男性",
    )
    assert chosen == shortlist[0].voice_id
