# Seven-day Keycloak login sessions — 2026-10-08

## Observed starting state

Read the live `clouddsp` realm and `clouddsp-react` settings through the Admin
API, printing only non-secret lifetime fields. Access tokens lasted 300 seconds
(five minutes), SSO idle timeout was 1800 seconds (30 minutes), and SSO maximum
was 36000 seconds (10 hours). Remember me was disabled. Client timeouts
inherited the realm, without a React client lifetime override.

## Applied policy

Committed policy/migration: `78ab18a`.

- SSO idle, SSO maximum, and both Remember me timeouts: 604800 seconds
  (seven days). The maximum is measured from login, rather than extending
  indefinitely with activity.
- Access token lifetime: 300 seconds; the existing browser refresh path stays
  in use. Client session timeouts inherit the realm (zero).
- Remember me enabled. Selecting it at login creates a persistent SSO cookie
  for browser restarts. React's tokens still use per-tab `sessionStorage`.
- Master realm, existing users, credentials, keys, grant types, PKCE, audience,
  registration, email verification and SMTP were not edited by this policy.

The committed migration mode ran only `keycloak-realm-session-policy`, after
checking prerequisites and a server dry run. Its partial realm update was
followed by successful verification of the complete durable realm/client
configuration. Fresh deployment now includes the same policy Job. No image
build or identity-server restart was required.

## Verification

- 51 relevant deployment tests passed (5862 assertions), including seven-day
  policy checks, client-override detection, ordered fresh bootstrap, and the
  targeted existing-realm migration.
- Policy YAML, shell syntax, Kubernetes server dry run and whitespace passed.
- Live realm readback and full `keycloak-config-verify.rb verify` passed.
- Real PKCE smoke used the unchanged `clouddsp-react` client with one random,
  disposable, email-verified user. Its login form included Remember me.
- Issued access token: 300 seconds. Refresh token remaining lifetime:
  approximately 604800 seconds. Remember me identity cookie: 604800 seconds.
- Refresh grant succeeded; the refreshed access token authenticated `/auth/me`
  as the test user. Deleting that temporary user revoked its refresh session,
  and a subsequent refresh returned HTTP 400. Test identity cleanup succeeded.
- No credentials, codes, cookies, or tokens were printed or written to files.

Previously expired refresh tokens cannot be revived. Sign in once again and
select Remember me to establish the new persistent session. A newly opened
tab may require the app's Sign in button to start PKCE again; Keycloak's valid
SSO cookie then avoids another password prompt. Sign-out/revocation can end
the session before seven days.
