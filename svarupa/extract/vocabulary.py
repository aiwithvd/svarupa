"""The classification vocabulary: which imported packages mean what.

Data, not code. Each table maps a package name (the import specifier's root:
`motor` for `motor.motor_asyncio`, `@google-cloud/storage` for a scoped npm
package) to a display label. A review attacks the table; a user can read it;
nothing here is inferred from the label text of a symbol.

Categories follow Archify's component vocabulary so the two artifacts speak
the same language: `database`, `messagebus`, `cloud` become external boxes a
module talks to (the import line is the evidence); `security` and `frontend`
are roles the importing module takes. A package that appears in no table is
an ordinary dependency and draws nothing: a third-party library is a
dependency, not a component.
"""

from __future__ import annotations

__all__ = ["CATEGORIES", "classify_import", "package_root"]

# package root -> (category, display label)
_STORES: dict[str, str] = {
    "pymongo": "MongoDB",
    "motor": "MongoDB",
    "mongoengine": "MongoDB",
    "beanie": "MongoDB",
    "mongoose": "MongoDB",
    "mongodb": "MongoDB",
    "sqlalchemy": "SQL database",
    "psycopg": "PostgreSQL",
    "psycopg2": "PostgreSQL",
    "asyncpg": "PostgreSQL",
    "pg": "PostgreSQL",
    "postgres": "PostgreSQL",
    "mysql": "MySQL",
    "pymysql": "MySQL",
    "mysql2": "MySQL",
    "aiomysql": "MySQL",
    "sqlite3": "SQLite",
    "better-sqlite3": "SQLite",
    "redis": "Redis",
    "aioredis": "Redis",
    "ioredis": "Redis",
    "prisma": "Prisma database",
    "@prisma/client": "Prisma database",
    "typeorm": "SQL database",
    "sequelize": "SQL database",
    "knex": "SQL database",
    "drizzle-orm": "SQL database",
    "elasticsearch": "Elasticsearch",
    "@elastic/elasticsearch": "Elasticsearch",
    "opensearchpy": "OpenSearch",
    "cassandra": "Cassandra",
    "neo4j": "Neo4j",
    "pinecone": "Pinecone",
    "weaviate": "Weaviate",
    "chromadb": "Chroma",
    "qdrant_client": "Qdrant",
    "pymilvus": "Milvus",
    "dynamodb": "DynamoDB",
}

_BUSES: dict[str, str] = {
    "kafka": "Kafka",
    "confluent_kafka": "Kafka",
    "aiokafka": "Kafka",
    "kafkajs": "Kafka",
    "pika": "RabbitMQ",
    "aio_pika": "RabbitMQ",
    "amqplib": "RabbitMQ",
    "amqp": "RabbitMQ",
    "kombu": "message broker",
    "celery": "Celery broker",
    "bullmq": "Redis queue",
    "bull": "Redis queue",
    "nats": "NATS",
    "pulsar": "Pulsar",
    "@google-cloud/pubsub": "Pub/Sub",
    "paho": "MQTT",
    "mqtt": "MQTT",
}

_CLOUD: dict[str, str] = {
    "openai": "OpenAI API",
    "anthropic": "Anthropic API",
    "@anthropic-ai/sdk": "Anthropic API",
    "google.generativeai": "Gemini API",
    "@google/generative-ai": "Gemini API",
    "langchain_google_genai": "Gemini API",
    "langchain_openai": "OpenAI API",
    "langchain_anthropic": "Anthropic API",
    "cohere": "Cohere API",
    "boto3": "AWS",
    "botocore": "AWS",
    "aioboto3": "AWS",
    "@aws-sdk": "AWS",
    "aws-sdk": "AWS",
    "google.cloud": "Google Cloud",
    "@google-cloud": "Google Cloud",
    "googleapis": "Google APIs",
    "azure": "Azure",
    "@azure": "Azure",
    "firebase": "Firebase",
    "firebase_admin": "Firebase",
    "firebase-admin": "Firebase",
    "stripe": "Stripe",
    "twilio": "Twilio",
    "sendgrid": "SendGrid",
    "@sendgrid/mail": "SendGrid",
    "slack_sdk": "Slack API",
    "@slack/web-api": "Slack API",
    "github": "GitHub API",
    "@octokit": "GitHub API",
    "supabase": "Supabase",
    "@supabase/supabase-js": "Supabase",
    "sentry_sdk": "Sentry",
    "@sentry": "Sentry",
}

_AUTH: dict[str, str] = {
    "jwt": "JWT",
    "pyjwt": "JWT",
    "jose": "JWT",
    "python_jose": "JWT",
    "jsonwebtoken": "JWT",
    "passlib": "password hashing",
    "bcrypt": "password hashing",
    "argon2": "password hashing",
    "authlib": "OAuth",
    "oauthlib": "OAuth",
    "passport": "Passport auth",
    "fastapi.security": "FastAPI security",
    "flask_login": "Flask-Login",
    "flask_jwt_extended": "JWT",
    "django.contrib.auth": "Django auth",
    "next-auth": "NextAuth",
    "@auth0": "Auth0",
    "auth0": "Auth0",
    "@clerk": "Clerk",
    "keycloak": "Keycloak",
}

_FRONTEND: dict[str, str] = {
    "react": "React",
    "react-dom": "React",
    "next": "Next.js",
    "vue": "Vue",
    "nuxt": "Nuxt",
    "svelte": "Svelte",
    "@sveltejs/kit": "SvelteKit",
    "@angular/core": "Angular",
    "solid-js": "Solid",
    "preact": "Preact",
    "lit": "Lit",
}

CATEGORIES: dict[str, dict[str, str]] = {
    "database": _STORES,
    "messagebus": _BUSES,
    "cloud": _CLOUD,
    "security": _AUTH,
    "frontend": _FRONTEND,
}


def package_root(specifier: str, lang: str) -> str:
    """The package a specifier belongs to, in the language's own terms.

    Python: `motor.motor_asyncio` -> `motor`, but dotted table keys such as
    `fastapi.security` and `google.cloud` are honoured when they prefix the
    specifier, because the root alone (`fastapi`, `google`) means something
    else. TypeScript: `@scope/name` keeps its scope; `@scope/name/sub` drops
    the sub-path; a bare `name/sub` drops the sub-path.
    """
    if lang in ("typescript", "javascript"):
        parts = specifier.split("/")
        if specifier.startswith("@") and len(parts) >= 2:
            return "/".join(parts[:2])
        return parts[0]
    return specifier.split(".")[0]


def classify_import(specifier: str, lang: str) -> tuple[str, str] | None:
    """(category, label) for a specifier the vocabulary knows, else None.

    Dotted and scoped table keys are tried as prefixes first (longest match),
    then the bare root, so `google.cloud.storage` is Google Cloud while
    `google.generativeai` is the Gemini API and plain `google` is nothing.
    """
    candidates: list[tuple[str, str, str]] = []  # (key, category, label)
    for category, table in CATEGORIES.items():
        for key, label in table.items():
            if (
                specifier == key
                or specifier.startswith(key + ".")
                or specifier.startswith(key + "/")
            ):
                candidates.append((key, category, label))
    if candidates:
        key, category, label = max(candidates, key=lambda c: len(c[0]))
        return (category, label)
    root = package_root(specifier, lang)
    for category, table in CATEGORIES.items():
        if root in table:
            return (category, table[root])
    return None
