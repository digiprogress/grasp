#!/bin/bash
# Idempotent schema bootstrap for grasp graph DB
set -e

source "$(dirname "$0")/../../.env"

URL="${ARCADEDB_URL:-http://localhost:2480}"
DB="${ARCADEDB_DATABASE:-grasp}"
USER="${ARCADEDB_USER:-root}"
PASS="${ARCADEDB_ROOT_PASSWORD}"

run_sql() {
  local sql="$1"
  curl -sf -u "$USER:$PASS" -X POST "$URL/api/v1/command/$DB" \
    -H "Content-Type: application/json" \
    -d "{\"language\":\"sql\",\"command\":$(printf '%s' "$sql" | python3 -c 'import sys,json;print(json.dumps(sys.stdin.read()))')}" \
    | python3 -m json.tool 2>/dev/null || true
}

echo "Creating vertex types..."
run_sql "CREATE VERTEX TYPE Concept IF NOT EXISTS"
run_sql "CREATE VERTEX TYPE Domain IF NOT EXISTS"
run_sql "CREATE VERTEX TYPE Entity IF NOT EXISTS"
run_sql "CREATE VERTEX TYPE Annotation IF NOT EXISTS"
run_sql "CREATE VERTEX TYPE Chunk IF NOT EXISTS"

echo "Creating edge types..."
run_sql "CREATE EDGE TYPE RELATES_TO IF NOT EXISTS"
run_sql "CREATE EDGE TYPE BELONGS_TO IF NOT EXISTS"
run_sql "CREATE EDGE TYPE MAPS_TO IF NOT EXISTS"
run_sql "CREATE EDGE TYPE ANNOTATES IF NOT EXISTS"
run_sql "CREATE EDGE TYPE HAS_CHUNK IF NOT EXISTS"

echo "Creating properties on Concept..."
run_sql "CREATE PROPERTY Concept.name IF NOT EXISTS STRING"
run_sql "CREATE PROPERTY Concept.description IF NOT EXISTS STRING"
run_sql "CREATE PROPERTY Concept.created_at IF NOT EXISTS DATETIME"

echo "Creating properties on Domain..."
run_sql "CREATE PROPERTY Domain.name IF NOT EXISTS STRING"
run_sql "CREATE PROPERTY Domain.description IF NOT EXISTS STRING"

echo "Creating properties on Entity..."
run_sql "CREATE PROPERTY Entity.name IF NOT EXISTS STRING"
run_sql "CREATE PROPERTY Entity.type IF NOT EXISTS STRING"
run_sql "CREATE PROPERTY Entity.metadata IF NOT EXISTS STRING"

echo "Creating properties on Annotation..."
run_sql "CREATE PROPERTY Annotation.id IF NOT EXISTS STRING"
run_sql "CREATE PROPERTY Annotation.content IF NOT EXISTS STRING"
run_sql "CREATE PROPERTY Annotation.summary IF NOT EXISTS STRING"
run_sql "CREATE PROPERTY Annotation.annotation_type IF NOT EXISTS STRING"
run_sql "CREATE PROPERTY Annotation.source IF NOT EXISTS STRING"
run_sql "CREATE PROPERTY Annotation.created_at IF NOT EXISTS DATETIME"
run_sql "CREATE PROPERTY Annotation.updated_at IF NOT EXISTS DATETIME"

echo "Creating indexes..."
run_sql "CREATE INDEX IF NOT EXISTS ON Concept (name) UNIQUE"
run_sql "CREATE INDEX IF NOT EXISTS ON Domain (name) UNIQUE"
run_sql "CREATE INDEX IF NOT EXISTS ON Entity (name) NOTUNIQUE"
run_sql "CREATE INDEX IF NOT EXISTS ON Annotation (id) UNIQUE"
run_sql "CREATE INDEX IF NOT EXISTS ON Annotation (created_at) NOTUNIQUE"

echo "Creating properties on Chunk..."
run_sql "CREATE PROPERTY Chunk.id IF NOT EXISTS STRING"
run_sql "CREATE PROPERTY Chunk.text IF NOT EXISTS STRING"
run_sql "CREATE PROPERTY Chunk.idx IF NOT EXISTS INTEGER"
run_sql "CREATE PROPERTY Chunk.embedding IF NOT EXISTS STRING"
run_sql "CREATE PROPERTY Chunk.embedding_model IF NOT EXISTS STRING"
run_sql "CREATE PROPERTY Chunk.created_at IF NOT EXISTS DATETIME"

run_sql "CREATE INDEX IF NOT EXISTS ON Chunk (id) UNIQUE"

echo "Schema bootstrap complete."
