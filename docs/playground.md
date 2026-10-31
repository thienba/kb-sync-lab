# OpenAI Playground check

Checked by hand on 2026-10-30. It works, and `docs/screenshot-youtube.png` is the result.

1. Open https://platform.openai.com/playground in the project that owns the store. The page is "New prompt"; there is no Assistant object.
2. Paste `prompts/optibot.md` into the Prompt box and set the model to `gpt-5.4-mini`.
3. Under Hosted tools, turn on File search and pick the store `optibot-kb` (over 400 files).
4. Ask "How do I add a YouTube video?".

Result: 5 bullets, the "Article URL:" line with the YouTube article, and the source file as a chip under the answer.

The store name shows only in the File search dialog (the pencil icon), not in the side panel. The prompt stays an unsaved draft. Saved prompts shut down on 2026-11-30; this page was not checked after that date. If the Playground stops offering File search, `uv run ask.py "<question>"` asks the same store with the same prompt.
