# OptiSigns Technical Test

My submission for the OptiSigns / AlphaSphere technical test (Coding + Planning), Mid/Senior Full Stack Developer.


## Build, run and test
```
uv sync --locked
uv run pytest -q
cp .env.sample .env   # then set API_KEY
uv run main.py
docker build -t kbsync . && docker run --rm -e API_KEY=... kbsync
```

## Plan
_To be written: how the work was planned and split._

## AI Collaboration Narrative
_To be written as the work progresses._
