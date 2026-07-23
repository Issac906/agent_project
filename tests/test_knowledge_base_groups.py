from __future__ import annotations

import tempfile
from pathlib import Path
import unittest
from unittest.mock import patch

import knowledge_base_groups as groups


class KnowledgeBaseIsolationTests(unittest.TestCase):
    def test_physical_knowledge_base_persists_its_own_instance(self) -> None:
        with tempfile.TemporaryDirectory() as directory, patch.object(
            groups, "CATALOG_PATH", Path(directory) / "knowledge_base_catalog.json"
        ):
            target = groups.create_knowledge_base(
                "铝电解知识库",
                "铝电解独立素材",
                "http://server:9622/webui/#/",
            )

            self.assertEqual("http://server:9622", target["base_url"])
            self.assertEqual(
                "http://server:9622/webui/?tab=knowledge-graph#/",
                target["graph_url"],
            )
            self.assertEqual("physical", target["isolation"])
            self.assertTrue(groups.require_knowledge_base(target["id"])["selectable"])

    def test_legacy_virtual_group_requires_instance_migration(self) -> None:
        with tempfile.TemporaryDirectory() as directory, patch.object(
            groups, "CATALOG_PATH", Path(directory) / "knowledge_base_catalog.json"
        ):
            groups.CATALOG_PATH.write_text(
                '{"version":1,"initialized":true,"knowledge_bases":[{"id":"legacy","name":"旧知识库"}]}',
                encoding="utf-8",
            )

            catalog = groups.list_knowledge_base_catalog()

            self.assertEqual("migration_required", catalog[0]["isolation"])
            self.assertFalse(catalog[0]["selectable"])
            with self.assertRaisesRegex(ValueError, "尚未绑定"):
                groups.require_knowledge_base("legacy")

    def test_one_instance_cannot_be_bound_to_two_knowledge_bases(self) -> None:
        with tempfile.TemporaryDirectory() as directory, patch.object(
            groups, "CATALOG_PATH", Path(directory) / "knowledge_base_catalog.json"
        ):
            groups.create_knowledge_base("知识库一", base_url="http://server:9622")
            with self.assertRaisesRegex(ValueError, "已经绑定"):
                groups.create_knowledge_base("知识库二", base_url="http://server:9622/webui/#/")


if __name__ == "__main__":
    unittest.main()
