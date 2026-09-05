const { Midi } = require('@tonejs/midi');

const midi = new Midi();
const track = midi.addTrack();
track.addNote({
    midi: 60,
    time: 0,
    duration: 1
});

const arr = midi.toArray();

const midi2 = new Midi(arr);
console.log(midi2.tracks[0].notes.length);
midi2.tracks[0].notes = midi2.tracks[0].notes.filter(n => false);
console.log(midi2.tracks[0].notes.length);

const arr2 = midi2.toArray();
console.log("arr2 length:", arr2.length);

const midi3 = new Midi(arr2);
console.log(midi3.tracks[0].notes.length);
