# Give Your AI Agent a Brain in 30 Minutes

Runnable code for the tutorial in `article.md`.

## Setup

```shell
python -m venv .venv
source .venv/bin/activate
pip install -r requirements.txt
cp .env.example .env
```

Add your HydraDB API key to `.env`. The OpenAI key is optional and is only used for the final generation examples.

## Run

```shell
python tutorial.py
```

The script creates a HydraDB database, ingests synthetic engineering documents and an explicit service dependency graph, compares retrieval with and without graph context, adds persona memories, and then deletes the tutorial database at the end.
