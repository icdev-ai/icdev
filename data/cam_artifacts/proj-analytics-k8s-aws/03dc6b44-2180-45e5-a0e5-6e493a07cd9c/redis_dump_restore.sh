#!/usr/bin/env bash
# CUI // SP-CTI
# Redis DUMP/RESTORE migration: K8s Redis → ElastiCache
# Run from a bastion host with access to both endpoints.
set -euo pipefail

SOURCE_HOST="${SOURCE_REDIS_HOST:-redis.kube-system.svc.cluster.local}"
SOURCE_PORT="${SOURCE_REDIS_PORT:-6379}"
TARGET_HOST="${TARGET_ELASTICACHE_HOST:-analytics-redis.xxxx.cache.amazonaws.com}"
TARGET_PORT="6379"
TARGET_AUTH="${TARGET_REDIS_AUTH:-}"

echo "Scanning source keys..."
redis-cli -h "$SOURCE_HOST" -p "$SOURCE_PORT" --scan --pattern '*' | while read -r KEY; do
  TTL=$(redis-cli -h "$SOURCE_HOST" -p "$SOURCE_PORT" pttl "$KEY")
  DUMP=$(redis-cli -h "$SOURCE_HOST" -p "$SOURCE_PORT" dump "$KEY")
  if [ -n "$TARGET_AUTH" ]; then
    redis-cli -h "$TARGET_HOST" -p "$TARGET_PORT" -a "$TARGET_AUTH" \
      RESTORE "$KEY" "$TTL" "$DUMP" REPLACE
  else
    redis-cli -h "$TARGET_HOST" -p "$TARGET_PORT" \
      RESTORE "$KEY" "$TTL" "$DUMP" REPLACE
  fi
done
echo "Migration complete. Verify with: redis-cli -h $TARGET_HOST dbsize"
