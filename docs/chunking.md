# Chunking measurement (2026-10-30, live, 416 articles)

## Corpus token stats (`scripts/measure_tokens.py`, tiktoken cl100k_base)

articles=416 tokens min=158 median=1077 p95=3476 max=7414 total=559378

Share of articles at or below the max chunk size: 600 -> 103/416 (24.8%); 800 -> 157/416 (37.7%).
Most articles are therefore split into several chunks under either setting.

## Retrieval check (`scripts/chunk_check.py 600/150 800/400`, search top 3 results, 10 questions)

Expected article in top 1 / top 3 (questions and expected ids: `tests/retrieval/questions.json`):

| Question | 600/150 top1 | 600/150 top3 | 800/400 top1 | 800/400 top3 |
|---|---|---|---|---|
| How do I add a YouTube video? | yes | yes | yes | yes |
| How do I set up and add a new screen? | no | no | no | no |
| Why does my screen show as offline on the web portal? | yes | yes | yes | yes |
| How do I change a screen's orientation to portrait? | yes | yes | yes | yes |
| How do I show a PDF on my screens? | yes | yes | yes | yes |
| How do I use RSS feeds? | yes | yes | yes | yes |
| How do I use the countdown app? | yes | yes | yes | yes |
| How do I change my billing information? | yes | yes | yes | yes |
| How do I show my Instagram feed? | yes | yes | yes | yes |
| How do I show the weather on a screen? | yes | yes | yes | yes |
| Total | 9/10 | 9/10 | 9/10 | 9/10 |

## Choice

`max_chunk_size_tokens=600`, `chunk_overlap_tokens=150` (defaults in `config.py` and `.env.sample`).
Both settings score the same (9/10 top 1 and top 3), so there is no evidence to leave the starting candidate.
600/150 gives smaller, more focused chunks and an overlap of one quarter, at a modest storage cost
(about 1,323 chunks estimated against fewer with 800/400).
The one miss is the same under both settings, so it is a question/article match issue, not a chunking one.
