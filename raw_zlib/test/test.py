#!/usr/bin/env python3
import contextlib
import ctypes
import itertools
import os
import random
import subprocess
import sys
import tempfile
import time
import unittest
import zlib

import parameterized
import raw_zlib


def gen_hello(r):
    while True:
        yield b'hello\n'


def gen_seq(r):
    i = 0
    while True:
        yield ('%d\n' % i).encode()
        i += 1


def gen_nulls(r):
    while True:
        yield b'\0' * 4096


def gen_zeros_ones(r):
    while True:
        yield bytes(r.choice((0x30, 0x31)) for _ in range(4096))


def gen_random(r):
    while True:
        yield bytes(r.getrandbits(8) for _ in range(4096))


class Gen(object):
    def __init__(self, chunks):
        self.chunks = chunks
        self.buffer = bytearray()

    def __call__(self, n):
        while len(self.buffer) < n:
            self.buffer.extend(next(self.chunks))
        result = self.buffer[:n]
        del self.buffer[:n]
        return result


def gen_mix(r):
    gs = [
        Gen(f(r))
        for f in (
            gen_hello,
            gen_seq,
            gen_nulls,
            gen_zeros_ones,
            gen_random,
        )
    ]
    while True:
        yield r.choice(gs)(r.randint(1, 65536))


WB_RAW = -15
WB_ZLIB = 15
WB_GZIP = 31


class TestCase(unittest.TestCase):
    def _assert_deflate_ok(self, strm, flush):
        err = raw_zlib.deflate(strm, flush)
        self.assertIn(err, (raw_zlib.Z_OK, raw_zlib.Z_BUF_ERROR))

    def _assert_deflate_stream_end(self, strm):
        err = raw_zlib.deflate(strm, raw_zlib.Z_FINISH)
        self.assertEqual(raw_zlib.Z_STREAM_END, err)

    def test_version(self):
        print(file=sys.stderr)
        print(raw_zlib.zlibVersion(), file=sys.stderr)

    def test_compile_flags(self):
        print(file=sys.stderr)
        print(hex(raw_zlib.zlibCompileFlags()), file=sys.stderr)

    def test_inflate_deflate(self):
        with tempfile.TemporaryFile() as ifp:
            data = b'\n'.join([str(x).encode() for x in range(5000)])
            ifp.write(data)
            ifp.flush()
            ifp.seek(0)
            basedir = os.path.dirname(__file__)
            deflate = subprocess.Popen(
                [sys.executable, os.path.join(basedir, 'deflate.py')],
                stdin=ifp,
                stdout=subprocess.PIPE)
            try:
                with tempfile.TemporaryFile() as ofp:
                    subprocess.check_call(
                        [sys.executable, os.path.join(basedir, 'inflate.py')],
                        stdin=deflate.stdout,
                        stdout=ofp)
                    ofp.seek(0)
                    self.assertEqual(data, ofp.read())
            finally:
                if deflate.wait() != 0:
                    raise Exception('deflate failed')

    @staticmethod
    @contextlib.contextmanager
    def _make_deflate_stream(
            window_bits=WB_ZLIB,
            level=raw_zlib.Z_DEFAULT_COMPRESSION,
            mem_level=8,
            strategy=raw_zlib.Z_DEFAULT_STRATEGY,
    ):
        strm = raw_zlib.z_stream(
            zalloc=raw_zlib.Z_NULL, free=raw_zlib.Z_NULL,
            opaque=raw_zlib.Z_NULL)
        if window_bits != WB_ZLIB or \
                mem_level != 8 or \
                strategy != raw_zlib.Z_DEFAULT_STRATEGY:
            err = raw_zlib.deflateInit2(
                strm,
                level=level,
                method=raw_zlib.Z_DEFLATED,
                windowBits=window_bits,
                memLevel=mem_level,
                strategy=strategy,
            )
        else:
            err = raw_zlib.deflateInit(strm, level)
        if err != raw_zlib.Z_OK:
            raise Exception('deflateInit() failed: error %d' % err)
        try:
            yield strm
        finally:
            err = raw_zlib.deflateEnd(strm)
            if err != raw_zlib.Z_OK:
                raise Exception('deflateEnd() failed: error %d' % err)

    @staticmethod
    @contextlib.contextmanager
    def _make_inflate_stream(window_bits=WB_ZLIB):
        strm = raw_zlib.z_stream(
            next_in=raw_zlib.Z_NULL, avail_in=0,
            zalloc=raw_zlib.Z_NULL, free=raw_zlib.Z_NULL,
            opaque=raw_zlib.Z_NULL)
        if window_bits != WB_ZLIB:
            err = raw_zlib.inflateInit2(strm, windowBits=window_bits)
        else:
            err = raw_zlib.inflateInit(strm)
        if err != raw_zlib.Z_OK:
            raise Exception('inflateInit() failed: error %d' % err)
        try:
            yield strm
        finally:
            err = raw_zlib.inflateEnd(strm)
            if err != raw_zlib.Z_OK:
                raise Exception('inflateEnd() failed: error %d' % err)

    @staticmethod
    def _addressof_string_buffer(buf, offset=0):
        return ctypes.cast(ctypes.addressof(buf) + offset, ctypes.c_char_p)

    @staticmethod
    def _addressof_bytearray(buf):
        return ctypes.cast(ctypes.addressof(
            (ctypes.c_char * len(buf)).from_buffer(buf)),
            ctypes.c_char_p)

    @staticmethod
    def _shl(buf, bits):
        buf_pos = 0
        value = 0
        value_bits = 0
        while bits >= 8:
            value |= ord(buf[buf_pos]) << value_bits
            buf_pos += 1
            value_bits += 8
            bits -= 8
        carry = 0
        for i in range(len(buf) - 1, buf_pos - 1, -1):
            next_carry = ord(buf[i]) & ((1 << bits) - 1)
            buf[i] = (ord(buf[i]) >> bits) | (carry << (8 - bits))
            carry = next_carry
        value |= carry << value_bits
        return value, buf_pos

    @parameterized.parameterized.expand(((bits,) for bits in range(0, 17)))
    def test_inflate_prime(self, bits):
        with self._make_deflate_stream(window_bits=WB_RAW) as strm:
            buf = ctypes.create_string_buffer(b'hello')
            strm.next_in = self._addressof_string_buffer(buf)
            strm.avail_in = len(buf)
            zbuf = ctypes.create_string_buffer(
                raw_zlib.deflateBound(strm, strm.avail_in))
            strm.next_out = self._addressof_string_buffer(zbuf)
            strm.avail_out = len(zbuf)
            self._assert_deflate_stream_end(strm)
            zbuf_len = len(zbuf) - strm.avail_out
        value, zbuf_pos = self._shl(zbuf, bits)
        with self._make_inflate_stream(window_bits=WB_RAW) as strm:
            strm.next_in = self._addressof_string_buffer(zbuf, offset=zbuf_pos)
            strm.avail_in = zbuf_len - zbuf_pos
            buf = ctypes.create_string_buffer(len(buf))
            strm.next_out = self._addressof_string_buffer(buf)
            strm.avail_out = len(buf)
            raw_zlib.inflatePrime(strm, bits, value)
            err = raw_zlib.inflate(strm, raw_zlib.Z_NO_FLUSH)
            self.assertEqual(raw_zlib.Z_STREAM_END, err)
            self.assertEqual(b'hello\0', bytes(buf))

    def _set_dictionary(self, strm, gen, size):
        buf = bytearray(gen(size))
        err = raw_zlib.deflateSetDictionary(
            strm, self._addressof_bytearray(buf), len(buf))
        self.assertEqual(raw_zlib.Z_OK, err)
        return buf

    def _gen_buf(self, gen, size, dict):
        result = bytearray()
        result += gen(size // 3)
        result += dict[:size // 3]
        result += gen(size - len(result))
        return result

    SET_DICTIONARY_SIZES = [1 << x for x in range(0, 17, 4)]

    @parameterized.parameterized.expand(
        itertools.product(*([SET_DICTIONARY_SIZES] * 4)))
    def test_set_dictionary(
            self, dict1_size, buf2_size, dict3_size, buf4_size):
        gen = Gen(gen_random(random.Random(2024749321)))
        with tempfile.NamedTemporaryFile() as zfp:
            with self._make_deflate_stream(window_bits=WB_RAW) as strm:
                dict1 = self._set_dictionary(strm, gen, dict1_size)
                buf2 = self._gen_buf(gen, buf2_size, dict1)
                strm.next_in = self._addressof_bytearray(buf2)
                strm.avail_in = len(buf2)
                while True:
                    zbuf = ctypes.create_string_buffer(4096)
                    strm.next_out = ctypes.cast(
                        ctypes.addressof(zbuf),
                        ctypes.c_char_p)
                    strm.avail_out = len(zbuf)
                    self._assert_deflate_ok(strm, raw_zlib.Z_BLOCK)
                    zfp.write(zbuf[:len(zbuf) - strm.avail_out])
                    if strm.avail_out != 0:
                        break
                dict3 = self._set_dictionary(strm, gen, dict3_size)
                buf4 = self._gen_buf(gen, buf4_size, dict1 + dict3)
                strm.next_in = self._addressof_bytearray(buf4)
                strm.avail_in = len(buf4)
                stream_end = False
                while not stream_end:
                    zbuf = ctypes.create_string_buffer(4096)
                    strm.next_out = ctypes.cast(
                        ctypes.addressof(zbuf),
                        ctypes.c_char_p)
                    strm.avail_out = len(zbuf)
                    err = raw_zlib.deflate(strm, raw_zlib.Z_FINISH)
                    if err == raw_zlib.Z_STREAM_END:
                        stream_end = True
                    else:
                        self.assertEqual(raw_zlib.Z_OK, err)
                    zfp.write(zbuf[:len(zbuf) - strm.avail_out])
            zfp.flush()
            zfp.seek(0)
            inflated = bytearray()
            with self._make_inflate_stream(window_bits=WB_RAW) as strm:
                err = raw_zlib.inflateSetDictionary(
                    strm, self._addressof_bytearray(dict1), len(dict1))
                self.assertEqual(raw_zlib.Z_OK, err)
                stream_end = False
                while not stream_end:
                    zbuf = bytearray(zfp.read(256))
                    if len(zbuf) == 0:
                        break
                    strm.next_in = ctypes.addressof(
                        (ctypes.c_char * len(zbuf)).from_buffer(zbuf))
                    strm.avail_in = len(zbuf)
                    while True:
                        buf = ctypes.create_string_buffer(4096)
                        strm.next_out = ctypes.cast(
                            ctypes.addressof(buf),
                            ctypes.c_char_p)
                        strm.avail_out = len(buf)
                        err = raw_zlib.inflate(strm, raw_zlib.Z_BLOCK)
                        inflated += buf[:len(buf) - strm.avail_out]
                        if err == raw_zlib.Z_STREAM_END:
                            stream_end = True
                            break
                        if err == raw_zlib.Z_BUF_ERROR:
                            break
                        self.assertEqual(raw_zlib.Z_OK, err)
                        if strm.data_type & 128 != 0:
                            if strm.total_out == len(buf2):
                                err = raw_zlib.inflateSetDictionary(
                                    strm, self._addressof_bytearray(dict3),
                                    len(dict3))
                                self.assertEqual(raw_zlib.Z_OK, err)
                self.assertEqual(buf2 + buf4, inflated)

    def test_compress(self):
        dest = bytearray(raw_zlib.compressBound(4096))
        source = bytearray(b'A' * 4096)
        err, dest_len = raw_zlib.compress(
            self._addressof_bytearray(dest),
            len(dest),
            self._addressof_bytearray(source),
            len(source))
        self.assertEqual(raw_zlib.Z_OK, err)
        self.assertLessEqual(dest_len, len(dest))
        self.assertEqual(source, zlib.decompress(dest[:dest_len]))

    @parameterized.parameterized.expand(((level,) for level in range(1, 10)))
    def test_compress2(self, level):
        dest = bytearray(128)
        source = bytearray(b'A' * 4096)
        err, dest_len = raw_zlib.compress2(
            self._addressof_bytearray(dest),
            len(dest),
            self._addressof_bytearray(source),
            len(source),
            level)
        self.assertEqual(raw_zlib.Z_OK, err)
        self.assertLessEqual(dest_len, len(dest))
        self.assertEqual(source, zlib.decompress(dest[:dest_len]))

    def test_uncompress(self):
        plain = bytearray(b'A' * 4096)
        source = bytearray(zlib.compress(plain))
        dest = bytearray(len(plain))
        err, dest_len = raw_zlib.uncompress(
            self._addressof_bytearray(dest),
            len(dest),
            self._addressof_bytearray(source),
            len(source))
        self.assertEqual(raw_zlib.Z_OK, err)
        self.assertLessEqual(dest_len, len(dest))
        self.assertEqual(plain, dest)

    def test_uncompress2(self):
        plain = bytearray(b'A' * 4096)
        source = bytearray(zlib.compress(plain))
        dest = bytearray(len(plain))
        err, dest_len, source_len = raw_zlib.uncompress2(
            self._addressof_bytearray(dest),
            len(dest),
            self._addressof_bytearray(source),
            len(source))
        self.assertEqual(raw_zlib.Z_OK, err)
        self.assertLessEqual(dest_len, len(dest))
        self.assertEqual(source_len, len(source))
        self.assertEqual(plain, dest)

    @staticmethod
    @contextlib.contextmanager
    def _limit_avail_in(strm, max_size):
        avail_in0 = strm.avail_in
        avail_in1 = min(avail_in0, max_size)
        strm.avail_in = avail_in1
        yield
        consumed = avail_in1 - strm.avail_in
        strm.avail_in = avail_in0 - consumed

    @staticmethod
    @contextlib.contextmanager
    def _limit_avail_out(strm, max_size):
        avail_out0 = strm.avail_out
        avail_out1 = min(avail_out0, max_size)
        strm.avail_out = avail_out1
        yield
        consumed = avail_out1 - strm.avail_out
        strm.avail_out = avail_out0 - consumed

    @classmethod
    @contextlib.contextmanager
    def _limit_avail_in_out(cls, strm, max_avail_in, max_avail_out):
        with cls._limit_avail_in(strm, max_avail_in):
            with cls._limit_avail_out(strm, max_avail_out):
                yield

    def _check_inflate(
            self,
            dest,
            compressed_size,
            plain,
            window_bits=WB_ZLIB,
            dictionary=None,
    ):
        plain2 = bytearray(len(plain))
        with self._make_inflate_stream(window_bits=window_bits) as strm:
            if window_bits == WB_RAW and dictionary is not None:
                err = raw_zlib.inflateSetDictionary(
                    strm, dictionary, len(dictionary))
                self.assertEqual(raw_zlib.Z_OK, err)
            strm.next_in = self._addressof_bytearray(dest)
            strm.avail_in = compressed_size
            strm.next_out = self._addressof_bytearray(plain2)
            strm.avail_out = len(plain2)
            err = raw_zlib.inflate(strm, raw_zlib.Z_NO_FLUSH)
            if window_bits == WB_ZLIB and dictionary is not None:
                self.assertEqual(raw_zlib.Z_NEED_DICT, err)
                err = raw_zlib.inflateSetDictionary(
                    strm, dictionary, len(dictionary))
                self.assertEqual(raw_zlib.Z_OK, err)
                err = raw_zlib.inflate(strm, raw_zlib.Z_NO_FLUSH)
            self.assertEqual(raw_zlib.Z_STREAM_END, err)
            self.assertEqual(0, strm.avail_out)
            self.assertEqual(plain, plain2)

    def test_deflate_params(self):
        gen = Gen(gen_random(random.Random(2097987671)))
        plain = gen(1024 * 1024)
        dest = bytearray(len(plain) * 2)
        chunk_size = len(plain) // 400
        with self._make_deflate_stream() as strm:
            strm.next_in = self._addressof_bytearray(plain)
            strm.avail_in = len(plain)
            strm.next_out = self._addressof_bytearray(dest)
            strm.avail_out = len(dest)
            for level1 in range(10):
                for level2 in range(10):
                    with self._limit_avail_in(strm, chunk_size):
                        err = raw_zlib.deflateParams(
                            strm, level1, raw_zlib.Z_DEFAULT_STRATEGY)
                        self.assertEqual(raw_zlib.Z_OK, err)
                    with self._limit_avail_in(strm, chunk_size):
                        self._assert_deflate_ok(strm, raw_zlib.Z_NO_FLUSH)
                    with self._limit_avail_in(strm, chunk_size):
                        err = raw_zlib.deflateParams(
                            strm, level2, raw_zlib.Z_DEFAULT_STRATEGY)
                        msg = 'deflateParams({} -> {})'.format(level1, level2)
                        self.assertEqual(raw_zlib.Z_OK, err, msg)
                    with self._limit_avail_in(strm, chunk_size):
                        self._assert_deflate_ok(strm, raw_zlib.Z_NO_FLUSH)
            self._assert_deflate_stream_end(strm)
            compressed_size = len(dest) - strm.avail_out
        self._check_inflate(dest, compressed_size, plain)

    def test_deflate_reset(self):
        strm = raw_zlib.z_stream(
            zalloc=raw_zlib.Z_NULL, free=raw_zlib.Z_NULL,
            opaque=raw_zlib.Z_NULL)
        err = raw_zlib.deflateInit(strm, raw_zlib.Z_BEST_SPEED)
        self.assertEqual(raw_zlib.Z_OK, err)
        try:
            for _ in range(2):
                plain = bytearray(b'AAAA')
                compressed = bytearray(1024)
                strm.next_in = self._addressof_bytearray(plain)
                strm.avail_in = len(plain)
                strm.next_out = self._addressof_bytearray(compressed)
                strm.avail_out = len(compressed)
                self._assert_deflate_stream_end(strm)
                compression_method = compressed[0] & 0xf
                self.assertEqual(0x8, compression_method)
                cinfo = compressed[0] >> 4
                self.assertLessEqual(cinfo, 7)
                fdict = (compressed[1] >> 5) & 1
                self.assertEqual(0, fdict)
                flevel = compressed[1] >> 6
                self.assertEqual(0, flevel)
                # deflateReset should preserve the compression level
                raw_zlib.deflateReset(strm)
        finally:
            raw_zlib.deflateEnd(strm)

    def test_small_out(self):
        plain = bytearray(b'\x05\x4e')
        dest = bytearray(16)
        sizeof_zlib_header = 2
        with self._make_deflate_stream() as strm:
            strm.next_in = self._addressof_bytearray(plain)
            strm.avail_in = len(plain)
            strm.next_out = self._addressof_bytearray(dest)
            strm.avail_out = sizeof_zlib_header
            self._assert_deflate_ok(strm, raw_zlib.Z_PARTIAL_FLUSH)
            self.assertEqual(0, strm.avail_out)
            strm.avail_out = len(dest) - sizeof_zlib_header
            self._assert_deflate_stream_end(strm)
            compressed_size = len(dest) - strm.avail_out
        self._check_inflate(dest, compressed_size, plain)

    def test_small_out2(self):
        plain = bytearray(b'\xff\xff\x60\xff\x00\x7b')
        dest = bytearray(16)
        with self._make_deflate_stream(level=raw_zlib.Z_BEST_SPEED) as strm:
            strm.next_in = self._addressof_bytearray(plain)
            strm.avail_in = 3
            strm.next_out = self._addressof_bytearray(dest)
            strm.avail_out = 1
            self._assert_deflate_ok(strm, raw_zlib.Z_PARTIAL_FLUSH)
            consumed_in = 3 - strm.avail_in
            consumed_out = 1 - strm.avail_out

            strm.avail_in = 3
            strm.avail_out = 4
            err = raw_zlib.deflateParams(
                strm,
                level=raw_zlib.Z_DEFAULT_COMPRESSION,
                strategy=raw_zlib.Z_DEFAULT_STRATEGY,
            )
            self.assertIn(err, (raw_zlib.Z_OK, raw_zlib.Z_BUF_ERROR))
            consumed_in += 3 - strm.avail_in
            consumed_out += 4 - strm.avail_out

            strm.avail_in = len(plain) - consumed_in
            strm.avail_out = len(dest) - consumed_out
            self._assert_deflate_stream_end(strm)
            consumed_out = len(dest) - strm.avail_out
        self._check_inflate(dest, consumed_out, plain)

    def test_small_out3(self):
        plain = bytearray(b'\x3f\xff\xf8\xff\xff\xff\xff\xff\xff')
        dest = bytearray(658)
        with self._make_deflate_stream(level=raw_zlib.Z_BEST_SPEED) as strm:
            strm.next_in = self._addressof_bytearray(plain)
            strm.avail_in = len(plain)
            strm.next_out = self._addressof_bytearray(dest)
            strm.avail_out = len(dest)

            with self._limit_avail_in_out(strm, 1, 2):
                self._assert_deflate_ok(strm, raw_zlib.Z_PARTIAL_FLUSH)
            with self._limit_avail_in_out(strm, 1, 2):
                err = raw_zlib.deflateParams(
                    strm,
                    level=raw_zlib.Z_BEST_SPEED,
                    strategy=raw_zlib.Z_HUFFMAN_ONLY,
                )
                self.assertIn(err, (raw_zlib.Z_OK, raw_zlib.Z_BUF_ERROR))
            with self._limit_avail_in_out(strm, 1, 2):
                self._assert_deflate_ok(strm, raw_zlib.Z_PARTIAL_FLUSH)
            with self._limit_avail_in_out(strm, 1, 2):
                err = raw_zlib.deflateParams(
                    strm,
                    level=raw_zlib.Z_BEST_SPEED,
                    strategy=raw_zlib.Z_DEFAULT_STRATEGY,
                )
                self.assertIn(err, (raw_zlib.Z_OK, raw_zlib.Z_BUF_ERROR))
            self._assert_deflate_stream_end(strm)
        self._check_inflate(dest, len(dest) - strm.avail_out, plain)

    def test_set_dictionary2(self):
        plain = bytearray(b'\x2d')
        dest = bytearray(130)
        with self._make_deflate_stream(
                window_bits=WB_RAW,
                level=raw_zlib.Z_BEST_SPEED,
        ) as strm:
            strm.next_in = self._addressof_bytearray(plain)
            strm.avail_in = len(plain)
            strm.next_out = self._addressof_bytearray(dest)
            strm.avail_out = len(dest)

            dictionary = b'\xd7'
            err = raw_zlib.deflateSetDictionary(
                strm, dictionary, len(dictionary))
            self.assertEqual(raw_zlib.Z_OK, err)

            err = raw_zlib.deflateParams(
                strm,
                level=raw_zlib.Z_NO_COMPRESSION,
                strategy=raw_zlib.Z_DEFAULT_STRATEGY,
            )
            self.assertEqual(raw_zlib.Z_OK, err)

            self._assert_deflate_stream_end(strm)
        self._check_inflate(
            dest=dest,
            compressed_size=len(dest) - strm.avail_out,
            plain=plain,
            window_bits=WB_RAW,
            dictionary=dictionary,
        )

    def test_set_dictionary3(self):
        plain = bytearray(b'\x00\x00\x00')
        dest = bytearray(134)
        with self._make_deflate_stream(
                window_bits=WB_RAW,
                level=raw_zlib.Z_BEST_SPEED,
                mem_level=1,
                strategy=raw_zlib.Z_FIXED,
        ) as strm:
            strm.next_in = self._addressof_bytearray(plain)
            strm.avail_in = len(plain)
            strm.next_out = self._addressof_bytearray(dest)
            strm.avail_out = len(dest)

            dictionary = b'\x00\x09'
            err = raw_zlib.deflateSetDictionary(
                strm, dictionary, len(dictionary))
            self.assertEqual(raw_zlib.Z_OK, err)

            err = raw_zlib.deflateParams(
                strm,
                level=raw_zlib.Z_DEFAULT_COMPRESSION,
                strategy=raw_zlib.Z_RLE,
            )
            self.assertEqual(raw_zlib.Z_OK, err)

            self._assert_deflate_stream_end(strm)
        self._check_inflate(
            dest=dest,
            compressed_size=len(dest) - strm.avail_out,
            plain=plain,
            window_bits=WB_RAW,
            dictionary=dictionary,
        )

    def test_set_dictionary4(self):
        plain = bytearray(b'\x00\x3a\x00\x00\x00')
        dest = bytearray(266)
        with self._make_deflate_stream(
                level=raw_zlib.Z_BEST_SPEED,
                mem_level=5,
                strategy=raw_zlib.Z_FIXED,
        ) as strm:
            strm.next_in = self._addressof_bytearray(plain)
            strm.avail_in = len(plain)
            strm.next_out = self._addressof_bytearray(dest)
            strm.avail_out = len(dest)

            dictionary = b'\x00'
            err = raw_zlib.deflateSetDictionary(
                strm, dictionary, len(dictionary))
            self.assertEqual(raw_zlib.Z_OK, err)

            with self._limit_avail_in_out(strm, 2, 3):
                err = raw_zlib.deflateParams(
                    strm,
                    level=raw_zlib.Z_BEST_SPEED,
                    strategy=raw_zlib.Z_FILTERED,
                )
                self.assertIn(err, (raw_zlib.Z_OK, raw_zlib.Z_BUF_ERROR))

            with self._limit_avail_in_out(strm, 2, 262):
                err = raw_zlib.deflateParams(
                    strm,
                    level=2,
                    strategy=raw_zlib.Z_RLE,
                )
                self.assertEqual(raw_zlib.Z_OK, err)

            self._assert_deflate_stream_end(strm)
        self._check_inflate(
            dest=dest,
            compressed_size=len(dest) - strm.avail_out,
            plain=plain,
            dictionary=dictionary,
        )

    def test_deflate_params2(self):
        plain = bytearray(b'\xef')
        dest = bytearray(392)
        with self._make_deflate_stream(
                window_bits=WB_GZIP,
                level=raw_zlib.Z_DEFAULT_COMPRESSION,
                mem_level=1,
                strategy=raw_zlib.Z_DEFAULT_STRATEGY,
        ) as strm:
            strm.next_in = self._addressof_bytearray(plain)
            strm.avail_in = len(plain)
            strm.next_out = self._addressof_bytearray(dest)
            strm.avail_out = len(dest)

            with self._limit_avail_in_out(strm, 1, 195):
                err = raw_zlib.deflateParams(
                    strm,
                    level=raw_zlib.Z_BEST_SPEED,
                    strategy=raw_zlib.Z_DEFAULT_STRATEGY,
                )
                self.assertIn(err, (raw_zlib.Z_OK, raw_zlib.Z_BUF_ERROR))

            with self._limit_avail_in_out(strm, 1, 195):
                self._assert_deflate_ok(strm, raw_zlib.Z_NO_FLUSH)

            with self._limit_avail_in_out(strm, 0, 0):
                err = raw_zlib.deflateParams(
                    strm,
                    level=raw_zlib.Z_BEST_SPEED,
                    strategy=raw_zlib.Z_FILTERED,
                )
                self.assertIn(err, (raw_zlib.Z_OK, raw_zlib.Z_BUF_ERROR))

            self._assert_deflate_stream_end(strm)
        self._check_inflate(
            dest=dest,
            compressed_size=len(dest) - strm.avail_out,
            plain=plain,
            window_bits=WB_GZIP,
        )

    def test_deflate_params3(self):
        plain = bytearray(b'\xfb\x00\x72\x00\x00\x00')
        dest = bytearray(786)
        with self._make_deflate_stream(
                window_bits=WB_GZIP,
                level=raw_zlib.Z_BEST_SPEED,
                mem_level=4,
                strategy=raw_zlib.Z_FILTERED,
        ) as strm:
            strm.next_in = self._addressof_bytearray(plain)
            strm.avail_in = len(plain)
            strm.next_out = self._addressof_bytearray(dest)
            strm.avail_out = len(dest)

            with self._limit_avail_in_out(strm, 2, 263):
                self._assert_deflate_ok(strm, raw_zlib.Z_NO_FLUSH)

            with self._limit_avail_in_out(strm, 0, 1):
                err = raw_zlib.deflateParams(
                    strm,
                    level=raw_zlib.Z_BEST_SPEED,
                    strategy=raw_zlib.Z_FIXED,
                )
                self.assertIn(err, (raw_zlib.Z_OK, raw_zlib.Z_BUF_ERROR))

            with self._limit_avail_in_out(strm, 2, 1):
                self._assert_deflate_ok(strm, raw_zlib.Z_PARTIAL_FLUSH)

            with self._limit_avail_in_out(strm, 1, 259):
                self._assert_deflate_ok(strm, raw_zlib.Z_PARTIAL_FLUSH)

            with self._limit_avail_in_out(strm, 0, 1):
                err = raw_zlib.deflateParams(
                    strm,
                    level=3,
                    strategy=raw_zlib.Z_FILTERED,
                )
                self.assertIn(err, (raw_zlib.Z_OK, raw_zlib.Z_BUF_ERROR))

            with self._limit_avail_in_out(strm, 1, 259):
                self._assert_deflate_ok(strm, raw_zlib.Z_NO_FLUSH)

            self._assert_deflate_stream_end(strm)
        self._check_inflate(
            dest=dest,
            compressed_size=len(dest) - strm.avail_out,
            plain=plain,
            window_bits=WB_GZIP,
        )

    def test_deflate_params4(self):
        plain = bytearray(b'\x00\x00\xff\x00\x00\x00')
        dest = bytearray(1042)
        with self._make_deflate_stream(
                window_bits=WB_GZIP,
                level=raw_zlib.Z_BEST_SPEED,
                mem_level=1,
                strategy=raw_zlib.Z_FILTERED,
        ) as strm:
            strm.next_in = self._addressof_bytearray(plain)
            strm.avail_in = len(plain)
            strm.next_out = self._addressof_bytearray(dest)
            strm.avail_out = len(dest)

            with self._limit_avail_in_out(strm, 1, 1):
                self._assert_deflate_ok(strm, raw_zlib.Z_NO_FLUSH)

            with self._limit_avail_in_out(strm, 1, 2):
                self._assert_deflate_ok(strm, raw_zlib.Z_SYNC_FLUSH)

            with self._limit_avail_in_out(strm, 1, 344):
                err = raw_zlib.deflateParams(
                    strm,
                    level=raw_zlib.Z_BEST_SPEED,
                    strategy=raw_zlib.Z_DEFAULT_STRATEGY,
                )
                self.assertIn(err, (raw_zlib.Z_OK, raw_zlib.Z_BUF_ERROR))

            with self._limit_avail_in_out(strm, 1, 344):
                self._assert_deflate_ok(strm, raw_zlib.Z_NO_FLUSH)

            with self._limit_avail_in_out(strm, 1, 344):
                self._assert_deflate_ok(strm, raw_zlib.Z_NO_FLUSH)

            with self._limit_avail_in_out(strm, 0, 1):
                self._assert_deflate_ok(strm, raw_zlib.Z_NO_FLUSH)

            with self._limit_avail_in_out(strm, 0, 1):
                self._assert_deflate_ok(strm, raw_zlib.Z_PARTIAL_FLUSH)

            with self._limit_avail_in_out(strm, 0, 1):
                err = raw_zlib.deflateParams(
                    strm,
                    level=raw_zlib.Z_DEFAULT_COMPRESSION,
                    strategy=raw_zlib.Z_RLE,
                )
                self.assertIn(err, (raw_zlib.Z_OK, raw_zlib.Z_BUF_ERROR))

            self._assert_deflate_stream_end(strm)
        self._check_inflate(
            dest=dest,
            compressed_size=len(dest) - strm.avail_out,
            plain=plain,
            window_bits=WB_GZIP,
        )

    def test_deflate_params5(self):
        plain = bytearray(
            b'\x00\x00\x00\x00\x00\x00\x99\x00\xfe\x00\x00\x00\x00\x00\x00\x00'
            b'\x00\x00\x00\x00\x00\x00\x00\xfe\x00\x00\x00\x00\x99\x00\xfe\x00'
            b'\x00\x00\x00\x00\x00\x99\x00\xfe\x00\x00\x00\x00\x99\x00\xfe\x00'
            b'\x00\x00\x99\x00\xfe\x00\x00\x00\x00\x99\x00\xfe\x00\x00\x00\x00'
            b'\x99\x00\xfe\x00\x00\x00\x00\x00\x00\x99\x00\xfe\x00\x00\x00\x00'
            b'\x99\x00\xfe\x00'
        )
        dest = bytearray(680)
        with self._make_deflate_stream(
                window_bits=WB_RAW,
                level=raw_zlib.Z_BEST_SPEED,
                mem_level=7,
                strategy=raw_zlib.Z_FILTERED,
        ) as strm:
            strm.next_in = self._addressof_bytearray(plain)
            strm.avail_in = len(plain)
            strm.next_out = self._addressof_bytearray(dest)
            strm.avail_out = len(dest)

            with self._limit_avail_in_out(strm, 3, 5):
                self._assert_deflate_ok(strm, raw_zlib.Z_PARTIAL_FLUSH)

            with self._limit_avail_in_out(strm, 77, 668):
                err = raw_zlib.deflateParams(
                    strm,
                    level=raw_zlib.Z_DEFAULT_COMPRESSION,
                    strategy=raw_zlib.Z_FILTERED,
                )
                self.assertIn(err, (raw_zlib.Z_OK, raw_zlib.Z_BUF_ERROR))

            with self._limit_avail_in_out(strm, 3, 5):
                self._assert_deflate_ok(strm, raw_zlib.Z_PARTIAL_FLUSH)

            self._assert_deflate_stream_end(strm)

        plain2 = bytearray(len(plain))
        with self._make_inflate_stream(window_bits=WB_RAW) as strm:
            strm.next_in = self._addressof_bytearray(dest)
            strm.avail_in = len(dest) - strm.avail_out
            strm.next_out = self._addressof_bytearray(plain2)
            strm.avail_out = len(plain2)
            with self._limit_avail_in_out(strm, 32, 83):
                err = raw_zlib.inflate(strm, raw_zlib.Z_NO_FLUSH)
                self.assertEqual(raw_zlib.Z_OK, err)
            err = raw_zlib.inflate(strm, raw_zlib.Z_NO_FLUSH)
            self.assertEqual(raw_zlib.Z_STREAM_END, err)
            self.assertEqual(0, strm.avail_out)
            self.assertEqual(plain, plain2)

    @staticmethod
    def _make_gen():
        return Gen(gen_mix(random.Random(1135747107)))

    @parameterized.parameterized.expand(((n,) for n in range(0, 7)))
    def test_1(self, n):
        buf = bytearray(self._make_gen()(n))
        with self._make_deflate_stream() as strm:
            zbuf = ctypes.create_string_buffer(
                raw_zlib.deflateBound(strm, len(buf)))
            strm.next_in = ctypes.addressof(
                (ctypes.c_char * len(buf)).from_buffer(buf))
            strm.avail_in = len(buf)
            strm.next_out = ctypes.addressof(zbuf)
            zlen = 0
            while True:
                strm.avail_out = 1
                err = raw_zlib.deflate(strm, raw_zlib.Z_FINISH)
                self.assertIn(err, (raw_zlib.Z_OK, raw_zlib.Z_STREAM_END))
                zlen += 1 - strm.avail_out
                if err == raw_zlib.Z_STREAM_END:
                    break
            self.assertEqual(zlen, strm.total_out)
        self._check_inflate(zbuf, zlen, buf)

    @parameterized.parameterized.expand(((n,) for n in range(0, 7)))
    def test_2(self, n):
        buf = bytearray(self._make_gen()(n))
        with self._make_deflate_stream() as strm:
            zbuf = ctypes.create_string_buffer(
                raw_zlib.deflateBound(strm, len(buf)))
            strm.next_in = ctypes.addressof(
                (ctypes.c_char * len(buf)).from_buffer(buf))
            strm.avail_in = len(buf)
            strm.next_out = ctypes.addressof(zbuf)
            strm.avail_out = len(zbuf)
            while True:
                flush = (raw_zlib.Z_FINISH
                         if strm.avail_in == 0
                         else raw_zlib.Z_NO_FLUSH)
                err = raw_zlib.deflate(strm, flush)
                if err == raw_zlib.Z_STREAM_END and flush == raw_zlib.Z_FINISH:
                    break
                self.assertEqual(raw_zlib.Z_OK, err)
            zlen = strm.total_out
        self._check_inflate(zbuf, zlen, buf)

    def test_3(self):
        buf = bytearray(self._make_gen()(2 * 1024 * 1024))
        with self._make_deflate_stream() as strm:
            zbuf = ctypes.create_string_buffer(
                raw_zlib.deflateBound(strm, len(buf)))
            strm.next_in = ctypes.addressof(
                (ctypes.c_char * len(buf)).from_buffer(buf))
            strm.avail_in = len(buf)
            strm.next_out = ctypes.addressof(zbuf)
            strm.avail_out = len(zbuf)
            while True:
                err = raw_zlib.deflate(strm, raw_zlib.Z_FINISH)
                if err == raw_zlib.Z_STREAM_END:
                    break
                self.assertEqual(raw_zlib.Z_OK, err)
            zlen = strm.total_out
        self._check_inflate(zbuf, zlen, buf)

    def test_deflate_performance(self):
        # https://www.zlib.net/zlib_how.html
        # "buffers sizes on the order of 128K or 256K bytes should be used"
        buf = bytearray(self._make_gen()(256 * 1024))
        len_buf = len(buf)
        addressof_buf = ctypes.cast(
            ctypes.addressof((ctypes.c_char * len_buf).from_buffer(buf)),
            ctypes.c_char_p)
        with self._make_deflate_stream(level=raw_zlib.Z_BEST_SPEED) as strm:
            zbuf = ctypes.create_string_buffer(
                raw_zlib.deflateBound(strm, len_buf))
            addressof_zbuf = ctypes.cast(
                ctypes.addressof(zbuf),
                ctypes.c_char_p)
            len_zbuf = len(zbuf)
            duration = 1
            deadline = time.time() + duration
            while time.time() < deadline:
                strm.next_in = addressof_buf
                strm.avail_in = len_buf
                while strm.avail_in > 0:
                    strm.next_out = addressof_zbuf
                    strm.avail_out = len_zbuf
                    err = raw_zlib.deflate(strm, raw_zlib.Z_NO_FLUSH)
                    self.assertEqual(raw_zlib.Z_OK, err)
            while True:
                strm.next_out = addressof_zbuf
                strm.avail_out = len_zbuf
                err = raw_zlib.deflate(strm, raw_zlib.Z_FINISH)
                if err == raw_zlib.Z_STREAM_END:
                    break
                self.assertEqual(raw_zlib.Z_OK, err)
        gbs = strm.total_in / 1024. / 1024. / 1024. / duration
        print(file=sys.stderr)
        print('deflate performance: %.3f GB/s' % gbs, file=sys.stderr)
        rate_percent = (strm.total_out * 100 / strm.total_in)
        print('deflate rate       : %.2f%%' % rate_percent, file=sys.stderr)

    def _deflate_blocks(self):
        buf = bytearray(self._make_gen()(256 * 1024))
        with self._make_deflate_stream(
                window_bits=WB_RAW, level=raw_zlib.Z_BEST_SPEED) as strm:
            strm.next_in = self._addressof_bytearray(buf)
            strm.avail_in = len(buf)
            zbuf = ctypes.create_string_buffer(
                raw_zlib.deflateBound(strm, len(buf)))
            strm.next_out = ctypes.cast(
                ctypes.addressof(zbuf),
                ctypes.c_char_p)
            strm.avail_out = len(zbuf)
            err = raw_zlib.deflate(strm, raw_zlib.Z_FULL_FLUSH)
            self.assertEqual(raw_zlib.Z_OK, err)
            self.assertEqual(0, strm.avail_in)
            zbuf_len = len(zbuf) - strm.avail_out
            zbuf_finish = ctypes.create_string_buffer(
                raw_zlib.deflateBound(strm, len(buf)))
            strm.next_out = ctypes.cast(
                ctypes.addressof(zbuf_finish),
                ctypes.c_char_p)
            strm.avail_out = len(zbuf_finish)
            err = raw_zlib.deflate(strm, raw_zlib.Z_FINISH)
            self.assertEqual(raw_zlib.Z_STREAM_END, err)
            zbuf_finish_len = len(zbuf_finish) - strm.avail_out
        return len(buf), zbuf, zbuf_len, zbuf_finish, zbuf_finish_len

    def test_inflate_performace(self):
        # https://www.zlib.net/zlib_how.html
        # "buffers sizes on the order of 128K or 256K bytes should be used"
        buf_len, zbuf, zbuf_len, zbuf_finish, zbuf_finish_len = \
            self._deflate_blocks()
        print(file=sys.stderr)
        print('repeat %dB, finish %dB' % (zbuf_len, zbuf_finish_len),
              file=sys.stderr)
        with self._make_inflate_stream(window_bits=WB_RAW) as strm:
            buf = ctypes.create_string_buffer(buf_len)
            duration = 1
            deadline = time.time() + duration
            while time.time() < deadline:
                strm.next_in = ctypes.cast(
                    ctypes.addressof(zbuf),
                    ctypes.c_char_p)
                strm.avail_in = zbuf_len
                strm.next_out = ctypes.cast(
                    ctypes.addressof(buf),
                    ctypes.c_char_p)
                strm.avail_out = len(buf)
                err = raw_zlib.inflate(strm, raw_zlib.Z_NO_FLUSH)
                self.assertEqual(raw_zlib.Z_OK, err)
                self.assertEqual(0, strm.avail_in)
                self.assertEqual(0, strm.avail_out)
        gbs = strm.total_out / 1024. / 1024. / 1024. / duration
        print('inflate performance: %.3f GB/s' % gbs, file=sys.stderr)

    # Putting all possible pairs into one sequence:
    #
    # (1, 1) (1, 2) (1, 3)
    # (2, 1) (2, 2) (2, 3)
    # (3, 1) (3, 2) (3, 3)
    #
    # (1, 1)                                             | 1,1
    # (1, 2) (2, 2) (2, 1)                               | 2,2,1
    # (1, 3) (3, 3) (3, 2) (2, 3) (3, 1)                 | 3,3,2,3,1
    # (1, 4) (4, 4) (4, 2) (2, 4) (4, 3) (3, 4) (4, 1)   | 4,4,2,4,3,4,1

    @staticmethod
    def _sizes():
        yield 1
        yield 2
        for i in range(2, 19):  # up to and including 512k
            yield 2 ** i - 1
            yield 2 ** i
            yield 2 ** i + 1

    @classmethod
    def _sequence_of_sizes(cls):
        it_x = enumerate(cls._sizes())
        _, x0 = next(it_x)
        yield x0
        yield x0
        for i, x in it_x:
            yield x
            yield x
            it_y = iter(enumerate(cls._sizes()))
            _, y0 = next(it_y)
            for j, y in it_y:
                if j == i:
                    break
                yield y
                yield x
            yield y0

    @classmethod
    def _deflate(cls, ofp, gen, isizes, osizes):
        with cls._make_deflate_stream() as strm:
            it = iter(zip(isizes, osizes))
            ibuf = bytearray()
            stream_end = False
            while not stream_end:
                try:
                    isize, osize = next(it)
                    flush = raw_zlib.Z_NO_FLUSH
                except StopIteration:
                    isize, osize = len(ibuf), 8192
                    flush = raw_zlib.Z_FINISH
                iextra = isize - len(ibuf)
                if iextra > 0:
                    ibuf.extend(gen(iextra))
                obuf = ctypes.create_string_buffer(osize)
                strm.next_in = ctypes.addressof(
                    (ctypes.c_char * len(ibuf)).from_buffer(ibuf))
                strm.avail_in = isize
                strm.next_out = ctypes.addressof(obuf)
                strm.avail_out = osize
                err = raw_zlib.deflate(strm, flush)
                if err == raw_zlib.Z_STREAM_END and flush == raw_zlib.Z_FINISH:
                    stream_end = True
                elif err != raw_zlib.Z_OK:
                    raise Exception('deflate() failed: error %d' % err)
                del ibuf[:isize - strm.avail_in]
                ofp.write(obuf[:osize - strm.avail_out])
            print('deflate ok, total_in=%d total_out=%d' %
                  (strm.total_in, strm.total_out),
                  file=sys.stderr)

    @staticmethod
    def _read_n(fp, n):
        buf = bytearray()
        while n > 0:
            chunk = fp.read(n)
            if len(chunk) == 0:
                break
            buf.extend(chunk)
            n -= len(chunk)
        return buf

    def _inflate(self, ifp, gen, isizes, osizes):
        with self._make_inflate_stream() as strm:
            it = iter(zip(isizes, osizes))
            ibuf = bytearray()
            while True:
                try:
                    isize, osize = next(it)
                except StopIteration:
                    isize, osize = 8192, 16384
                iextra = isize - len(ibuf)
                if iextra > 0:
                    ibuf.extend(self._read_n(ifp, iextra))
                    if isize > len(ibuf):
                        isize = len(ibuf)
                obuf = ctypes.create_string_buffer(osize)
                strm.next_in = ctypes.addressof(
                    (ctypes.c_char * len(ibuf)).from_buffer(ibuf))
                strm.avail_in = isize
                strm.next_out = ctypes.addressof(obuf)
                strm.avail_out = osize
                err = raw_zlib.inflate(strm, raw_zlib.Z_NO_FLUSH)
                if err == raw_zlib.Z_STREAM_END:
                    break
                if err != raw_zlib.Z_OK:
                    raise Exception('inflate() failed: error %d' % err)
                del ibuf[:isize - strm.avail_in]
                self.assertEqual(
                    gen(osize - strm.avail_out),
                    obuf[:osize - strm.avail_out],
                    msg='total_in=%d total_out=%d' %
                        (strm.total_in, strm.total_out))
            print('inflate ok, total_in=%d total_out=%d' %
                  (strm.total_in, strm.total_out),
                  file=sys.stderr)

    def _test_deflate_inflate(self, isizes, osizes):
        with tempfile.TemporaryFile() as fp:
            with tempfile.TemporaryFile() as zfp:
                self._deflate(zfp, self._make_gen(), isizes, osizes)
                fp.seek(0)
                zfp.seek(0)
                self._inflate(zfp, self._make_gen(), isizes, osizes)

    def test_matrix(self):
        print(file=sys.stderr)
        isizes = list(self._sequence_of_sizes())
        osizes = reversed(isizes)
        self._test_deflate_inflate(isizes, osizes)


if __name__ == '__main__':
    unittest.main()
