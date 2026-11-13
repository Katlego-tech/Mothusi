#!/bin/sh
# Compose POSTGRES_URL from the RDS secret's parts (deployment-aws.md §6.4), then run the command.
# ECS injects POSTGRES_HOST/PORT/DB/USER/PASSWORD from the secret; the laptop can pass POSTGRES_URL
# directly, in which case it is left as it is. User and password are percent-encoded.
set -eu
if [ -z "${POSTGRES_URL:-}" ] && [ -n "${POSTGRES_HOST:-}" ]; then
  POSTGRES_URL="$(python -c '
import os
from urllib.parse import quote
e = os.environ
print("postgresql://{}:{}@{}:{}/{}".format(
    quote(e["POSTGRES_USER"], safe=""), quote(e["POSTGRES_PASSWORD"], safe=""),
    e["POSTGRES_HOST"], e.get("POSTGRES_PORT", "5432"), e["POSTGRES_DB"]))
')"
  export POSTGRES_URL
fi
exec "$@"
