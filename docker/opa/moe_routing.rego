package moe.routing

# Deny outbound LLM egress for local_only requests to non-private hosts.
# Wired up in a follow-up: services/sovereignty.py will query OPA when
# MOE_OPA_URL is set; until then the in-process guard enforces the same rule.
default allow := false

allow if {
    not input.local_only
}

allow if {
    input.local_only
    input.host_is_private
}
