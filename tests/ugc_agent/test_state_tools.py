from ugc_agent.tools.state import (
    record_media,
    save_outline,
    save_shot_design,
    save_story,
    set_deliverable,
)


def test_save_tools_describe_parameters():
    outline = save_outline.tool_call_schema.model_json_schema()
    story = save_story.tool_call_schema.model_json_schema()
    shot_design = save_shot_design.tool_call_schema.model_json_schema()
    media = record_media.tool_call_schema.model_json_schema()
    deliverable = set_deliverable.tool_call_schema.model_json_schema()

    assert "runtime" not in outline["properties"]
    assert "expected_revision" not in outline["properties"]
    assert "不要写长段剧情" in outline["properties"]["outline"]["description"]
    assert "composition skill" in story["properties"]["story"]["description"]
    assert "record_media 之前必须先写入" in shot_design["properties"]["shot_design"][
        "description"
    ]
    assert "owner_ref" in media["properties"]["asset"]["description"]
    assert "成片 media_id" in deliverable["properties"]["media_id"]["description"]

    defs = {**outline.get("$defs", {}), **media.get("$defs", {}), **shot_design.get("$defs", {})}
    assert "片种" in defs["Outline"]["properties"]["video_type"]["description"]
    assert "character:c1" in defs["MediaAsset"]["properties"]["owner_ref"]["description"]
    assert "shot_design" in defs["MediaAsset"]["properties"]["owner_ref"]["description"]
    assert "seedance-prompt" in defs["Shot"]["properties"]["prompt"]["description"]
