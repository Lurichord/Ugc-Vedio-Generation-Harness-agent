from ugc_agent.skills import SkillsMiddleware, discover_skills, make_load_skill


def test_composition_catalog_lists_four_skills_without_bodies():
    skills = discover_skills()
    middleware = SkillsMiddleware(skills)
    catalog = middleware.catalog_text()

    assert set(skills) == {
        "avatar-explainer",
        "drama",
        "ad",
        "explainer",
        "seedance-prompt",
    }
    for name, skill in skills.items():
        assert f"- {name}: {skill.description}" in catalog
        assert skill.body not in catalog
    assert "先问清片子类型" not in catalog
    assert "fl2va / ref2va" in skills["seedance-prompt"].description


def test_load_skill_returns_full_body_or_unknown():
    skills = discover_skills()
    load_skill = make_load_skill(skills)

    drama = load_skill.invoke({"name": "drama"})
    assert drama.startswith("---")
    assert "name: drama" in drama
    assert "先问清片子类型" in drama
    seedance = load_skill.invoke({"name": "seedance-prompt"})
    assert "name: seedance-prompt" in seedance
    assert "[图1]" in seedance
    assert "@图片1" not in seedance
    assert "运镜语言参考" in seedance
    assert "提示词模版库" in seedance
    assert "视频延长" in seedance
    assert "音乐卡点" in seedance
    assert load_skill.invoke({"name": "missing"}) == "unknown skill: missing"


def test_composition_skills_keep_distinct_workflows():
    skills = discover_skills()
    avatar = skills["avatar-explainer"].body
    drama = skills["drama"].body
    ad = skills["ad"].body
    explainer = skills["explainer"].body

    assert "未完成整套分段口播，不得进入视频生成" in avatar
    assert "数字人口播讲解人" in avatar
    assert "禁止用 `split-audio` 从上一段视频抽音色来驱动下一段" in avatar
    assert "fl2va" in avatar and "ref2va" in avatar
    assert "拿本镜分镜里的旧 prompt 来微调" in avatar
    assert "用微调后的 prompt" in avatar

    assert "第 n+1 段必须使用第 n 段拆出的音频作参考" in drama
    assert "四宫格剧情图" not in drama
    assert "分段四宫格" not in drama
    assert "未完成整套分段口播" not in drama
    assert "fl2va" in drama and "ref2va" in drama
    assert "拿本镜分镜里的旧 prompt 来微调" in drama
    assert "用微调后的 prompt" in drama
    assert "切分镜头" in drama
    assert "不要抽上一镜尾帧" in drama

    assert "卖点" in ad
    assert "未完成整套分段旁白，不得进入视频生成" in ad
    assert "不要用 `split-audio` 从上一段成片抽音频来当本段旁白" in ad
    assert "fl2va" in ad and "ref2va" in ad
    assert "拿本镜分镜里的旧 prompt 来微调" in ad
    assert "用微调后的 prompt" in ad

    assert "四宫格教学帧" in explainer
    assert "未完成整套分段口播，不得进入视频生成" in explainer
    assert "数字人口播讲解人" not in explainer
    assert "改用 `avatar-explainer`" in explainer
    assert "fl2va" in explainer and "ref2va" in explainer
    assert "拿本镜分镜里的旧 prompt 来微调" in explainer
    assert "用微调后的 prompt" in explainer

    for body in (avatar, drama, ad, explainer):
        assert "load_skill seedance-prompt" in body
        assert "缺项写「无」" not in body
        assert "上一镜尾帧放进 `reference_paths`" in body
        assert "作为首帧" in body
