# Browser Transport Performance and Memory

## Why a short project could consume gigabytes

A four-minute history job can create several independent kinds of browser
allocation. Looking only at a Timeline event count is misleading: many Paint
events are expected while a timeline moves, but a DAW should not rebuild every
MIDI note or allocate every instrument sample on each update.

Before this design, CloudDSP had five compounding costs:

1. The Web Audio transport called React `setProgress()` once per animation
   frame. That re-rendered the workspace, every MIDI scheduler, and the
   timeline while playback was active.
2. Each MIDI scheduler flattened and scanned all notes for its track whenever
   that visual progress value changed. The total work grew with
   `all notes × active MIDI tracks × display refresh rate`.
3. The main timeline mounted one DOM node per MIDI note, including per-note
   shadows, even when nearly all notes were far outside the viewport. A dense
   Basic Pitch or ADTOF result can have many thousands of nodes.
4. MIDI parsing eagerly constructed smplr instruments as every MIDI file
   arrived, even when the user never enabled MIDI playback. Sampled piano and
   soundfont instruments allocate native decoded audio buffers. Instruments
   were previously stopped but not disposed when a job changed or MIDI was
   disabled.
5. A job snapshot generates new presigned query strings even when the MIDI S3
   object is unchanged. The MIDI loader compared the complete URL, so polling
   could fetch and parse the same artifact again. It also retained a second
   full Tone.js MIDI graph solely for the Revert button.

Decoded source/stem audio also has a real, linear cost. An `AudioBuffer` uses
32-bit PCM:

```text
bytes = seconds × sample rate × channel count × 4
```

At 44.1 kHz stereo, a four-minute track is about 80.7 MiB. One original plus
six stems is therefore about 565 MiB before decoder overhead, instrument
samples, MIDI data, DOM/layout memory, browser caches, and temporary encoded
buffers. That is significant, but it does not by itself explain an 8 GiB Safari
process. The eager instruments and dense note DOM were the more likely
unbounded multipliers.

## Transport and rendering design

The shared `AudioContext` remains the source of truth for synchronized audio.
All source/stem `AudioBufferSourceNode`s are scheduled against the same future
context time. The UI is now deliberately separate from that real-time clock:

```text
AudioContext clock
       │
       ├── AudioBufferSourceNodes and GainNodes
       ├── transportRef (position, offset, start time, rate, revision)
       ├── direct requestAnimationFrame playhead transform
       └── MIDI look-ahead scheduler

React state (one-Hz position/readout updates, controls, edits, status)
```

`AudioMultiTrackPlayer` updates `transportRef` every frame from
`AudioContext.currentTime`, but commits its React `progress` state only once a
second; the visible readout displays whole seconds. This means a delayed Safari
frame advances the next playhead draw to
the correct absolute audio position; it does not attempt to replay missed
visual positions.

`useTransportPlayhead` updates only the main playhead line and ruler triangle
with `translate3d(...)`. It does not alter `left`, rebuild React children, or
interpret a late frame as a manual seek. Once the playhead crosses the viewport
center, it deterministically follows the timeline by setting `scrollLeft` only
when the target changes by at least two pixels. The obsolete heuristic scroll
state machine must not be reintroduced.

The popup editor uses the same clock-driven transform and does not animate the
occluded main workspace at the same time. It is mounted only while open, so a
closed editor owns no keyboard handler, selection state, or note-render index.
While it is open, the occluded workspace MIDI grid is also unmounted; the
browser never updates two dense note surfaces for one edit.

## Static timeline and MIDI scheduling

`TrackGrid`, `TimelineRuler`, and the popup editor now receive a
tile-quantized visible range. Only nearby bars and notes are mounted, with a
modest overscan region. Each renderer keeps a time-sorted index and uses binary
search to select the visible window while preserving original array indices for
MIDI edits. Track rows, ruler marks, and note elements are memoized; normal
note rendering avoids paint-heavy shadows. A horizontal scroll can occur at
display rate, but React does not even enqueue a viewport update until it crosses
a tile boundary. Automatic scroll advances in two-pixel steps while the
playhead itself remains a smooth compositor transform.

The main workspace can mount only after a job has returned playable artifacts.
For that lifecycle, `useTimelineViewport` returns a callback ref as well as the
visible range. Attach that callback to the actual scroll element rather than
only assigning a mutable ref: an assignment to `ref.current` does not rerun a
React effect, whereas the callback establishes the scroll and resize observers
when the element appears. This keeps the virtual range advancing past the first
tile for delayed job results, history hydration, and editor remounts.

`useMidiSynth` no longer follows visual progress. For each active MIDI track it
builds one sorted note list when MIDI data changes. A 40 ms scheduler reads the
audio-clock transport, binary-searches after a play/seek/BPM/cycle revision,
and advances a cursor through only notes due in a 250 ms look-ahead window. The
MIDI scheduler retains immediate mute, solo, drum-voice, tempo, pause, and seek
behavior by clearing and rebuilding only that short scheduled window.

## Memory lifecycle

Audio loading is serialized to one fetch-and-decode operation at a time.
`decodeAudioData` can temporarily require both encoded bytes and decoded PCM,
so this bounds the decode peak. One persistent queue is shared across snapshot
updates: a new stem cannot create a second `concurrency = 1` worker while an
older snapshot is still decoding. The transport:

- aborts stale history-job requests before decoding;
- releases encoded `ArrayBuffer` references after decoding;
- uses `fetch(..., { cache: 'no-store' })` because the current decoded buffer
  is the deliberate cache, not a duplicate browser HTTP-media cache entry;
- drops stopped source-node buffer references and disconnects removed tracks;
- clears current-job buffers at a durable workspace boundary;
- closes the AudioContext only when the workspace unmounts; and
- exposes `audioMemoryMetrics` with decoded bytes, temporary bytes, peak
  decoded bytes, and track count.

MIDI is keyed by stable S3 host/path, not an expiring presigned URL, and is
loaded and parsed through one persistent queue. The loader parses each source
once, retains one editable Tone.js graph, and stores only compact immutable MIDI
bytes for Revert/Undo. It therefore cannot reparse unchanged artifacts on every
poll or retain two full note graphs for every stem. Its raw fetch also bypasses
the HTTP memory cache once the compact application representation exists.

A sampled instrument is created only when its MIDI mode is enabled or its
editor opens. Instrument construction itself is serialized because smplr begins
sample fetch/decode at construction time. Piano loading is limited to the
pitches used by that track, and the guitar/bass soundfont uses smplr's smaller
FluidR3 kit rather than its heavier default. Disabling MIDI disposes that
instrument; changing jobs disposes all old instruments. Use smplr `dispose()`
rather than only `stop()`, because disposal tears down its output graph,
scheduler, and sample-buffer ownership.

## Debugging Safari measurements

Use Safari Web Inspector Timelines to compare total duration and longest frame,
not merely the number of event rows. Record these values during a ten-second
playback test:

1. maximum JavaScript time per frame;
2. total and longest Layout/Paint time;
3. DOM-node count while the timeline is at the beginning, middle, and end;
4. `CloudDSP` audio-memory console metrics after each stem decodes;
5. one `Starting S3 MIDI download` / `MIDI parse complete` pair per stable
   artifact path—not a new pair for every polling response;
6. memory before and after enabling one MIDI track, then after disabling it;
   and
7. memory after opening a different history job.

At 60 Hz, no frame should routinely exceed 16.7 ms; at 120 Hz, the practical
budget is 8.3 ms. The playhead remains correct if a frame is missed, but
repeated long frames still indicate work that should be removed or deferred.

## Remaining scalability boundary

The current design intentionally retains all current source/stem PCM to keep
sample-accurate synchronized playback after seek, pause, and tempo changes.
It is appropriate for normal song-length jobs, but decoded audio still grows
linearly with duration, sample rate, channels, and stem count. If CloudDSP must
support long-form recordings reliably, the next architectural change is a
chunked/streaming transport or server-generated lower-resolution preview stems;
do not silently restore independent HTML media elements, since that reintroduces
cross-track Safari synchronization drift.
