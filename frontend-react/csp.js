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

function cognitoOrigin(userPoolId) {
    const region = typeof userPoolId === 'string' ? userPoolId.split('_', 1)[0] : '';
    return /^[a-z]{2}(?:-gov)?-[a-z]+-\d+$/.test(region)
        ? `https://cognito-idp.${region}.amazonaws.com`
        : null;
}

/**
 * Build a policy from public build-time endpoints rather than permitting every
 * API Gateway or Cognito account. S3 remains a service wildcard because the
 * browser receives per-job presigned URLs rather than a fixed bucket origin.
 */
export function contentSecurityPolicy({ jobApiUrl, webSocketUrl, userPoolId, development = false }) {
    const connectSources = new Set([
        "'self'",
        'https://*.s3.amazonaws.com',
        'https://smpldsnds.github.io',
    ]);
    const jobApiOrigin = configuredOrigin(jobApiUrl, new Set(['https:']));
    const webSocketOrigin = configuredOrigin(webSocketUrl, new Set(['wss:']));
    const resolvedCognitoOrigin = cognitoOrigin(userPoolId);
    if (jobApiOrigin) connectSources.add(jobApiOrigin);
    if (webSocketOrigin) connectSources.add(webSocketOrigin);
    if (resolvedCognitoOrigin) connectSources.add(resolvedCognitoOrigin);
    if (development) {
        connectSources.add('http://localhost:*');
        connectSources.add('ws://localhost:*');
    }

    return [...commonDirectives, `connect-src ${[...connectSources].join(' ')}`].join('; ');
}
