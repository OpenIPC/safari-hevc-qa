#!/usr/bin/env python3
"""Split a fragmented MP4 into the manifest shape the harness page replays.

A control fixture is only worth carrying if it can be rebuilt, and the two
synthetic ones here exist to ask container questions without a codec question
riding along -- so the recipe that made them belongs next to them rather than
in somebody's shell history.
"""
import json, struct, sys

def kids(b, s, e):
    out = []
    while s + 8 <= e:
        sz = struct.unpack('>I', b[s:s + 4])[0]
        ty = b[s + 4:s + 8].decode('latin1')
        if sz == 0:
            sz = e - s
        out.append((ty, s, s + sz))
        s += sz
    return out

def find(b, s, e, ty):
    for t, ss, ee in kids(b, s, e):
        if t == ty:
            return ss, ee
    return None

def main():
    src, dst, codec, width, height = sys.argv[1:6]
    d = open(src, 'rb').read()
    top = kids(d, 0, len(d))
    init_len = next(s for t, s, e in top if t == 'moof')

    moov = find(d, 0, init_len, 'moov')
    trak = find(d, moov[0] + 8, moov[1], 'trak')
    mdia = find(d, trak[0] + 8, trak[1], 'mdia')
    minf = find(d, mdia[0] + 8, mdia[1], 'minf')
    stbl = find(d, minf[0] + 8, minf[1], 'stbl')
    stsd = find(d, stbl[0] + 8, stbl[1], 'stsd')
    mdhd = find(d, mdia[0] + 8, mdia[1], 'mdhd')
    timescale = struct.unpack('>I', d[mdhd[0] + 20:mdhd[0] + 24])[0]

    entry = kids(d, stsd[0] + 16, stsd[1])[0]
    if entry[0] in ('avc1', 'avc3'):
        cfg = find(d, entry[1] + 86, entry[2], 'avcC')
        p = d[cfg[0] + 8:cfg[1]]
        codec_string = 'avc1.%02X%02X%02X' % (p[1], p[2], p[3])
    else:
        cfg = find(d, entry[1] + 86, entry[2], 'hvcC')
        p = d[cfg[0] + 8:cfg[1]]
        # RFC 6381 for HEVC, and the compatibility field is the trap: it is
        # the 32 flag bits in REVERSE order, printed as hex with leading
        # zeros dropped. Straight through it reads 60000000 where every real
        # camera reports 6, and Safari would refuse the MIME outright -- the
        # paired control would have caught it, but as the wrong answer to the
        # wrong question.
        compat = struct.unpack('>I', p[2:6])[0]
        rev = int('{:032b}'.format(compat)[::-1], 2)
        tier = 'H' if (p[1] >> 5) & 1 else 'L'
        codec_string = 'hvc1.%d.%X.%s%d.B0' % (p[1] & 0x1f, rev, tier, p[12])

    frags, cur = [], None
    for t, s, e in top:
        if t == 'moof':
            cur = [s, e]
        elif t == 'mdat' and cur:
            cur[1] = e
            frags.append(tuple(cur))
            cur = None

    def dts(fs, fe):
        moof = find(d, fs, fe, 'moof')
        traf = find(d, moof[0] + 8, moof[1], 'traf')
        tfdt = find(d, traf[0] + 8, traf[1], 'tfdt')
        if d[tfdt[0] + 8] == 1:
            return struct.unpack('>Q', d[tfdt[0] + 12:tfdt[0] + 20])[0]
        return struct.unpack('>I', d[tfdt[0] + 12:tfdt[0] + 16])[0]

    out = bytearray(d[:init_len])
    mf = []
    for i, (fs, fe) in enumerate(frags):
        mf.append({'offset': len(out), 'length': fe - fs, 'dts': dts(fs, fe),
                   'arrivalMs': int(i * 1000 / 20), 'key': i % 20 == 0})
        out += d[fs:fe]

    mime = 'video/mp4; codecs="%s"' % codec_string
    open(dst, 'wb').write(out)
    json.dump({
        'codecString': codec_string, 'mime': mime, 'timescale': timescale,
        'initLength': init_len, 'fragments': mf,
        'textInit': json.dumps({'type': 'init', 'codec': codec,
            'codecString': codec_string, 'mime': mime, 'width': int(width),
            'height': int(height), 'stream': 0, 'audioCodec': None}),
        'source': 'synthetic ffmpeg testsrc %sx%s 20fps, fragment per frame - '
                  'no camera footage in it at all, so the only question it can '
                  'answer is about the container' % (width, height),
    }, open(dst[:-4] + '.json', 'w'))
    print(f'{dst}: {codec_string}, init {init_len} B, {len(mf)} fragments')

if __name__ == '__main__':
    main()
