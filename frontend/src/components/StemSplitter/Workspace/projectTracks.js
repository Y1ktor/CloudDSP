/** Assemble source and stem rows without changing their artifact references. */
export function getTracksToRender(file, sourceUrl, stemUrls) {
    const tracks = {};
    if (file) tracks.Original = null;
    else if (sourceUrl) tracks.Original = sourceUrl;
    // Incomplete legacy jobs may predate the original-upload URL contract.
    else if (!file && stemUrls && Object.keys(stemUrls).length > 0) {
        tracks.Original = stemUrls[Object.keys(stemUrls)[0]];
    }
    if (stemUrls) Object.assign(tracks, stemUrls);
    return tracks;
}

/** Parsed MIDI wins over backend state; a received URL is still loading. */
export function getMidiStatusByTrack(stemUrls, midiUrls, midiStates, parsedMidiStems) {
    return Object.keys(stemUrls || {}).reduce((statuses, trackName) => {
        if (parsedMidiStems[trackName]) {
            statuses[trackName] = 'ready';
        } else if (midiStates?.[trackName]?.status === 'failed') {
            statuses[trackName] = 'failed';
        } else if (midiUrls?.[trackName]) {
            statuses[trackName] = 'loading';
        } else if (trackName === 'Original') {
            statuses[trackName] = 'unavailable';
        } else {
            statuses[trackName] = 'processing';
        }
        return statuses;
    }, {});
}

/** Expand an ADTOF parent row into the canonical kit's MIDI-only lanes. */
export function getTimelineRows(tracksToRender, parsedMidiStems, expandedDrumTracks, drumVoices) {
    return Object.entries(tracksToRender).flatMap(([trackName, url]) => {
        const hasDrumSubtracks = trackName === 'drums' && parsedMidiStems[trackName]?.isAdtofDrum;
        const isDrumExpanded = hasDrumSubtracks && Boolean(expandedDrumTracks[trackName]);
        const stemRow = {
            id: trackName,
            trackName,
            url,
            kind: 'stem',
            hasDrumSubtracks,
            isDrumExpanded,
        };
        if (!hasDrumSubtracks || !isDrumExpanded) return [stemRow];

        return [
            stemRow,
            ...drumVoices.map((drumVoice) => ({
                id: `drums:${drumVoice.id}`,
                trackName: 'drums',
                kind: 'drum-lane',
                drumVoice,
            })),
        ];
    });
}
