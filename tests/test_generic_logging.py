from io import StringIO

from ugc_harness.agents.generic import _log, _log_tool
from ugc_harness.intake.agent import _log_tool as _intake_log_tool
from ugc_harness.tools.mcp import forward_child_stderr, intake_stdio_server_config


def test_generic_log_writes_agent_tool_log(monkeypatch, tmp_path) -> None:
    path = tmp_path / "agent-tools.log"
    monkeypatch.setenv("AGENT_TOOL_LOG", str(path))
    _log("[narrative_agent] call repair.submit_candidate")
    text = path.read_text(encoding="utf-8")
    assert "[narrative_agent] call repair.submit_candidate" in text


def test_generic_log_tool_includes_full_arguments(capsys, monkeypatch) -> None:
    monkeypatch.delenv("AGENT_TOOL_LOG", raising=False)
    monkeypatch.delenv("INTAKE_TOOL_LOG", raising=False)
    arguments = {
        "patches": [
            {"ref": "shot:shot_act_2", "value": {"purpose": "x" * 50}},
            {"ref": "shot:shot_act_2-value:payload:dialogue_lines", "value": {}},
        ]
    }
    _log_tool("narrative_agent", "call", "repair.submit_candidate", arguments=arguments)
    err = capsys.readouterr().err
    assert "shot:shot_act_2-value:payload:dialogue_lines" in err
    assert '"purpose":"' in err


def test_intake_log_tool_includes_empty_arguments(capsys, monkeypatch) -> None:
    monkeypatch.delenv("INTAKE_TOOL_LOG", raising=False)
    _intake_log_tool("call", "harness.list_graph", {})
    err = capsys.readouterr().err
    assert "[intake] call harness.list_graph {}" in err


def test_forward_child_stderr_copies_lines_to_parent() -> None:
    dest = StringIO()
    with forward_child_stderr(dest) as errlog:
        errlog.write("[narrative_agent] start task=t1 format=drama\n")
        errlog.flush()
    assert "[narrative_agent] start task=t1 format=drama" in dest.getvalue()


def test_intake_mcp_child_gets_agent_tool_log_path() -> None:
    config = intake_stdio_server_config()
    assert config.env.get("AGENT_TOOL_LOG")
    assert config.env.get("PYTHONUNBUFFERED") == "1"
