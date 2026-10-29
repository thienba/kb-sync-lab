# OpenAI Playground check (pending human)

Status: pending human. Not verified by the agent; backup is `uv run ask.py "<question>"`.

Steps to try (the UI may have changed; write down what differs):
1. Open https://platform.openai.com/playground in the project that owns the dev store.
2. Pick a prompt/assistant-style view that has a system prompt (Instructions) box; paste `prompts/optibot.md`.
3. Add the File Search tool and attach the vector store `optibot-kb-dev` (416 files).
4. Ask "How do I add a YouTube video?" and check the answer cites the YouTube article.

Finding to record here (max 15 lines in total): works / how it differs / use `ask.py` instead.
