import tempfile
import unittest
from pathlib import Path

from kb_manager_service import LightRAGInstanceManager, ManagerConfig, create_app


class FakeContainer:
    def __init__(self, name: str) -> None:
        self.name = name
        self.status = "running"
        self.removed = False

    def reload(self) -> None:
        return None

    def remove(self, force: bool = False) -> None:
        self.removed = force


class FakeContainers:
    def __init__(self) -> None:
        self.items = {}
        self.last_options = None

    def run(self, **options):
        self.last_options = options
        container = FakeContainer(options["name"])
        self.items[container.name] = container
        return container

    def get(self, name):
        if name not in self.items:
            raise RuntimeError("not found")
        return self.items[name]


class FakeDocker:
    def __init__(self) -> None:
        self.containers = FakeContainers()

    def ping(self) -> bool:
        return True


class KnowledgeBaseManagerServiceTests(unittest.TestCase):
    def setUp(self) -> None:
        self.temp_dir = tempfile.TemporaryDirectory()
        self.config = ManagerConfig(
            api_key="manager-secret",
            image="lightrag:test",
            public_host="192.0.2.10",
            port_start=19622,
            port_end=19629,
            data_root=Path(self.temp_dir.name),
            lightrag_env_file=None,
            lightrag_api_key="rag-secret",
            docker_network=None,
            startup_timeout=30,
        )
        self.docker = FakeDocker()
        self.manager = LightRAGInstanceManager(self.config, self.docker)
        self.manager._wait_until_ready = lambda _url: None
        self.manager._port_available = lambda _port: True

    def tearDown(self) -> None:
        self.temp_dir.cleanup()

    def test_create_allocates_isolated_workspace_port_and_directories(self) -> None:
        first = self.manager.create("铝电解知识库")
        second = self.manager.create("工业智能知识库")

        self.assertNotEqual(first["workspace"], second["workspace"])
        self.assertNotEqual(first["port"], second["port"])
        self.assertTrue((Path(first["data_dir"]) / "rag_storage").is_dir())
        self.assertTrue((Path(first["data_dir"]) / "inputs").is_dir())
        self.assertEqual(first["status"], "ready")
        self.assertTrue(first["base_url"].startswith("http://192.0.2.10:"))

        options = self.docker.containers.last_options
        self.assertEqual(options["environment"]["WORKSPACE"], second["workspace"])
        self.assertEqual(options["environment"]["WORKING_DIR"], "/app/data/rag_storage")
        self.assertEqual(options["labels"]["com.patent-agent.managed"], "true")

    def test_management_api_requires_token_and_can_delete(self) -> None:
        service = create_app(self.config, self.manager)
        client = service.test_client()
        unauthorized = client.get("/knowledge-bases")
        self.assertEqual(unauthorized.status_code, 401)

        headers = {"Authorization": "Bearer manager-secret"}
        created = client.post(
            "/knowledge-bases",
            headers=headers,
            json={"name": "测试知识库"},
        )
        self.assertEqual(created.status_code, 201)
        instance_id = created.get_json()["id"]

        rejected = client.delete(
            f"/knowledge-bases/{instance_id}", headers=headers, json={"confirm": False}
        )
        self.assertEqual(rejected.status_code, 400)
        deleted = client.delete(
            f"/knowledge-bases/{instance_id}", headers=headers, json={"confirm": True}
        )
        self.assertEqual(deleted.status_code, 200)
        self.assertTrue(deleted.get_json()["data_retained_at"])


if __name__ == "__main__":
    unittest.main()
