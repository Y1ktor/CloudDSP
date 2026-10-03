import { useMemo, useState } from 'react';
import { getProjectDownloadArtifacts, projectFolderName } from './projectDownloads';

/** Own download choices independently of playback and editable MIDI state. */
export function useProjectDownloads({ file, fileName, sourceUrl, stemUrls, midiUrls }) {
    const [isDownloadOpen, setIsDownloadOpen] = useState(false);
    const [selectedDownloadArtifactIds, setSelectedDownloadArtifactIds] = useState(new Set());

    const downloadRootFolderName = useMemo(
        () => projectFolderName(file?.name || fileName),
        [file, fileName],
    );
    const downloadArtifacts = useMemo(
        () => getProjectDownloadArtifacts(downloadRootFolderName, file, fileName, sourceUrl, stemUrls, midiUrls),
        [downloadRootFolderName, file, fileName, sourceUrl, stemUrls, midiUrls],
    );

    const openDownloadPopup = () => {
        if (downloadArtifacts.length === 0) return;
        setSelectedDownloadArtifactIds(new Set(downloadArtifacts.map((artifact) => artifact.id)));
        setIsDownloadOpen(true);
    };

    return {
        isDownloadOpen,
        setIsDownloadOpen,
        selectedDownloadArtifactIds,
        setSelectedDownloadArtifactIds,
        downloadRootFolderName,
        downloadArtifacts,
        openDownloadPopup,
    };
}
