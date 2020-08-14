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
        next_in=raw_zlib.Z_NULL, avail_in=0,
        zalloc=raw_zlib.Z_NULL, free=raw_zlib.Z_NULL, opaque=raw_zlib.Z_NULL)
    if args.window_bits == 15:
        init_func_name = 'inflateInit'
        rc = raw_zlib.inflateInit(strm)
    else:
        init_func_name = 'inflateInit2'
        rc = raw_zlib.inflateInit2(strm, args.window_bits)
    if rc != raw_zlib.Z_OK:
        raise Exception('{}() failed with error {}'.format(init_func_name, rc))
    stream_end = False
    obuf = ctypes.create_string_buffer(16384)
    while not stream_end:
        ibuf = sys.stdin.buffer.read(8192)
        strm.next_in = ibuf
        strm.avail_in = len(ibuf)
        while not stream_end:
            strm.next_out = ctypes.addressof(obuf)
            strm.avail_out = ctypes.sizeof(obuf)
            rc = raw_zlib.inflate(strm, raw_zlib.Z_NO_FLUSH)
            if rc == raw_zlib.Z_STREAM_END:
                stream_end = True
            elif rc == raw_zlib.Z_BUF_ERROR:
                break
            elif rc != raw_zlib.Z_OK:
                raise Exception('inflate() failed with error {}'.format(rc))
            sys.stdout.buffer.write(obuf[:len(obuf) - strm.avail_out])
    rc = raw_zlib.inflateEnd(strm)
    if rc != raw_zlib.Z_OK:
        raise Exception('inflateEnd() failed with error {}'.format(rc))


if __name__ == '__main__':
    main()
