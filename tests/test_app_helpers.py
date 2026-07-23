import unittest

from app import lightrag_graph_webui_url


class AppHelperTests(unittest.TestCase):
    def test_builds_graph_url_from_webui_url(self) -> None:
        self.assertEqual(
            "http://192.168.130.130:9621/webui/?tab=knowledge-graph#/",
            lightrag_graph_webui_url("http://192.168.130.130:9621/webui/#/"),
        )

    def test_builds_graph_url_from_server_root(self) -> None:
        self.assertEqual(
            "http://192.168.130.130:9621/webui/?tab=knowledge-graph#/",
            lightrag_graph_webui_url("http://192.168.130.130:9621"),
        )


if __name__ == "__main__":
    unittest.main()
