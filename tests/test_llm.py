from types import SimpleNamespace

from pydantic import BaseModel, ConfigDict

from ugc_harness.shared.llm import StructuredLLM
from ugc_harness.shared.settings import LLMSettings


class ExampleOutput(BaseModel):
    model_config = ConfigDict(extra="forbid")

    name: str
    count: int


class FakeCompletions:
    def __init__(self, responses: list[str]) -> None:
        self.responses = responses
        self.requests: list[dict] = []

    def create(self, **kwargs):
        self.requests.append(kwargs)
        content = self.responses[len(self.requests) - 1]
        return SimpleNamespace(
            choices=[SimpleNamespace(message=SimpleNamespace(content=content))]
        )


def _llm(responses: list[str]) -> tuple[StructuredLLM, FakeCompletions]:
    llm = StructuredLLM(
        LLMSettings(
            api_key="test-key-value",
            base_url="https://example.invalid/v1",
            model="test-model",
        )
    )
    completions = FakeCompletions(responses)
    llm.client = SimpleNamespace(
        chat=SimpleNamespace(completions=completions)
    )
    return llm, completions


def test_generate_sends_model_json_schema_in_first_request() -> None:
    llm, completions = _llm(['{"name":"demo","count":2}'])

    result = llm.generate("create output", ExampleOutput)

    assert result == ExampleOutput(name="demo", count=2)
    prompt = completions.requests[0]["messages"][1]["content"]
    assert "ExampleOutput" in prompt
    assert '"additionalProperties":false' in prompt
    assert '"required":["name","count"]' in prompt


def test_generate_keeps_schema_in_validation_repair_request() -> None:
    llm, completions = _llm(
        [
            '{"name":"demo","amount":2}',
            '{"name":"demo","count":2}',
        ]
    )

    result = llm.generate("create output", ExampleOutput)

    assert result.count == 2
    repair = completions.requests[1]["messages"][1]["content"]
    assert "你上一次的输出未通过结构校验" in repair
    assert '"additionalProperties":false' in repair
    assert '"required":["name","count"]' in repair
