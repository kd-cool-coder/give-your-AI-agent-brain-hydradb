import io
import json
import os
import time

from dotenv import load_dotenv
from hydra_db import HydraDB
from hydra_db.errors import ConflictError
from hydra_db.helpers import build_string


load_dotenv()

DATABASE = "eng_assistant_tutorial"
SHARED_COLLECTION = "engineering_knowledge"
QUERY = "What would break if we deprecated the v1 payments API?"


def text_file(filename, content):
    return (filename, io.BytesIO(content.encode("utf-8")), "text/plain")


def field(obj, key, default=""):
    if obj is None:
        return default
    if isinstance(obj, dict):
        return obj.get(key, default)
    return getattr(obj, key, default)


def score_text(score):
    return f"{score:.3f}" if isinstance(score, (int, float)) else "n/a"


def wait_for_indexing(client, database, ids, collection=None, timeout=300):
    start = time.time()
    while time.time() - start < timeout:
        status_kwargs = {"database": database, "ids": ids}
        if collection is not None:
            status_kwargs["collection"] = collection

        status = client.context.status(**status_kwargs)
        items = status.data.statuses
        states = [s.indexing_status for s in items]

        if all(s == "completed" for s in states):
            print("All sources indexed and graph-ready.")
            return

        if any(s in ("errored", "failed") for s in states):
            details = {
                id_: getattr(item, "error_message", None) or getattr(item, "message", "")
                for id_, item in zip(ids, items)
            }
            raise RuntimeError(f"Indexing failed: {dict(zip(ids, states))}; {details}")

        print(f"  Status: {dict(zip(ids, states))}")
        time.sleep(5)

    raise TimeoutError("Indexing did not complete within timeout.")


def create_database(client):
    try:
        client.databases.create(database=DATABASE)
    except ConflictError:
        print(f"Database {DATABASE!r} already exists; reusing it.")

    while True:
        status = client.databases.status(database=DATABASE)
        if status.data.infra.ready_for_ingestion:
            print("Database ready.")
            return
        print("Waiting for database provisioning...")
        time.sleep(3)


def ingest_knowledge(client):
    adr_007 = """# ADR-007: Migrate from Payments API v1 to v2
**Status:** Accepted | **Date:** 2025-03-15 | **Team:** Payments

## Context
v1 payments API uses synchronous Stripe calls with per-request connection setup.
p99 latency exceeds 1200ms at 500 RPS. v2 introduces connection pooling, async
webhook handling, and batch settlement.

## Decision
Deprecate v1 by Q3 2025. All consuming services must migrate to v2.
Highest-priority consumers: billing-service, invoice-generator, checkout-service.

## Consequences
- checkout-service: update payment initiation calls (owner: frontend-platform)
- billing-service: migrate recurring charge logic (owner: payments)
- invoice-generator: switch settlement endpoints (owner: payments)
"""

    meeting_notes = """# Architecture Review: Payments v1 Deprecation
**Date:** 2025-04-02 | **Attendees:** Sarah Chen (Payments Lead), Mike Torres (Platform),
Priya Patel (Billing), James Wright (SRE)

Sarah: ADR-007 is accepted. v1 sunset target is September 1. Billing-service is the
critical path - Priya, your team owns that migration.
Priya: We can start in May. Recurring charge logic is tightly coupled to v1's
synchronous response format. Estimate: 3-4 sprints.
Mike: checkout-service has a thinner integration. We can parallelize.
James: The March 12 incident (INC-2025-0312) was v1 connection exhaustion under load.
That postmortem recommended this migration. Link the timeline so on-call knows.
"""

    postmortem = """# Incident Postmortem: INC-2025-0312
**Severity:** SEV-2 | **Duration:** 47 minutes | **Date:** 2025-03-12
**Service:** payments-api-v1 | **On-call:** James Wright (SRE)

## Summary
payments-api-v1 exhausted its Stripe connection pool under sustained load (>600 RPS),
causing cascading timeouts in billing-service and checkout-service. Customer-facing
payment failures lasted 47 minutes.

## Root Cause
v1 creates a new Stripe connection per request. At 600+ RPS, the connection pool
ceiling (default: 500) is exceeded. No circuit breaker existed.

## Resolution
Temporarily increased pool ceiling to 1000. Permanent fix: migrate to v2 API
which uses persistent connection pooling (see ADR-007).
"""

    source_ids = []

    adr_result = client.context.ingest(
        database=DATABASE,
        collection=SHARED_COLLECTION,
        type="knowledge",
        documents=text_file("adr_007.md", adr_007),
        document_metadata=json.dumps(
            [
                {
                    "id": "adr_007",
                    "title": "ADR-007: Payments API v1 to v2 Migration",
                    "additional_metadata": {"document_type": "adr", "service": "payments"},
                }
            ]
        ),
        upsert="true",
    )
    source_ids.extend([r.id for r in adr_result.data.results])
    print(f"Ingested ADR: {adr_result.data.results[0].id}")

    slack_messages = [
        {
            "id": "slack_payments_eng_20250410_0914",
            "external_id": "1744276440.000100",
            "body": "Migration tracking spreadsheet is live. billing-service target: June 30. checkout-service target: July 15. invoice-generator target: August 1.",
            "author": "sarah.chen",
            "created_at": "2025-04-10T09:14:00Z",
        },
        {
            "id": "slack_payments_eng_20250410_0932",
            "external_id": "1744277520.000200",
            "body": "Billing will need a feature flag for the cutover. We're calling it `use_payments_v2`. Can't do a hard switch mid-billing-cycle.",
            "author": "priya.patel",
            "created_at": "2025-04-10T09:32:00Z",
            "parent_id": "1744276440.000100",
        },
        {
            "id": "slack_payments_eng_20250410_1005",
            "external_id": "1744279500.000300",
            "body": "SRE will keep v1 monitoring active until all services confirm migration. Dashboard: go/payments-v1-deprecation",
            "author": "james.wright",
            "created_at": "2025-04-10T10:05:00Z",
            "parent_id": "1744276440.000100",
        },
        {
            "id": "slack_payments_eng_20250415_1422",
            "external_id": "1744726920.000400",
            "body": "checkout-service PR is up - only 3 call sites. Targeting merge by April 25. After that, billing is the only hard blocker.",
            "author": "mike.torres",
            "created_at": "2025-04-15T14:22:00Z",
            "parent_id": "1744276440.000100",
        },
    ]
    thread_id = slack_messages[0]["external_id"]

    app_sources = [
        {
            "id": "meeting_notes",
            "database": DATABASE,
            "collection": SHARED_COLLECTION,
            "title": "Architecture Review: Payments v1 Deprecation",
            "type": "internal_notes",
            "kind": "knowledge_base",
            "provider": "some_internal_notes_provider",
            "external_id": "arch-review-payments-v1-2025-04-02",
            "timestamp": "2025-04-02T00:00:00Z",
            "fields": {
                "kind": "knowledge_base",
                "title": "Architecture Review: Payments v1 Deprecation",
                "body": meeting_notes,
                "created_by": "sarah.chen",
                "created_at": "2025-04-02T00:00:00Z",
            },
            "metadata": {"document_type": "meeting_notes", "service": "payments"},
        },
        *[
            {
                "id": message["id"],
                "database": DATABASE,
                "collection": SHARED_COLLECTION,
                "title": "#payments-eng - v1 Deprecation Timeline",
                "type": "slack",
                "kind": "message",
                "provider": "slack",
                "external_id": message["external_id"],
                "timestamp": message["created_at"],
                "fields": {
                    "kind": "message",
                    "body": message["body"],
                    "author": message["author"],
                    "thread_id": thread_id,
                    "created_at": message["created_at"],
                    **({"parent_id": message["parent_id"]} if "parent_id" in message else {}),
                },
                "metadata": {"document_type": "slack_thread", "service": "payments", "channel": "payments-eng"},
            }
            for message in slack_messages
        ],
        {
            "id": "postmortem",
            "database": DATABASE,
            "collection": SHARED_COLLECTION,
            "title": "Incident Postmortem: INC-2025-0312",
            "type": "jira",
            "kind": "ticket",
            "provider": "jira",
            "external_id": "INC-2025-0312",
            "timestamp": "2025-03-12T00:00:00Z",
            "fields": {
                "kind": "ticket",
                "title": "Incident Postmortem: INC-2025-0312",
                "description": postmortem,
                "status": "resolved",
                "priority": "sev-2",
                "assignee": "james.wright",
                "created_at": "2025-03-12T00:00:00Z",
            },
            "metadata": {"document_type": "postmortem", "service": "payments", "incident_id": "INC-2025-0312"},
        },
    ]

    app_result = client.context.ingest(
        database=DATABASE,
        collection=SHARED_COLLECTION,
        type="knowledge",
        app_knowledge=json.dumps(app_sources),
        upsert="true",
    )
    source_ids.extend([r.id for r in app_result.data.results])
    print(f"Ingested {len(app_sources)} app sources")

    return source_ids


def ingest_manifest_graph(client):
    manifest_text = """# Service Dependency Manifest
- checkout-service (owner: frontend-platform) -> payments-api-v1, payments-api-v2
- billing-service (owner: payments) -> payments-api-v1, stripe-api
- invoice-generator (owner: payments) -> payments-api-v1, billing-service
- auth-service (owner: platform) -> rate-limit-config
- rate-limit-config -> references payments-api-v1 routes
"""

    manifest_id = "service_manifest"
    manifest_graph = {
        "entities": {
            "checkout": {"name": "checkout-service", "type": "SERVICE", "namespace": "services"},
            "billing": {"name": "billing-service", "type": "SERVICE", "namespace": "services"},
            "invoice": {"name": "invoice-generator", "type": "SERVICE", "namespace": "services"},
            "auth": {"name": "auth-service", "type": "SERVICE", "namespace": "services"},
            "payments_v1": {"name": "payments-api-v1", "type": "API", "namespace": "apis"},
            "payments_v2": {"name": "payments-api-v2", "type": "API", "namespace": "apis"},
            "stripe_api": {"name": "stripe-api", "type": "API", "namespace": "apis"},
            "rate_limit_config": {"name": "rate-limit-config", "type": "CONFIG", "namespace": "platform"},
            "team_frontend": {"name": "frontend-platform", "type": "TEAM", "namespace": "teams"},
            "team_payments": {"name": "payments", "type": "TEAM", "namespace": "teams"},
            "team_platform": {"name": "platform", "type": "TEAM", "namespace": "teams"},
        },
        "relations": [
            {
                "source": "checkout",
                "target": "payments_v1",
                "predicate": "DEPENDS_ON",
                "context": "checkout-service still calls payments-api-v1.",
            },
            {
                "source": "checkout",
                "target": "payments_v2",
                "predicate": "DEPENDS_ON",
                "context": "checkout-service has started integrating payments-api-v2.",
            },
            {
                "source": "billing",
                "target": "payments_v1",
                "predicate": "DEPENDS_ON",
                "context": "billing-service depends on payments-api-v1 for recurring charges.",
            },
            {
                "source": "billing",
                "target": "stripe_api",
                "predicate": "DEPENDS_ON",
                "context": "billing-service also calls stripe-api.",
            },
            {
                "source": "invoice",
                "target": "payments_v1",
                "predicate": "DEPENDS_ON",
                "context": "invoice-generator depends on payments-api-v1 settlement endpoints.",
            },
            {
                "source": "invoice",
                "target": "billing",
                "predicate": "DEPENDS_ON",
                "context": "invoice-generator depends on billing-service.",
            },
            {
                "source": "auth",
                "target": "rate_limit_config",
                "predicate": "DEPENDS_ON",
                "context": "auth-service references payment route rate-limit configuration.",
            },
            {
                "source": "rate_limit_config",
                "target": "payments_v1",
                "predicate": "REFERENCES",
                "context": "rate-limit-config references payments-api-v1 routes.",
            },
            {
                "source": "payments_v2",
                "target": "payments_v1",
                "predicate": "REPLACES",
                "context": "payments-api-v2 is the migration target for payments-api-v1 consumers.",
            },
            {"source": "checkout", "target": "team_frontend", "predicate": "OWNED_BY"},
            {"source": "billing", "target": "team_payments", "predicate": "OWNED_BY"},
            {"source": "invoice", "target": "team_payments", "predicate": "OWNED_BY"},
            {"source": "auth", "target": "team_platform", "predicate": "OWNED_BY"},
        ],
    }

    result = client.context.ingest(
        database=DATABASE,
        collection=SHARED_COLLECTION,
        type="knowledge",
        documents=text_file("service_manifest.md", manifest_text),
        document_metadata=json.dumps(
            [
                {
                    "id": manifest_id,
                    "title": "service_manifest.md",
                    "additional_metadata": {"document_type": "manifest", "service": "payments"},
                }
            ]
        ),
        graph_payload=json.dumps({manifest_id: manifest_graph}),
        upsert="true",
    )
    print(
        f"Ingested manifest with {len(manifest_graph['entities'])} entities, "
        f"{len(manifest_graph['relations'])} relations"
    )
    return [r.id for r in result.data.results]


def compare_graph_retrieval(client):
    result_no_graph = client.query(
        database=DATABASE,
        collection=SHARED_COLLECTION,
        query=QUERY,
        type="knowledge",
        query_by="hybrid",
        mode="fast",
        max_results=10,
        graph_context=False,
        query_apps=True,
    )

    chunks = result_no_graph.data.chunks or []
    print(f"\nGraph context off: {len(chunks)} chunks returned")
    for chunk in chunks[:5]:
        title = chunk.source_title or chunk.id or "unknown source"
        content = chunk.chunk_content or ""
        print(f"[{title}] (score: {score_text(chunk.relevancy_score)})")
        print(f"  {content[:120]}...")

    result_with_graph = client.query(
        database=DATABASE,
        collection=SHARED_COLLECTION,
        query=QUERY,
        type="knowledge",
        query_by="hybrid",
        graph_context=True,
        query_apps=True,
        mode="thinking",
        max_results=10,
    )

    graph = result_with_graph.data.graph_context
    chunks = result_with_graph.data.chunks or []
    query_paths = graph.query_paths or [] if graph else []
    chunk_relations = graph.chunk_relations or [] if graph else []

    print(f"\nGraph context on: {len(chunks)} chunks returned")
    print(f"Query paths: {len(query_paths)}")
    print(f"Chunk relations: {len(chunk_relations)}")

    print("\n--- Query Paths ---")
    for path in query_paths:
        for triplet in path.triplets or []:
            src = field(triplet.source, "name", "unknown")
            rel = field(triplet.relation, "canonical_predicate", "RELATED_TO")
            tgt = field(triplet.target, "name", "unknown")
            print(f"  {src} --[{rel}]--> {tgt}")
        print(f"  (relevancy: {score_text(path.relevancy_score)})\n")

    return result_with_graph


def generate_with_openai(result_with_graph):
    if not os.environ.get("OPENAI_API_KEY"):
        return

    from openai import OpenAI

    context_string = build_string(result_with_graph)
    llm = OpenAI()
    response = llm.chat.completions.create(
        model=os.getenv("OPENAI_MODEL", "gpt-4o"),
        messages=[
            {
                "role": "system",
                "content": (
                    "You are an internal engineering assistant. Answer based only "
                    "on the provided context. If the context doesn't contain enough "
                    "information, say so. Cite the source document for each claim."
                ),
            },
            {"role": "user", "content": f"Context:\n{context_string}\n\nQuestion: {QUERY}"},
        ],
    )
    print(response.choices[0].message.content)


def ingest_persona_memories(client):
    engineer_memories = [
        {
            "id": "eng_mem_1",
            "text": (
                "I work on billing-service. My current sprint focuses on migrating "
                "the recurring charge logic from payments-api-v1 to v2."
            ),
            "infer": False,
        },
        {
            "id": "eng_mem_2",
            "text": (
                "The feature flag for our v2 cutover is `use_payments_v2`. "
                "We can't do a hard switch mid-billing-cycle."
            ),
            "infer": False,
        },
        {
            "id": "eng_mem_3",
            "text": (
                "I prefer seeing API request/response examples and code-level "
                "migration steps over high-level timelines."
            ),
            "infer": False,
        },
    ]

    manager_memories = [
        {
            "id": "mgr_mem_1",
            "text": "I manage the payments team. Priya Patel reports to me and owns the billing-service migration.",
            "infer": False,
        },
        {
            "id": "mgr_mem_2",
            "text": (
                "My priorities are: shipping the v1 deprecation by September 1, "
                "managing risk to billing SLAs, and keeping stakeholders updated."
            ),
            "infer": False,
        },
        {
            "id": "mgr_mem_3",
            "text": "I prefer timeline views, ownership maps, and risk assessments over implementation details.",
            "infer": False,
        },
    ]

    memory_ids_by_collection = {}
    for collection, memories in [("backend_engineer", engineer_memories), ("eng_manager", manager_memories)]:
        result = client.context.ingest(
            database=DATABASE,
            collection=collection,
            type="memory",
            memories=json.dumps(memories),
            upsert="true",
        )
        memory_ids_by_collection[collection] = [r.id for r in result.data.results]
        print(f"Ingested {len(memories)} memories for {collection}")

    return memory_ids_by_collection


def query_with_personas(client):
    for persona in ["backend_engineer", "eng_manager"]:
        result = client.query(
            database=DATABASE,
            query=QUERY,
            type="all",
            collections={SHARED_COLLECTION: 1.0, persona: 1.0},
            graph_context=True,
            query_apps=True,
            query_by="hybrid",
            mode="thinking",
            max_results=10,
        )

        graph = result.data.graph_context
        chunks = result.data.chunks or []
        query_paths = graph.query_paths or [] if graph else []

        print(f"\n{'=' * 60}")
        print(f"PERSONA: {persona}")
        print(f"Chunks: {len(chunks)} | Paths: {len(query_paths)}")
        print(f"{'=' * 60}")

        if os.environ.get("OPENAI_API_KEY"):
            from openai import OpenAI

            context = build_string(result)
            llm = OpenAI()
            response = llm.chat.completions.create(
                model=os.getenv("OPENAI_MODEL", "gpt-4o"),
                messages=[
                    {
                        "role": "system",
                        "content": (
                            "You are an internal engineering assistant. Answer based only on the "
                            "provided context. Tailor the depth and focus to what is most relevant "
                            "given the user's context."
                        ),
                    },
                    {"role": "user", "content": f"Context:\n{context}\n\nQuestion: {QUERY}"},
                ],
            )
            print(response.choices[0].message.content)


def main():
    client = HydraDB(token=os.environ["HYDRA_DB_API_KEY"])

    create_database(client)
    source_ids = ingest_knowledge(client)
    source_ids.extend(ingest_manifest_graph(client))

    wait_for_indexing(client, DATABASE, source_ids, collection=SHARED_COLLECTION)

    result_with_graph = compare_graph_retrieval(client)
    generate_with_openai(result_with_graph)

    memory_ids_by_collection = ingest_persona_memories(client)
    for collection, ids in memory_ids_by_collection.items():
        wait_for_indexing(client, DATABASE, ids, collection=collection, timeout=120)

    query_with_personas(client)

    client.databases.delete(database=DATABASE)
    print("Tutorial database deleted.")


if __name__ == "__main__":
    main()
