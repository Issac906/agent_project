from __future__ import annotations

import tempfile
from pathlib import Path
import unittest

from patent_memory import (
    append_patent_memory,
    bootstrap_patent_memory_from_history,
    format_patent_memory_for_prompt,
    load_patent_memory,
)


class PatentMemoryTests(unittest.TestCase):
    def test_append_and_format_compact_memory(self) -> None:
        with tempfile.TemporaryDirectory() as tmpdir:
            path = Path(tmpdir) / "memory.json"
            result = append_patent_memory(
                title="铝电解槽电流效率诊断方法",
                topic="铝电解槽运行状态",
                idea="融合电流效率、氧化铝浓度和槽况漂移进行诊断。",
                path=path,
            )

            self.assertTrue(result["saved"])
            records = load_patent_memory(path)
            self.assertEqual(len(records), 1)
            self.assertEqual(records[0]["title"], "铝电解槽电流效率诊断方法")
            prompt_text = format_patent_memory_for_prompt(records)
            self.assertIn("铝电解槽电流效率诊断方法", prompt_text)
            self.assertIn("槽况漂移", prompt_text)

    def test_duplicate_record_updates_single_entry(self) -> None:
        with tempfile.TemporaryDirectory() as tmpdir:
            path = Path(tmpdir) / "memory.json"
            for _ in range(2):
                append_patent_memory(
                    title="阳极效应预警方法",
                    topic="铝电解",
                    idea="使用槽电压趋势和运行状态进行预警。",
                    path=path,
                )

            self.assertEqual(len(load_patent_memory(path)), 1)

    def test_bootstrap_from_existing_history_records(self) -> None:
        with tempfile.TemporaryDirectory() as tmpdir:
            history_dir = Path(tmpdir) / "history"
            record_dir = history_dir / "run1"
            record_dir.mkdir(parents=True)
            (record_dir / "record.json").write_text(
                """{
  "completed_at": "2026-07-10T10:00:00",
  "title": "铝电解槽槽况诊断方法",
  "search_topic": "铝电解槽 槽况 诊断",
  "selected_candidate": {
    "title": "铝电解槽槽况诊断方法",
    "raw": "名称：铝电解槽槽况诊断方法\\n核心方案：融合槽电压、电流效率和电解质状态。"
  }
}""",
                encoding="utf-8",
            )

            records = bootstrap_patent_memory_from_history(history_dir)
            self.assertEqual(len(records), 1)
            self.assertEqual(records[0]["topic"], "铝电解槽 槽况 诊断")
            self.assertIn("槽电压", records[0]["idea"])


if __name__ == "__main__":
    unittest.main()
