"""Exercise the installed INT8 model, without recording or touching the clipboard."""

import unittest

from voice_input.punctuation import MODEL_PATH, Punctuator


@unittest.skipUnless(MODEL_PATH.is_file(), "local punctuation model is not installed")
class PunctuationModelTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.punctuator = Punctuator()

    def test_compound_question_gets_sentence_boundaries(self):
        text = "这个功能可以在本地运行吗如果可以我们今天就开始测试"
        output = self.punctuator.restore(text)
        self.assertIn("吗？", output)
        self.assertIn("，", output)
        self.assertTrue(output.endswith("。"))
        self.assertEqual(output.translate(str.maketrans("", "", "，。？、")), text)

    def test_technical_text_retains_all_original_characters_and_spaces(self):
        text = "先打开 /home/demo/app.py 再检查 v3.12 和 feature/asr 然后测试"
        output = self.punctuator.restore(text)
        self.assertTrue(output.endswith("。"))
        self.assertEqual(output.translate(str.maketrans("", "", "，。？、")), text)

    def test_existing_punctuation_is_not_doubled(self):
        text = "你好，世界！\n请问可以吗？"
        self.assertEqual(self.punctuator.restore(text), text)

    def test_english_keeps_whisper_output(self):
        text = "Open /home/demo/app.py and check version 3.12"
        self.assertEqual(self.punctuator.restore(text), text)


if __name__ == "__main__":
    unittest.main()
