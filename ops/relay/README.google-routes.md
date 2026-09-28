# Google Routes Relay

The private relay forwards fixed Google Routes and Geocoding endpoints without
changing request coordinates or native responses. The application remains the
owner of address identity, timing policy, and result interpretation.

## Explicit Egress

`BRP_GOOGLE_ROUTES_UPSTREAM_PROXY` optionally selects a loopback HTTP CONNECT
proxy, for example `http://127.0.0.1:18080`. Configure a separately supervised
private tunnel at that listener. Only literal loopback IP addresses and an
explicit nonzero port are accepted; credentials, paths, queries, and fragments
are rejected. Leave the variable unset to retain direct egress.

Both upstream APIs use this setting. Ambient proxy variables are ignored, TLS
certificate verification stays enabled, redirects are disabled, and proxy
failure does not trigger direct fallback or an automatic paid retry. The Google
API key travels inside the provider TLS connection, not in proxy authentication.

Use a loopback-only relay listener when application and relay are on the same
host. Retain bearer authentication and restrictive permissions on its environment
file. Do not expose the tunnel or relay publicly.

## Migration And Quota

Deploy an immutable, tested relay artifact independently of application feature
promotion. Before changing client URLs, check tunnel reachability, unauthorized
request rejection, a bounded authenticated request for each API, and the existing
monthly ledger. Keep the application quota database; never reset usage on a host
move. Preserve the old egress ledger when available. If it cannot be recovered,
record the source and use a conservative migration baseline for the independent
egress cap, without inventing historical success counts. The two enforcement
ledgers are not additive billing records.

Switch and restart idle application services one environment at a time, keeping
their code versions unchanged when only transport is being migrated. Verify
process-effective endpoints, service health, quota continuity, and absence of
the retired relay from active fallback settings. Preserve rollback configuration
privately; do not roll back to a known-offline host automatically.
