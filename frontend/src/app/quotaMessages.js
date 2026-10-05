/** Format quota reset times and submission errors for direct uploads and media links. */
export function quotaResetMessage(resetsAt, now = Date.now()) {
    const resetTime = new Date(resetsAt).getTime();
    if (!Number.isFinite(resetTime)) return 'Resets at midnight UTC.';
    const remainingMs = Math.max(0, resetTime - now);
    if (remainingMs < 60_000) return 'Resets shortly.';
    const hours = Math.ceil(remainingMs / 3_600_000);
    return `Resets in ${hours} ${hours === 1 ? 'hour' : 'hours'}.`;
}

export function quotaErrorMessage(payload, quotaKey, fallback) {
    const quota = payload?.quota?.[quotaKey];
    if (!quota || Number(payload?.statusCode) !== 429) return payload?.error || fallback;
    const label = quotaKey === 'direct_uploads' ? 'local-upload' : 'URL-import';
    return `Daily ${label} quota reached (${quota.used}/${quota.limit}). ${quotaResetMessage(payload.quota.resets_at)}`;
}
