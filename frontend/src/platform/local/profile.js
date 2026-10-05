/** Select local service wording without duplicating the processing workspace. */
export const profile = Object.freeze({
    id: 'local',
    authKind: 'oidc',
    requiresWebSocket: false,
    tutorialStorageKey: 'clouddsp.local.welcomeTutorial.seen.v1',
    messages: Object.freeze({
        uploadPending: 'Upload received. Verifying and queuing stem separation…',
        stemProcessing: 'Separating the audio into stems…',
        midiProcessing: 'Stems are ready. Extracting MIDI…',
        uploading: 'Uploading audio to local storage…',
        uploadFailed: (status) => `Audio upload failed (${status}). The file may exceed the 256 MiB limit or the secure upload form may have expired.`,
        signInStarting: 'Redirecting to local Keycloak…',
        signedIn: 'Signed in. Select an audio file to begin.',
        sessionRestoreFailed: 'Could not restore OIDC session:',
        sessionRestoreError: 'Could not complete Keycloak sign-in.',
        artifactStemLabel: 'Private stem links',
        artifactMidiLabel: 'Private MIDI links',
        configurationWarning: 'Set VITE_JOB_API_URL before using the processing workspace.',
    }),
});
