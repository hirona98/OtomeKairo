import unittest

from otomekairo.defaults import build_default_state
from otomekairo.service.common import ServiceError
from otomekairo.service.config.mixin import ServiceConfigMixin
from otomekairo.service.docs import ServiceDocsMixin


class DummyStore:
    def __init__(self) -> None:
        self.state = build_default_state()
        self.state["console_access_token"] = "token"

    def read_state(self) -> dict:
        return dict(self.state)


class DummyService(ServiceDocsMixin, ServiceConfigMixin):
    def __init__(self) -> None:
        self.store = DummyStore()


class DocsApiTests(unittest.TestCase):
    def test_docs_requires_token(self) -> None:
        service = DummyService()

        with self.assertRaises(ServiceError) as raised:
            service.get_docs(None)

        self.assertEqual(raised.exception.error_code, "invalid_token")

    def test_docs_returns_selected_console_sections(self) -> None:
        service = DummyService()

        response = service.get_docs("token")

        self.assertEqual(response["document_set_id"], "console_docs")
        self.assertEqual(response["format"], "plain_text")
        self.assertEqual([section["section_id"] for section in response["sections"]], ["conversation", "wake"])
        self.assertIn("POST {BASE_URL}/api/conversation", response["sections"][0]["body_text"])
        self.assertIn("POST {BASE_URL}/api/wake", response["sections"][1]["body_text"])
        self.assertNotIn("body_markdown", response["sections"][0])
        joined = "\n".join(section["body_text"] for section in response["sections"])
        self.assertNotIn("tok_", joined)
        self.assertNotIn("API_KEY", joined)


if __name__ == "__main__":
    unittest.main()
