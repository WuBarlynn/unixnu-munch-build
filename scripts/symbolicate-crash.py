#!/usr/bin/env python3
"""Map runtime addresses from a crash log to dyld shared-cache images.

    scripts/symbolicate-crash.py CACHE_DIR ADDRESS [ADDRESS ...]

CACHE_DIR is the directory that holds dyld_shared_cache_arm64e (usually
<SystemOS mount>/System/Library/Caches/com.apple.dyld). The addresses are the
hex values in an EXITCRASH line (PC, LR and the bt list, without 0x).

The shared cache is mapped at a randomized base, so the log's addresses carry a
slide the log does not record. The image table gives every image's unslid
address and text size; the script finds the one slide (16 KB aligned) that puts
every address inside some image's text, then prints image and offset. This is
identification, not full symbolication: an exported-symbol lookup would need the
image's export trie, and the frame's function name is then read from the offset
against a disassembly if that is needed.
"""
import bisect
import mmap
import struct
import sys
from pathlib import Path

MAX_SLIDE = 0x40000000
ALIGN = 0x4000


def u32(b, o):
    return struct.unpack_from('<I', b, o)[0]


def u64(b, o):
    return struct.unpack_from('<Q', b, o)[0]


def main():
    if len(sys.argv) < 3:
        raise SystemExit(__doc__)
    cache_dir = Path(sys.argv[1])
    addresses = [int(a, 16) for a in sys.argv[2:]]
    files = [p for p in sorted(cache_dir.glob('dyld_shared_cache_arm64*'))
             if p.suffix != '.symbols' and p.name.count('.') == 0]
    if not files:
        raise SystemExit(f'no dyld_shared_cache_arm64* in {cache_dir}')
    path = files[0]
    print(f'cache: {path} ({path.stat().st_size >> 20} MiB)')
    with path.open('rb') as stream, mmap.mmap(stream.fileno(), 0, access=mmap.ACCESS_READ) as b:
        magic = bytes(b[:16])
        if not magic.startswith(b'dyld_v1'):
            raise SystemExit(f'not a dyld cache: {magic!r}')
        images_text_offset = u64(b, 0x88)
        images_text_count = u64(b, 0x90)
        if not (0 < images_text_count < 200000 and images_text_offset + images_text_count * 32 <= len(b)):
            raise SystemExit(f'implausible image table: offset {images_text_offset:#x} count {images_text_count}')
        images = []
        for i in range(images_text_count):
            at = images_text_offset + i * 32
            load = u64(b, at + 16)
            size = u32(b, at + 24)
            name_at = u32(b, at + 28)
            if not (0 < name_at < len(b)):
                raise SystemExit(f'image {i}: path offset {name_at:#x} outside the cache')
            end = b.find(b'\0', name_at)
            images.append((load, load + size, bytes(b[name_at:end]).decode('utf-8', 'replace')))
    if not images[0][2].startswith('/'):
        raise SystemExit(f'first image path does not look right: {images[0][2]!r}')
    images.sort()
    starts = [image[0] for image in images]
    print(f'images: {len(images)}; first {images[0][2]} @ {images[0][0]:#x}, '
          f'last {images[-1][2]} @ {images[-1][0]:#x}')

    def image_at(unslid):
        i = bisect.bisect_right(starts, unslid) - 1
        if i >= 0 and unslid < images[i][1]:
            return images[i]
        return None

    slides = [s for s in range(0, MAX_SLIDE + 1, ALIGN)
              if all(image_at(a - s) for a in addresses)]
    if not slides:
        raise SystemExit('no slide in [0, 1 GiB) puts every address inside an image; '
                         'pass the addresses of one process only')
    for slide in slides:
        print(f'slide {slide:#x}')
        for a in addresses:
            load, _, name = image_at(a - slide)
            print(f'  {a:09x} -> {name}+{a - slide - load:#x}')
        if len(slides) > 1:
            print('  (ambiguous; the correct slide is the one whose image names match the process)')


if __name__ == '__main__':
    sys.exit(main())
