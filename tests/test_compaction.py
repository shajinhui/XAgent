"""
上下文压缩模块测试（中文注释）。
"""
import unittest

from context_manager.compaction import should_compact, compact_messages


class ContextCompactionTest(unittest.TestCase):
    def test_should_compact(self) -> None:
        """测试压缩触发条件。"""

        messages = [
            {"role": "system", "content": "你是一个助手"},
            {"role": "user", "content": "你好"},
        ]
        self.assertFalse(should_compact(messages, threshold_tokens=1000))

        # 长消息需要压缩。
        long_content = "a" * 1100000
        messages = [
            {"role": "system", "content": "你是一个助手"},
            {"role": "user", "content": long_content},
        ]
        self.assertTrue(should_compact(messages, threshold_tokens=256000))

    def test_compact_messages(self) -> None:
        """测试消息压缩。"""

        messages = [
            {"role": "system", "content": "你是一个助手"},
            {"role": "user", "content": "消息1"},
            {"role": "assistant", "content": "回复1"},
            {"role": "user", "content": "消息2"},
            {"role": "assistant", "content": "回复2"},
            {"role": "user", "content": "消息3"},
            {"role": "assistant", "content": "回复3"},
            {"role": "user", "content": "消息4"},
            {"role": "assistant", "content": "回复4"},
            {"role": "user", "content": "消息5"},
        ]

        compacted, summary_text = compact_messages(messages, keep_ratio=0.2)

        self.assertEqual(compacted[0]["role"], "system")
        self.assertEqual(compacted[1]["role"], "system")
        self.assertEqual(compacted[1]["content"], "[SUMMARY_PLACEHOLDER]")
        self.assertGreaterEqual(len(compacted[2:]), 1)
        self.assertTrue("消息1" in summary_text or "回复1" in summary_text)

    def test_compact_messages_minimal(self) -> None:
        """测试最少消息不压缩。"""

        messages = [
            {"role": "system", "content": "你是一个助手"},
            {"role": "user", "content": "你好"},
        ]

        compacted, summary_text = compact_messages(messages, keep_ratio=0.2)

        self.assertEqual(len(compacted), len(messages))
        self.assertEqual(summary_text, "")


if __name__ == "__main__":
    unittest.main()
