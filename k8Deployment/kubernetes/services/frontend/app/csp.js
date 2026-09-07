import { URL } from 'node:url';

const commonDirectives = [
    "default-src 'self'",
    "base-uri 'self'",
    "object-src 'none'",
    "script-src 'self'",
    "style-src 'self' 'unsafe-inline'",
    "img-src 'self' data: blob:",
    "font-src 'self' data:",
    "media-src 'self' blob: https://*.s3.amazonaws.com https://smpldsnds.github.io",
    "worker-src 'self' blob:",
    "frame-src 'none'",
    "frame-ancestors 'none'",
    "form-action 'self'",
];

function configuredOrigin(value, permittedProtocols) {
    if (!value) return null;
    try {
        const parsed = new URL(value);
        return permittedProtocols.has(parsed.protocol) ? parsed.origin : null;
    } catch {
        return null;
    }
}

/**
 * Build a policy from public build-time endpoints rather than permitting every
 * API Gateway or identity provider. The retained cloud S3 artifact path uses
 * its narrow HTTPS service wildcard, while the local private object store is
 * one explicit reviewed origin supplied at build time. Do not replace either
 * with a broad HTTP wildcard: a presigned POST needs only its one MinIO host.
 */
export function contentSecurityPolicy({
    jobApiUrl,
    objectStorageUrl,
    webSocketUrl,
    oidcIssuer,
    development = false,
}) {
    const connectSources = new Set([
        "'self'",
        'https://*.s3.amazonaws.com',
        // smplr fetches the FluidR3 guitar and bass banks as data through
        // fetch(), so this belongs in connect-src rather than script-src.
        'https://gleitz.github.io',
        'https://smpldsnds.github.io',
    ]);
    const jobApiOrigin = configuredOrigin(jobApiUrl, new Set(['https:']));
    // The local k3d profile deliberately exposes MinIO through its one HTTP
    // Traefik origin. HTTPS is accepted too so a future TLS profile changes
    // configuration rather than weakening this source allow-list.
    const objectStorageOrigin = configuredOrigin(objectStorageUrl, new Set(['http:', 'https:']));
    const webSocketOrigin = configuredOrigin(webSocketUrl, new Set(['wss:']));
    // The local Keycloak issuer uses HTTP only because it is mapped to the Mac
    // loopback interface for this learning cluster. Production deployment must
    // use HTTPS and a separately reviewed CSP policy.
    const resolvedOidcOrigin = configuredOrigin(oidcIssuer, new Set(['http:', 'https:']));
    if (jobApiOrigin) connectSources.add(jobApiOrigin);
    if (objectStorageOrigin) connectSources.add(objectStorageOrigin);
    if (webSocketOrigin) connectSources.add(webSocketOrigin);
    if (resolvedOidcOrigin) connectSources.add(resolvedOidcOrigin);
    if (development) {
        connectSources.add('http://localhost:*');
        connectSources.add('ws://localhost:*');
    }

    return [...commonDirectives, `connect-src ${[...connectSources].join(' ')}`].join('; ');
}
