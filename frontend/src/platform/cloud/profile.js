/** Cloud deployment wording and browser preferences shared by the main UI. */
export const profile = Object.freeze({
    id: 'cloud',
    authKind: 'cognito',
    requiresWebSocket: true,
    tutorialStorageKey: 'clouddsp.welcomeTutorial.seen.v1',
    messages: Object.freeze({
        uploadPending: 'Upload complete. Waiting for AWS Batch capacity…',
        stemProcessing: 'AWS Batch is separating stems…',
        midiProcessing: 'Stems are ready. MIDI extraction is still running…',
        uploading: 'Uploading audio to the secure job location…',
        uploadFailed: (status) => `S3 upload failed (${status}). The file may exceed the 256 MiB limit or the upload policy may have expired.`,
        signInStarting: '',
        signedIn: 'Signed in. Select an audio file to begin.',
        sessionRestoreFailed: 'Could not restore Cognito session:',
        sessionRestoreError: '',
        artifactStemLabel: 'Signed S3 stem URLs',
        artifactMidiLabel: 'Signed S3 MIDI URLs',
        configurationWarning: 'Set VITE_JOB_API_URL and VITE_WEBSOCKET_URL before using the processing workspace.',
    }),
});
