/** Builds cloud and local Content Security Policy directives from public endpoint origins. */
import { URL } from 'node:url';

const commonDirectives = [
    "default-src 'self'",
    "base-uri 'self'",
    "object-src 'none'",
    "script-src 'self'",
    "style-src 'self' 'unsafe-inline'",
    "img-src 'self' data: blob:",
    "font-src 'self' data:",
    "worker-src 'self' blob:",
    "frame-src 'none'",
    "frame-ancestors 'none'",
    "form-action 'self'",
];

function configuredOrigin(value, permittedProtocols) {
    if (!value) return null;
    try {
        const parsed = new URL(value);
        return permittedProtocols.has(parsed.protocol) && !parsed.username && !parsed.password
            ? parsed.origin : null;
    } catch {
        return null;
    }
}

function cognitoOrigin(userPoolId) {
    const region = typeof userPoolId === 'string' ? userPoolId.split('_', 1)[0] : '';
    return /^[a-z]{2}(?:-gov)?-[a-z]+-\d+$/.test(region)
        ? `https://cognito-idp.${region}.amazonaws.com` : null;
}

/**
 * Each artifact receives only its active deployment's reviewed browser origins.
 * Cloud retains its S3 service wildcard because private presigned artifact URLs
 * name different buckets; local names exactly one MinIO origin and has no need
 * to contact the cloud sample hosts or Cognito. Explicit local HTTP/WS endpoints
 * support the existing loopback k3d profile without broad protocol wildcards.
 */
export function contentSecurityPolicy({
    profile = 'cloud', jobApiUrl, webSocketUrl, userPoolId,
    objectStorageUrl, oidcIssuer, development = false,
}) {
    if (!['cloud', 'local'].includes(profile)) {
        throw new Error('Unknown CloudDSP Content-Security-Policy profile.');
    }
    const cloud = profile === 'cloud';
    const connectSources = new Set(["'self'"]);
    const mediaSources = new Set(["'self'", 'blob:']);
    if (cloud) {
        connectSources.add('https://*.s3.amazonaws.com');
        connectSources.add('https://gleitz.github.io');
        connectSources.add('https://smpldsnds.github.io');
        mediaSources.add('https://*.s3.amazonaws.com');
        mediaSources.add('https://smpldsnds.github.io');
        const identityOrigin = cognitoOrigin(userPoolId);
        if (identityOrigin) connectSources.add(identityOrigin);
    } else {
        const objectOrigin = configuredOrigin(objectStorageUrl, new Set(['http:', 'https:']));
        const identityOrigin = configuredOrigin(oidcIssuer, new Set(['http:', 'https:']));
        if (objectOrigin) {
            connectSources.add(objectOrigin);
            mediaSources.add(objectOrigin);
        }
        if (identityOrigin) connectSources.add(identityOrigin);
    }
    const apiOrigin = configuredOrigin(jobApiUrl, new Set(cloud ? ['https:'] : ['http:', 'https:']));
    const socketOrigin = configuredOrigin(webSocketUrl, new Set(cloud ? ['wss:'] : ['ws:', 'wss:']));
    if (apiOrigin) connectSources.add(apiOrigin);
    if (socketOrigin) connectSources.add(socketOrigin);
    if (development) {
        connectSources.add('http://localhost:*');
        connectSources.add('ws://localhost:*');
    }
    return [...commonDirectives, `media-src ${[...mediaSources].join(' ')}`,
        `connect-src ${[...connectSources].join(' ')}`].join('; ');
}
