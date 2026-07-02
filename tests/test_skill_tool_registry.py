from __future__ import annotations

import tempfile
import unittest
from pathlib import Path

from agent_skill_loader import load_agent_skills
from tool_registry import register_tool, registered_tools


class SkillToolRegistryTests(unittest.TestCase):
    def test_skill_loader_discovers_new_directory(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            root = Path(temp_dir)
            skill_dir = root / "skills" / "new-skill"
            skill_dir.mkdir(parents=True)
            (skill_dir / "SKILL.md").write_text(
                "---\nname: new-skill\ndescription: newly added skill\n---\n# Skill\n",
                encoding="utf-8",
            )
            skills = load_agent_skills(root)
            self.assertEqual(["new-skill"], [skill.name for skill in skills])
            self.assertEqual("newly added skill", skills[0].description)

    def test_registered_tool_is_discoverable(self) -> None:
        @register_tool("test_dynamic_tool", "test tool", "Test")
        def dynamic_tool() -> None:
            return None

        names = [tool.name for tool in registered_tools()]
        self.assertIn("test_dynamic_tool", names)


if __name__ == "__main__":
    unittest.main()
