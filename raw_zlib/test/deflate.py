#!/usr/bin/env python3
import argparse
import ctypes
import sys

import raw_zlib


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument('--window-bits', type=int, default=15)
    args = parser.parse_args()
    strm = raw_zlib.z_stream(
        zalloc=raw_zlib.Z_NULL, free=raw_zlib.Z_NULL, opaque=raw_zlib.Z_NULL)
    if args.window_bits == 15:
        init_func_name = 'deflateInit'
        rc = raw_zlib.deflateInit(strm, raw_zlib.Z_DEFAULT_COMPRESSION)
    else:
        init_func_name = 'deflateInit2'
        rc = raw_zlib.deflateInit2(
            strm=strm,
            level=raw_zlib.Z_DEFAULT_COMPRESSION,
            method=raw_zlib.Z_DEFLATED,
            windowBits=args.window_bits,
            memLevel=8,
            strategy=raw_zlib.Z_DEFAULT_STRATEGY,
        )
    if rc != raw_zlib.Z_OK:
        raise Exception('{}() failed with error {}'.format(init_func_name, rc))
    stream_end = False
    obuf = ctypes.create_string_buffer(8192)
    while not stream_end:
        ibuf = sys.stdin.buffer.read(16384)
        strm.next_in = ibuf
        strm.avail_in = len(ibuf)
        flush = (raw_zlib.Z_FINISH
                 if strm.avail_in == 0
                 else raw_zlib.Z_NO_FLUSH)
        while not stream_end:
            if flush != raw_zlib.Z_FINISH and strm.avail_in == 0:
                break
            strm.next_out = ctypes.addressof(obuf)
            strm.avail_out = ctypes.sizeof(obuf)
            rc = raw_zlib.deflate(strm, flush)
            stream_end = (rc == raw_zlib.Z_STREAM_END and
                          flush == raw_zlib.Z_FINISH)
            if rc != raw_zlib.Z_OK and not stream_end:
                raise Exception('deflate() failed with error {}'.format(rc))
            sys.stdout.buffer.write(obuf[:len(obuf) - strm.avail_out])
    rc = raw_zlib.deflateEnd(strm)
    if rc != raw_zlib.Z_OK:
        raise Exception('deflateEnd() failed with error {}'.format(rc))


if __name__ == '__main__':
    main()
