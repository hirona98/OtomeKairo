import unittest

from otomekairo.skills import (
    ELYTH_REMOTE_BUNDLE_ID,
    ELYTH_REMOTE_UPSTREAM_COMMIT,
    SkillBundleError,
    load_skill_bundle,
)


class SkillBundleTests(unittest.TestCase):
    def test_vendored_elyth_bundle_is_pinned_and_complete(self) -> None:
        bundle = load_skill_bundle(ELYTH_REMOTE_BUNDLE_ID)

        self.assertEqual(bundle.version, "0.1.0")
        self.assertEqual(bundle.upstream_commit, ELYTH_REMOTE_UPSTREAM_COMMIT)
        self.assertEqual(bundle.root_skill, "elyth")
        self.assertEqual(bundle.session_skill, "elyth-run-session")
        self.assertEqual(set(bundle.tool_skills), set(bundle.declared_tools))
        self.assertEqual(bundle.tool_skills["create_post"], "elyth-post")
        self.assertIn("create_post", bundle.mutating_tools)
        self.assertNotIn("get_information", bundle.mutating_tools)

    def test_unknown_bundle_fails_explicitly(self) -> None:
        with self.assertRaises(SkillBundleError):
            load_skill_bundle("unknown@1.0.0")


if __name__ == "__main__":
    unittest.main()
