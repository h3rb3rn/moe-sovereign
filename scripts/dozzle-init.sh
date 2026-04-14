#!/bin/sh
# Generates /opt/moe-infra/dozzle-users.yml with a bcrypt-hashed password.
# Reads ADMIN_USER and ADMIN_PASSWORD from the container environment (.env).
set -e

USER="${ADMIN_USER:-admin}"
PASS="${ADMIN_PASSWORD:-}"

if [ -z "$PASS" ]; then
  echo "ERROR: ADMIN_PASSWORD is not set in .env" >&2
  exit 1
fi

pip install bcrypt --quiet --no-cache-dir

python3 - <<PYEOF
import bcrypt, os
user = os.environ["ADMIN_USER"]
pwd  = os.environ["ADMIN_PASSWORD"].encode()
hsh  = bcrypt.hashpw(pwd, bcrypt.gensalt(10)).decode()
out  = "users:\n  {}:\n    name: {}\n    password: \"{}\"\n    email: admin@localhost\n".format(user, user, hsh)
with open("/opt/moe-infra/dozzle-users.yml", "w") as f:
    f.write(out)
print("dozzle-users.yml written for user:", user)
PYEOF
