// strip(inject(recording)) must be the recording.
//
// Two implementations, written independently and in different languages, on
// real files: tools/inject-meta-track.py puts the track in, and the WebUI's
// own stripper takes it back out. If the result is not the original then one
// of them is wrong, and this says which fragment first.
//
// A stronger question than "does it play". A container can be malformed in
// ways every decoder tolerates until one does not — which is exactly what
// this harness found the first time it asked.
import fs from 'node:fs';
import path from 'node:path';
import { createRequire } from 'node:module';

const require = createRequire(import.meta.url);
const web = path.join(path.dirname(new URL(import.meta.url).pathname), '..', 'web');
const M = require(path.join(web, 'mp4meta.js'));

const same = (a, b) => a.length === b.length && a.every((v, i) => v === b[i]);
let bad = 0;

for (const base of process.argv.slice(2)) {
	const orig = new Uint8Array(fs.readFileSync(path.join(web, base + '.bin')));
	const om = JSON.parse(fs.readFileSync(path.join(web, base + '.json')));
	const meta = new Uint8Array(fs.readFileSync(path.join(web, base + '-meta.bin')));
	const mm = JSON.parse(fs.readFileSync(path.join(web, base + '-meta.json')));

	// The init is compared with next_track_ID masked out, and only that.
	// It only has to EXCEED every track_ID in use, so a value left high after
	// a track is removed is legal — and majestic writes the same number
	// whether or not the track is there, so a stripper that lowered it would
	// produce a file unlike one the camera wrote without it. The two fixtures
	// here were written by older muxers that said 2.
	const si = Uint8Array.from(M.stripInit(meta.subarray(0, mm.initLength)));
	const oi = Uint8Array.from(orig.subarray(0, om.initLength));
	let initNote = '';
	if (si.length === oi.length) {
		const diff = [];
		for (let i = 0; i < si.length; i++) if (si[i] !== oi[i]) diff.push(i);
		const mvhd = si.indexOf(0x6d);   // located properly below
		const at = findNextTrackId(si);
		const onlyNextTrackId = at >= 0 && diff.length && diff.length <= 4 &&
			diff.every((i) => i >= at && i < at + 4);
		if (!diff.length) initNote = 'identical';
		else if (onlyNextTrackId) initNote = 'identical but for next_track_ID (legal, see above)';
		else { initNote = `DIFFERS at ${diff.length} bytes, first ${diff[0]}`; bad++; }
		void mvhd;
	} else { initNote = `DIFFERS in length: ${si.length} vs ${oi.length}`; bad++; }

	let ok = 0, differ = 0, firstBad = -1;
	for (let i = 0; i < mm.fragments.length; i++) {
		const f = mm.fragments[i], g = om.fragments[i];
		const s = M.stripFragment(meta.subarray(f.offset, f.offset + f.length));
		const o = orig.subarray(g.offset, g.offset + g.length);
		if (same(s, o)) ok++;
		else { differ++; if (firstBad < 0) firstBad = i; }
	}
	if (differ) bad++;
	console.log(`${base}: init ${initNote}; fragments ${ok}/${mm.fragments.length} identical` +
		(differ ? `, ${differ} differ, first at ${firstBad}` : ''));
}

// Byte offset of mvhd's next_track_ID: the box body starts 8 in, and the
// field is the last of a 100-byte v0 body.
function findNextTrackId(u8) {
	for (let i = 0; i + 8 <= u8.length; i++)
		if (u8[i] === 0x6d && u8[i + 1] === 0x76 && u8[i + 2] === 0x68 && u8[i + 3] === 0x64)
			return i + 4 + 96;
	return -1;
}

process.exit(bad ? 1 : 0);
