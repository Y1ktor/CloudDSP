export function filenameFromDownloadUrl(url, fallbackName) {
    try {
        const pathname = new URL(url).pathname;
        const filename = pathname.split('/').filter(Boolean).at(-1);
        return filename ? decodeURIComponent(filename) : fallbackName;
    } catch {
        return fallbackName;
    }
}

export function projectFolderName(filename) {
    const withoutExtension = String(filename || '').replace(/\.[^./\\]+$/, '').trim();
    const safeName = withoutExtension
        .replace(/[<>:"/\\|?*]/g, '_')
        .replace(/\p{Cc}/gu, '_');
    return safeName || 'CloudDSP project';
}

/** Preserve source precedence and artifact order for the download selector. */
export function getProjectDownloadArtifacts(rootPath, file, fileName, sourceUrl, stemUrls, midiUrls) {
    const artifacts = [];
    const originalFilename = file?.name || filenameFromDownloadUrl(sourceUrl, fileName || 'original-audio.wav');

    if (file instanceof Blob) {
        artifacts.push({
            id: 'original', group: 'original', filename: originalFilename, file,
            archivePath: `${rootPath}/${originalFilename}`,
        });
    } else if (sourceUrl) {
        artifacts.push({
            id: 'original', group: 'original', filename: originalFilename, url: sourceUrl,
            archivePath: `${rootPath}/${originalFilename}`,
        });
    }

    Object.entries(stemUrls || {}).forEach(([stemName, url]) => {
        if (!url) return;
        const filename = filenameFromDownloadUrl(url, `${stemName}.wav`);
        artifacts.push({
            id: `stem:${stemName}`, group: 'stems', filename, url,
            archivePath: `${rootPath}/stems/${filename}`,
        });
    });
    Object.entries(midiUrls || {}).forEach(([stemName, url]) => {
        if (!url) return;
        const filename = filenameFromDownloadUrl(url, `${stemName}.mid`);
        artifacts.push({
            id: `midi:${stemName}`, group: 'midi', filename, url,
            archivePath: `${rootPath}/midi/${filename}`,
        });
    });
    return artifacts;
}
