#!/bin/sh
# Generates a bcrypt-hashed dozzle users.yml at $OUTPUT_FILE.
# Reads ADMIN_USER and ADMIN_PASSWORD from the container environment (.env).
# OUTPUT_FILE defaults to /data/dozzle-users.yml — the container expects
# its host data root to be bind-mounted at /data (any value of
# MOE_DATA_ROOT on the host works).
set -e

USER="${ADMIN_USER:-admin}"
PASS="${ADMIN_PASSWORD:-}"
OUTPUT_FILE="${DOZZLE_USERS_FILE:-/data/dozzle-users.yml}"

if [ -z "$PASS" ]; then
  echo "ERROR: ADMIN_PASSWORD is not set in .env (refusing to write empty bcrypt hash)" >&2
  exit 1
fi

OUT_DIR="$(dirname "$OUTPUT_FILE")"
if [ ! -d "$OUT_DIR" ]; then
  echo "ERROR: output directory $OUT_DIR does not exist (mount MOE_DATA_ROOT at $OUT_DIR)" >&2
  exit 1
fi

pip install bcrypt --quiet --no-cache-dir --root-user-action ignore

OUTPUT_FILE="$OUTPUT_FILE" python3 - <<PYEOF
import bcrypt, os
user = os.environ["ADMIN_USER"]
pwd  = os.environ["ADMIN_PASSWORD"].encode()
out_path = os.environ.get("OUTPUT_FILE", "/data/dozzle-users.yml")
hsh  = bcrypt.hashpw(pwd, bcrypt.gensalt(10)).decode()
out  = "users:\n  {}:\n    name: {}\n    password: \"{}\"\n    email: admin@localhost\n".format(user, user, hsh)
with open(out_path, "w") as f:
    f.write(out)
print(f"dozzle-users.yml written for user '{user}' at {out_path}")
PYEOF
