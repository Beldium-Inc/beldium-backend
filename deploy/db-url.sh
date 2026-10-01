#!/bin/bash
# Prints the environment's DATABASE_URL (from SSM), rewritten to go through the
# local tunnel opened by deploy/db-tunnel.sh. Use it inside $( ) so the password
# is never shown:
#
#   export TARGET=$(bash deploy/db-url.sh staging)
#   psql "$TARGET" -c 'select 1'
set -euo pipefail
ENV="${1:-staging}"
PORT="${2:-15432}"
URL=$(AWS_REGION=us-east-1 aws ssm get-parameter --name "/beldium/$ENV/DATABASE_URL" --with-decryption \
  --query Parameter.Value --output text)
python3 - "$URL" "$PORT" <<'EOF'
import sys
from urllib.parse import urlsplit, urlunsplit
url, port = sys.argv[1], sys.argv[2]
p = urlsplit(url)
# sslmode=require: RDS insists on TLS; the certificate names the RDS host, not
# localhost, so the tunnel can encrypt but not verify the name.
print(urlunsplit((p.scheme, f"{p.username}:{p.password}@127.0.0.1:{port}", p.path, "sslmode=require", "")))
EOF
