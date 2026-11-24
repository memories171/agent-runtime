from pathlib import Path

from agent_runtime.skills.registry import SkillRegistry


def test_skill_registry(tmp_path: Path):
    folder = tmp_path / "demo"
    folder.mkdir()
    (folder / "SKILL.md").write_text("---\nname: demo\ndescription: Example\n---\nDo work.", encoding="utf-8")
    registry = SkillRegistry(tmp_path)
    assert registry.list_skills()[0]["skill_id"] == "demo"
    assert registry.load_skill("demo")["instructions"].strip() == "Do work."
    assert registry.load_skill("../outside") is None
