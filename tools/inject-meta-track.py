#!/usr/bin/env python3
"""Add a timed-metadata track to a recorded fMP4 stream, offline.

The question this exists to answer: does a browser's MediaSource tolerate an
init segment that declares a `meta` handler track it cannot decode? majestic
wants to write detection boxes into its recordings as an ISO 14496-12 timed
metadata track, and the WebUI's recordings player appends those very fragments
to a MediaSource -- so "browsers ignore tracks they do not support" has to be
a measurement rather than an assumption before the muxer is changed.

It is done offline, to an already-published recording from a camera with no
lens, for the same two reasons the prft boxes in web/stream.bin were: the
bytes do not depend on the picture, and no new footage has to be recorded or
published to ask the question.

Written before the C, deliberately. Laying the boxes out by hand here is what
turns the format into something that can be looked at and argued with, and
the fixture it produces stays as a regression afterwards.
"""
import json
import struct
import sys

# --- box plumbing --------------------------------------------------------

def box(kind: bytes, payload: bytes) -> bytes:
    return struct.pack('>I', len(payload) + 8) + kind + payload

def full_box(kind: bytes, version: int, flags: int, payload: bytes) -> bytes:
    return box(kind, bytes([version]) + struct.pack('>I', flags)[1:] + payload)

def children(buf: bytes, start: int, end: int):
    """Immediate children of a container, as (type, start, end)."""
    out = []
    while start + 8 <= end:
        size = struct.unpack('>I', buf[start:start + 4])[0]
        kind = buf[start + 4:start + 8]
        if size == 0:
            size = end - start
        if size < 8 or start + size > end:
            raise ValueError(f'bad box {kind!r} size {size} at {start}')
        out.append((kind, start, start + size))
        start += size
    return out

def find(buf, start, end, kind):
    for k, s, e in children(buf, start, end):
        if k == kind:
            return s, e
    raise KeyError(f'{kind!r} not found in [{start},{end})')

def resize(buf: bytearray, start: int, delta: int):
    """Grow the box header at `start` by `delta` bytes."""
    size = struct.unpack('>I', bytes(buf[start:start + 4]))[0]
    buf[start:start + 4] = struct.pack('>I', size + delta)

# --- the metadata track --------------------------------------------------

META_TRACK_ID = 3      # video is 1, audio would be 2
MIME = b'application/json'

def mett_sample_entry() -> bytes:
    """TextMetaDataSampleEntry, ISO/IEC 14496-12 12.3.3.2.

        class TextMetaDataSampleEntry() extends MetaDataSampleEntry('mett') {
            utf8string content_encoding;   // optional
            utf8string mime_format;
            BitRateBox();                  // optional
        }

    The EMPTY content_encoding is written rather than omitted. Both fields are
    NUL-terminated strings with no flag between them, so a reader that expects
    content_encoding and meets the mime first takes the mime as the encoding
    and then runs off the end of the box. Writing the empty string is what
    every muxer that emits mett does.

    No BitRateBox: it is optional and the track's bitrate is a few hundred
    bits a second, which is not a number anything acts on.
    """
    payload = b'\x00' * 6                 # SampleEntry.reserved
    payload += struct.pack('>H', 1)       # data_reference_index
    payload += b'\x00'                    # content_encoding: ""
    payload += MIME + b'\x00'             # mime_format
    return box(b'mett', payload)

def meta_trak(timescale: int) -> bytes:
    # DURATION IS ZERO, in both tkhd and mdhd, because this is a fragmented
    # file: the duration is not known until the fragments are, and every other
    # track in these recordings says 0 for the same reason.
    #
    # Writing a real number here is what made Safari on macos-14 refuse the
    # whole stream with MEDIA_ERR_DECODE at t=0. Two mistakes at once, and the
    # second is the instructive one: tkhd.duration is in the MOVIE timescale
    # (mvhd, 1000 in both these recordings) while mdhd.duration is in the
    # MEDIA timescale (10240 and 1000000). Putting media ticks in the tkhd
    # declared a 61-second metadata track on a 6-second movie. Chrome played
    # it anyway; Safari did not, and Safari was right.
    tkhd = full_box(b'tkhd', 0, 0x000003,  # enabled | in_movie, NOT in_preview
        struct.pack('>IIIII', 0, 0, META_TRACK_ID, 0, 0)
        + b'\x00' * 8                      # reserved
        + struct.pack('>hhhh', 0, 0, 0, 0) # layer, alt group, volume, reserved
        + struct.pack('>9i', 0x10000, 0, 0, 0, 0x10000, 0, 0, 0, 0x40000000)
        + struct.pack('>II', 0, 0))        # width, height: a metadata track
                                           # has no picture

    mdhd = full_box(b'mdhd', 0, 0,
        struct.pack('>IIII', 0, 0, timescale, 0)
        + struct.pack('>HH', 0x55C4, 0))   # 'und', pre_defined

    # handler_type 'meta' -- ISO/IEC 14496-12 12.3.2. Not the MetaBox that
    # shares the four-CC; same letters, different question.
    hdlr = full_box(b'hdlr', 0, 0,
        b'\x00' * 4 + b'meta' + b'\x00' * 12 + b'Analytics\x00')

    # Null Media Header, 8.4.5.5: a FullBox with no fields. 12.3.2 names it as
    # the media information header for a metadata track -- there is no
    # "metadata media header" box.
    nmhd = full_box(b'nmhd', 0, 0, b'')

    dref = full_box(b'dref', 0, 0,
        struct.pack('>I', 1) + full_box(b'url ', 0, 1, b''))
    dinf = box(b'dinf', dref)

    stsd = full_box(b'stsd', 0, 0, struct.pack('>I', 1) + mett_sample_entry())
    stbl = box(b'stbl', stsd
        + full_box(b'stts', 0, 0, struct.pack('>I', 0))
        + full_box(b'stsc', 0, 0, struct.pack('>I', 0))
        + full_box(b'stsz', 0, 0, struct.pack('>II', 0, 0))
        + full_box(b'stco', 0, 0, struct.pack('>I', 0)))

    minf = box(b'minf', nmhd + dinf + stbl)
    mdia = box(b'mdia', mdhd + hdlr + minf)
    return box(b'trak', tkhd + mdia)

def meta_trex() -> bytes:
    # default_sample_flags 0x02000000: sample_depends_on = 2, "this is a sync
    # sample". True of every metadata sample, and the opposite of video's
    # default -- a metadata track whose samples defaulted to non-sync would be
    # unseekable.
    return full_box(b'trex', 0, 0,
        struct.pack('>IIIII', META_TRACK_ID, 1, 0, 0, 0x02000000))

def meta_traf(decode_time: int, duration: int, sample: bytes,
              data_offset: int) -> bytes:
    tfhd = full_box(b'tfhd', 0, 0x020000 | 0x000010 | 0x000020,
        struct.pack('>I', META_TRACK_ID)   # default-base-is-moof
        + struct.pack('>I', len(sample))   # default_sample_size
        + struct.pack('>I', 0x02000000))   # default_sample_flags: sync
    tfdt = full_box(b'tfdt', 1, 0, struct.pack('>Q', decode_time))
    # flags: data-offset-present | sample-duration-present
    trun = full_box(b'trun', 0, 0x000001 | 0x000100,
        struct.pack('>I', 1)               # sample_count
        + struct.pack('>i', data_offset)
        + struct.pack('>I', duration))
    return box(b'traf', tfhd + tfdt + trun)

# --- rewriting -----------------------------------------------------------

def patch_init(init: bytes, timescale: int) -> bytes:
    buf = bytearray(init)
    moov_s, moov_e = find(buf, 0, len(buf), b'moov')

    # next_track_ID is one past the largest this moov can contain.
    mvhd_s, mvhd_e = find(buf, moov_s + 8, moov_e, b'mvhd')
    buf[mvhd_e - 4:mvhd_e] = struct.pack('>I', META_TRACK_ID + 1)

    mvex_s, mvex_e = find(buf, moov_s + 8, moov_e, b'mvex')

    trak = meta_trak(timescale)
    trex = meta_trex()

    # The trak goes before mvex, the trex inside it. Track order in the moov
    # has to match traf order in every moof: readers that walk a fragment by
    # position -- and the CENC sealing in majestic is one -- depend on it.
    out = bytearray()
    out += buf[:mvex_s]
    out += trak
    out += buf[mvex_s:mvex_e]
    out += buf[mvex_e:]

    # Insert the trex inside the mvex we just copied, then fix both sizes.
    mvex_at = mvex_s + len(trak)
    mvex_size = struct.unpack('>I', bytes(out[mvex_at:mvex_at + 4]))[0]
    out[mvex_at + mvex_size:mvex_at + mvex_size] = trex
    resize(out, mvex_at, len(trex))
    resize(out, moov_s, len(trak) + len(trex))
    return bytes(out)

def patch_fragment(frag: bytes, sample: bytes, duration: int) -> bytes:
    """Insert a metadata traf and append its bytes to the mdat.

    The fiddly part is that data_offset is relative to the start of the
    enclosing moof (tfhd's default-base-is-moof flag), so growing the moof
    moves the mdat payload underneath every trun that already exists.
    """
    buf = bytearray(frag)
    top = children(buf, 0, len(buf))
    moof_s = mdat_s = mdat_e = None
    for kind, s, e in top:
        if kind == b'moof':
            moof_s, moof_e = s, e
        elif kind == b'mdat':
            mdat_s, mdat_e = s, e
    if moof_s is None or mdat_s is None:
        raise ValueError('fragment is not [moof][mdat]')

    # The video track's decode time, so the metadata sample names the same
    # instant as the picture it describes.
    traf_s, traf_e = find(buf, moof_s + 8, moof_e, b'traf')
    tfdt_s, tfdt_e = find(buf, traf_s + 8, traf_e, b'tfdt')
    version = buf[tfdt_s + 8]
    dt = struct.unpack('>Q', bytes(buf[tfdt_s + 12:tfdt_s + 20]))[0] \
        if version == 1 else \
        struct.unpack('>I', bytes(buf[tfdt_s + 12:tfdt_s + 16]))[0]

    mdat_payload_len = (mdat_e - mdat_s) - 8
    old_moof_size = moof_e - moof_s

    # Built once with a placeholder to learn its length, then again with the
    # offset that length implies. The offset depends on the size of the thing
    # being measured, which is the only reason this is done twice.
    probe = meta_traf(dt, duration, sample, 0)
    new_moof_size = old_moof_size + len(probe)
    data_offset = new_moof_size + 8 + mdat_payload_len
    traf = meta_traf(dt, duration, sample, data_offset)
    assert len(traf) == len(probe), 'traf length must not depend on the offset'

    # Every existing trun points past a moof that has just grown.
    for kind, s, e in children(buf, moof_s + 8, moof_e):
        if kind != b'traf':
            continue
        for k2, s2, e2 in children(buf, s + 8, e):
            if k2 != b'trun':
                continue
            flags = struct.unpack('>I', b'\x00' + bytes(buf[s2 + 9:s2 + 12]))[0]
            if not (flags & 0x000001):
                continue
            at = s2 + 16
            off = struct.unpack('>i', bytes(buf[at:at + 4]))[0]
            buf[at:at + 4] = struct.pack('>i', off + len(traf))

    out = bytearray()
    out += buf[:moof_e]
    out[moof_s:moof_s] = b''            # no-op, kept for symmetry
    out[moof_e:moof_e] = traf           # the traf goes last inside the moof
    resize(out, moof_s, len(traf))

    # The mdat, with the sample bytes appended after the video's.
    out += buf[mdat_s:mdat_e]
    out += sample
    resize(out, mdat_s + len(traf), len(sample))

    # Anything after the mdat (there is none today) would follow here.
    out += buf[mdat_e:]
    return bytes(out)

def main():
    src, dst = sys.argv[1], sys.argv[2]
    manifest = json.load(open(src + '.json' if not src.endswith('.bin')
                              else src[:-4] + '.json'))
    data = open(src, 'rb').read()

    timescale = manifest['timescale']
    frags = manifest['fragments']
    duration = frags[1]['dts'] - frags[0]['dts'] if len(frags) > 1 else 50000

    out = bytearray()
    out += patch_init(data[:manifest['initLength']], timescale)
    new_init_len = len(out)

    new_frags = []
    for i, f in enumerate(frags):
        frag = data[f['offset']:f['offset'] + f['length']]
        # A plausible detection, so the sample is the size a real one would
        # be rather than a token. Two boxes, which is what a scene with
        # something moving in it typically produces.
        sample = json.dumps({
            's': 0, 'n': 2,
            'r': [[600 + (i % 40) * 10, 400, 240, 180, 0, 0],
                  [1800, 900 + (i % 20) * 5, 120, 90, 0, 0]],
        }, separators=(',', ':')).encode()
        patched = patch_fragment(frag, sample, duration)
        new_frags.append(dict(f, offset=len(out), length=len(patched)))
        out += patched

    open(dst, 'wb').write(out)
    m = dict(manifest, initLength=new_init_len, fragments=new_frags,
             source=manifest['source'] +
             '; a meta/mett timed-metadata track (application/json) inserted '
             'per fragment offline by tools/inject-meta-track.py')
    json.dump(m, open(dst[:-4] + '.json', 'w'))
    print(f'{dst}: init {new_init_len} B, {len(new_frags)} fragments, '
          f'{len(out)} B total (was {len(data)} B)')

if __name__ == '__main__':
    main()
