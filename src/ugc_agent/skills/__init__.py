from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path

from langchain.agents.middleware import AgentMiddleware, ModelRequest, ModelResponse
from langchain.messages import SystemMessage
from langchain.tools import tool

SKILLS_ROOT = Path(__file__).resolve().parent


@dataclass(frozen=True)
class Skill:
    name: str
    description: str
    body: str


def parse_skill(path: Path) -> Skill:
    text = path.read_text(encoding="utf-8")
    _, frontmatter, _body = text.split("---", 2)
    meta: dict[str, str] = {}
    for line in frontmatter.strip().splitlines():
        key, value = line.split(":", 1)
        meta[key.strip()] = value.strip()
    return Skill(name=meta["name"], description=meta["description"], body=text)


def discover_skills(root: Path = SKILLS_ROOT) -> dict[str, Skill]:
    skills: dict[str, Skill] = {}
    for kind in ("composition", "foundation"):
        folder = root / kind
        if not folder.is_dir():
            continue
        for skill_dir in sorted(folder.iterdir()):
            path = skill_dir / "SKILL.md"
            if path.is_file():
                skill = parse_skill(path)
                skills[skill.name] = skill
    return skills


def make_load_skill(skills: dict[str, Skill]):
    @tool
    def load_skill(name: str) -> str:
        """Load full skill instructions by name from the skill catalog."""
        skill = skills.get(name)
        if skill is None:
            return f"unknown skill: {name}"
        return skill.body

    return load_skill


class SkillsMiddleware(AgentMiddleware):
    def __init__(self, skills: dict[str, Skill] | None = None) -> None:
        self.skills = skills if skills is not None else discover_skills()
        self.tools = [make_load_skill(self.skills)]

    def catalog_text(self) -> str:
        lines = [
            "## Skills",
            "需要全文时调用 load_skill(name)。这里只有目录：",
        ]
        for skill in self.skills.values():
            lines.append(f"- {skill.name}: {skill.description}")
        return "\n".join(lines)

    def wrap_model_call(self, request: ModelRequest, handler) -> ModelResponse:
        base = request.system_message.content if request.system_message else ""
        system = SystemMessage(content=f"{base}\n\n{self.catalog_text()}")
        return handler(request.override(system_message=system))
