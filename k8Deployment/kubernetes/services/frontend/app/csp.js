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
 * API Gateway or identity provider. S3 remains a service wildcard because the
 * browser receives per-job presigned URLs rather than a fixed bucket origin.
 */
export function contentSecurityPolicy({ jobApiUrl, webSocketUrl, oidcIssuer, development = false }) {
    const connectSources = new Set([
        "'self'",
        'https://*.s3.amazonaws.com',
        // smplr fetches the FluidR3 guitar and bass banks as data through
        // fetch(), so this belongs in connect-src rather than script-src.
        'https://gleitz.github.io',
        'https://smpldsnds.github.io',
    ]);
    const jobApiOrigin = configuredOrigin(jobApiUrl, new Set(['https:']));
    const webSocketOrigin = configuredOrigin(webSocketUrl, new Set(['wss:']));
    // The local Keycloak issuer uses HTTP only because it is mapped to the Mac
    // loopback interface for this learning cluster. Production deployment must
    // use HTTPS and a separately reviewed CSP policy.
    const resolvedOidcOrigin = configuredOrigin(oidcIssuer, new Set(['http:', 'https:']));
    if (jobApiOrigin) connectSources.add(jobApiOrigin);
    if (webSocketOrigin) connectSources.add(webSocketOrigin);
    if (resolvedOidcOrigin) connectSources.add(resolvedOidcOrigin);
    if (development) {
        connectSources.add('http://localhost:*');
        connectSources.add('ws://localhost:*');
    }

    return [...commonDirectives, `connect-src ${[...connectSources].join(' ')}`].join('; ');
}
