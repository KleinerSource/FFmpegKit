"""CPU checks of the actual Metal math; these do not replace Apple/GPU tests.

Run after source preparation, or set KSPLAYER_SOURCE to the component checkout.
Only float-vector syntax is adapted for C++; shader equations are not copied
into a second Python implementation. Requires clang++ or g++.
"""
import ctypes
import _ctypes
import math
import os
from pathlib import Path
import re
import shutil
import subprocess
import sys
import tempfile
import unittest


ROOT = Path(__file__).resolve().parents[2]
SOURCE = Path(os.environ.get("KSPLAYER_SOURCE", ROOT / "build/ios-native/pods/KSPlayer"))


def function(source, name):
    start = source.index("float3 " + name + "(") if "float3 " + name + "(" in source else source.index("float " + name + "(")
    body = source.index("{", start)
    depth = 1
    end = body + 1
    while depth:
        depth += (source[end] == "{") - (source[end] == "}")
        end += 1
    return source[start:end]


class ColorReferenceTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        shader = (SOURCE / "Sources/KSPlayer/Metal/Shaders.metal").read_text(encoding="utf-8")
        compiler = shutil.which("clang++") or shutil.which("g++")
        if compiler is None:
            raise RuntimeError("Color reference checks require clang++ or g++")
        cls.temp = tempfile.TemporaryDirectory()
        directory = Path(cls.temp.name)
        functions = "\n".join(function(shader, name) for name in ("colorPQDecode", "colorPQEncode", "colorRows", "colorDoviReshape", "colorBT2390", "colorYCbCr", "colorHDR"))
        functions = functions.replace("constant ", "const ").replace(".xyz", ".xyz()").replace(".yz", ".yz()")
        adapter = r"""
#include <cmath>
#include <algorithm>
using uint = unsigned;
using std::pow;
float max(float a,float b) { return std::max(a,b); }
float clamp(float a,float lo,float hi) { return std::clamp(a,lo,hi); }
struct float2 { float x,y; };
struct bool3 { bool x,y,z; };
struct float3 {
    float x,y,z;
    float3() = default;
    explicit float3(float a):x(a),y(a),z(a) {}
    float3(float a,float b,float c):x(a),y(b),z(c) {}
    float2 yz() const { return {y,z}; }
    float& operator[](uint n) { return (&x)[n]; }
    float3& operator*=(float3 a) { x*=a.x;y*=a.y;z*=a.z;return *this; }
};
struct float4 {
    float x,y,z,w;
    float4() = default;
    float4(float a,float b,float c,float d):x(a),y(b),z(c),w(d) {}
    float operator[](uint n) const { return (&x)[n]; }
    float3 xyz() const { return {x,y,z}; }
    float4& operator*=(float4 a) { x*=a.x;y*=a.y;z*=a.z;w*=a.w;return *this; }
};
float3 clamp(float3 v,float lo,float hi) { return {clamp(v.x,lo,hi),clamp(v.y,lo,hi),clamp(v.z,lo,hi)}; }
float dot(float3 a,float3 b) { return a.x*b.x+a.y*b.y+a.z*b.z; }
float dot(float4 a,float4 b) { return a.x*b.x+a.y*b.y+a.z*b.z+a.w*b.w; }
float2 operator-(float2 a,float b) { return {a.x-b,a.y-b}; }
float2 operator*(float2 a,float b) { return {a.x*b,a.y*b}; }
float3 max(float3 a,float b) { return {max(a.x,b),max(a.y,b),max(a.z,b)}; }
float3 pow(float3 a,float3 b) { return {pow(a.x,b.x),pow(a.y,b.y),pow(a.z,b.z)}; }
float3 exp(float3 a) { return {std::exp(a.x),std::exp(a.y),std::exp(a.z)}; }
bool3 operator>(float3 a,float b) { return {a.x>b,a.y>b,a.z>b}; }
float3 select(float3 a,float3 b,bool3 c) { return {c.x?b.x:a.x,c.y?b.y:a.y,c.z?b.z:a.z}; }
"""
        for symbol in ("+", "-", "*", "/"):
            adapter += f"float3 operator{symbol}(float3 a,float3 b) {{ return {{a.x{symbol}b.x,a.y{symbol}b.y,a.z{symbol}b.z}}; }}\n"
            adapter += f"float3 operator{symbol}(float3 a,float b) {{ return a{symbol}float3(b); }}\n"
            adapter += f"float3 operator{symbol}(float a,float3 b) {{ return float3(a){symbol}b; }}\n"
        adapter += "float3& operator*=(float3& a,float b) { a=a*b;return a; }\n"
        wrapper = r"""
extern "C" float pq_encode(float x) { return colorPQEncode(float3(x)).x; }
extern "C" float pq_decode(float x) { return colorPQDecode(float3(x)).x; }
extern "C" float eetf(float x,float sourceMin,float sourceMax,float targetMax) { return colorBT2390(x,sourceMin,sourceMax,targetMax); }
extern "C" void reshape(const float* signal,const float* data,float* result) {
    auto output=colorDoviReshape(float3(signal[0],signal[1],signal[2]),reinterpret_cast<const float4*>(data));
    result[0]=output.x;result[1]=output.y;result[2]=output.z;
}
extern "C" void color(const float* signal,const float* data,bool rgb,float* result) {
    auto output=colorHDR(float3(signal[0],signal[1],signal[2]),reinterpret_cast<const float4*>(data),rgb);
    result[0]=output.x;result[1]=output.y;result[2]=output.z;
}
"""
        cpp = directory / "color.cpp"
        cpp.write_text(adapter + functions + wrapper, encoding="utf-8")
        library = directory / ("color.dll" if sys.platform == "win32" else "color.so")
        command = [compiler, "-std=c++17", "-shared", "-O2", str(cpp), "-o", str(library)]
        if sys.platform == "win32":
            command += ["-static-libgcc", "-static-libstdc++"]
        else:
            command += ["-fPIC"]
        subprocess.run(command, check=True, capture_output=True, text=True, encoding="utf-8")
        cls.library = ctypes.CDLL(str(library))
        for name, count in (("pq_encode", 1), ("pq_decode", 1), ("eetf", 4)):
            func = getattr(cls.library, name)
            func.argtypes = [ctypes.c_float] * count
            func.restype = ctypes.c_float
        cls.library.reshape.argtypes = [ctypes.POINTER(ctypes.c_float)] * 3
        cls.library.color.argtypes = [ctypes.POINTER(ctypes.c_float), ctypes.POINTER(ctypes.c_float), ctypes.c_bool, ctypes.POINTER(ctypes.c_float)]

    @classmethod
    def tearDownClass(cls):
        if sys.platform == "win32":
            _ctypes.FreeLibrary(cls.library._handle)
        else:
            _ctypes.dlclose(cls.library._handle)
        del cls.library
        cls.temp.cleanup()

    def test_st2084_published_reference_levels(self):
        for nits, pq in ((0, 0), (100, 0.5080784215), (1000, 0.7518270962), (10000, 1)):
            with self.subTest(nits=nits):
                self.assertAlmostEqual(self.library.pq_encode(nits / 10000), pq, delta=2e-5)
                self.assertAlmostEqual(self.library.pq_decode(pq) * 10000, nits, delta=max(0.002, nits * 0.0001))

    def test_pq_roundtrip_near_black_through_peak(self):
        for power in range(-8, 1):
            value = 10 ** power
            decoded = self.library.pq_decode(self.library.pq_encode(value))
            self.assertAlmostEqual(decoded, value, delta=max(1e-10, value * 0.0002))

    def test_bt2390_is_bounded_and_monotone(self):
        peak = self.library.pq_encode(0.1)
        target = self.library.pq_encode(0.01)
        values = [self.library.eetf(i / 1000, 0, peak, target) for i in range(1001)]
        self.assertEqual(values[0], 0)
        self.assertAlmostEqual(values[-1], target, delta=1e-6)
        self.assertTrue(all(math.isfinite(x) and 0 <= x <= target for x in values))
        self.assertTrue(all(a <= b + 1e-6 for a, b in zip(values, values[1:])))

    def test_bt2390_identity_without_range_compression(self):
        peak = self.library.pq_encode(0.01)
        for i in range(101):
            pq = peak * i / 100
            self.assertAlmostEqual(self.library.eetf(pq, 0, peak, peak), pq, delta=1e-6)

    @staticmethod
    def identity_curves():
        data = (ctypes.c_float * (186 * 4))()
        for component in range(3):
            base = (9 + component * 59) * 4
            data[base], data[base + 2] = 1, 1
            data[base + 4] = 1
            data[base + 3 * 4 + 1] = 1
        return data

    def run_reshape(self, signal, data):
        output = (ctypes.c_float * 3)()
        self.library.reshape((ctypes.c_float * 3)(*signal), data, output)
        return tuple(output)

    def run_color(self, signal, data, rgb=True):
        output = (ctypes.c_float * 3)()
        self.library.color((ctypes.c_float * 3)(*signal), data, rgb, output)
        return tuple(output)

    def test_dovi_offset_matrix_pq_pipeline_preserves_neutral_hdr(self):
        data = self.identity_curves()
        data[0], data[1], data[2] = 1, 3, 1
        data[4], data[5], data[6] = self.library.pq_encode(0.1), self.library.pq_encode(0.01), 1
        # IPT neutral chroma must be subtracted before the nonlinear matrix.
        for i, row in enumerate(((1, 0.1, 0.2), (1, -0.1, -0.2), (1, 0.3, -0.4))):
            for j, value in enumerate(row):
                data[(2 + i) * 4 + j] = value
            data[(6 + i) * 4 + i] = 1
        data[5 * 4 + 1], data[5 * 4 + 2] = 0.5, 0.5
        pq = self.library.pq_encode(0.01)
        output = self.run_color((pq, 0.5, 0.5), data, False)
        for actual in output:
            self.assertAlmostEqual(actual, pq, delta=2e-5)

    def test_pq_and_hlg_sdr_peak_and_black(self):
        for transfer, white in ((1, self.library.pq_encode(0.1)), (2, 1)):
            data = self.identity_curves()
            data[0], data[1], data[2] = 1, transfer, 0
            data[4], data[5], data[6] = self.library.pq_encode(0.1), self.library.pq_encode(0.01), 1
            for actual in self.run_color((white,) * 3, data):
                self.assertAlmostEqual(actual, 1, delta=2e-5)
            for actual in self.run_color((0,) * 3, data):
                self.assertAlmostEqual(actual, 0, delta=1e-6)

    def test_p010_legal_range_normalization(self):
        data = self.identity_curves()
        data[0], data[1], data[2] = 65535 / (1023 * 64), 2, 0
        data[4], data[5], data[6], data[7] = self.library.pq_encode(0.1), self.library.pq_encode(0.01), 1, 2
        data[2 * 4 + 3], data[3 * 4 + 3] = 64 / 1023, 512 / 1023
        data[4 * 4 + 3], data[5 * 4 + 3] = 1023 / 876, 1023 / 896
        for code, expected in ((64, 0), (940, 1)):
            output = self.run_color((code * 64 / 65535, 512 * 64 / 65535, 512 * 64 / 65535), data, False)
            for actual in output:
                self.assertAlmostEqual(actual, expected, delta=2e-5)

    def test_piecewise_polynomial_and_unmodified_other_components(self):
        data = self.identity_curves()
        base = 9 * 4
        data[base] = 2
        data[base + 4] = 0.5
        data[base + 5] = 1
        data[base + 3 * 4 + 1] = 2
        data[base + 10 * 4] = 1
        self.assertAlmostEqual(self.run_reshape((0.25, 0.3, 0.4), data)[0], 0.5)
        for actual, expected in zip(self.run_reshape((0.75, 0.3, 0.4), data), (1, 0.3, 0.4)):
            self.assertAlmostEqual(actual, expected, places=6)

    def test_mmr_order_one_two_three_reference_vectors(self):
        data = self.identity_curves()
        piece = (9 + 3) * 4
        data[piece] = 0.01
        for i, value in enumerate((0.1, 0.2, 0.3)):
            data[piece + 4 + i] = value
        for i, value in enumerate((0.4, 0.5, 0.6, 0.7)):
            data[piece + 8 + i] = value
        for order in (2, 3):
            for i in (0, 1, 2, 4, 5, 6, 7):
                data[piece + 4 + (order - 1) * 8 + i] = order / 10
        for order, expected in ((1, 0.3628), (2, 0.4257952), (3, 0.4562361472)):
            data[piece + 3] = order
            result = self.run_reshape((0.2, 0.3, 0.4), data)
            self.assertAlmostEqual(result[0], expected, places=6)
            self.assertAlmostEqual(result[1], 0.3, places=6)
            self.assertAlmostEqual(result[2], 0.4, places=6)

    def test_eight_pieces_reach_last_of_186_uniform_rows(self):
        data = self.identity_curves()
        for component in range(3):
            base = (9 + component * 59) * 4
            data[base] = 8
            for i in range(8):
                data[base + 4 + i] = (i + 1) / 8
                data[base + (3 + i * 7) * 4] = (i + 1) / 8
                data[base + (3 + i * 7) * 4 + 1] = 0
        # Last blue piece: order 3, only coefficient (x*y*z)^3 = 1.
        piece = (9 + 2 * 59 + 3 + 7 * 7) * 4
        data[piece], data[piece + 3] = 0, 3
        data[185 * 4 + 3] = 1
        output = self.run_reshape((0.9, 0.9, 0.9), data)
        for actual, expected in zip(output, (1, 1, 0.387420489)):
            self.assertAlmostEqual(actual, expected, places=6)

    def test_ffmpeg_fate_dv84_rpu_reference(self):
        # Official pinned n8.1 fixture: tests/ref/fate/hevc-dv-rpu (dv84.mov).
        # Expected PQ results were evaluated independently in float64 from its
        # published polynomial/MMR and rational matrices. This is a math oracle,
        # not a claim that the encoded clip has been rendered on an Apple GPU.
        ffmpeg = Path(os.environ.get("FFMPEG_SOURCE", ROOT / "build/ios-native/sources/FFmpeg"))
        fixture = (ffmpeg / "tests/ref/fate/hevc-dv-rpu").read_text(encoding="utf-8").split("[/FRAME]")[0]
        data = self.identity_curves()
        data[0], data[1], data[2] = 1, 3, 1
        for component, block in enumerate(re.findall(r"\[COMPONENT\](.*?)\[/COMPONENT\]", fixture, re.S)):
            base = (9 + component * 59) * 4
            pivots = list(map(int, re.search(r"pivots=(.*)", block).group(1).split()))
            data[base], data[base + 1], data[base + 2] = len(pivots) - 1, pivots[0] / 1023, pivots[-1] / 1023
            for i, pivot in enumerate(pivots[1:]):
                data[base + 4 + i] = pivot / 1023
            for i, segment in enumerate(re.findall(r"\[PIECE\](.*?)\[/PIECE\]", block, re.S)):
                params = dict(re.findall(r"^([^=\n]+)=(.*)$", segment, re.M))
                piece = base + (3 + i * 7) * 4
                if params["mapping_idc"] == "0":
                    for j, coefficient in enumerate(map(int, params["poly_coef"].split())):
                        data[piece + j] = coefficient / (2 ** 23)
                else:
                    data[piece], data[piece + 3] = int(params["mmr_constant"]) / (2 ** 23), int(params["mmr_order"])
                    for j, coefficient in enumerate(map(int, params["mmr_coef"].split())):
                        order, term = divmod(j, 7)
                        index = piece + (1 + order * 2) * 4 + term + (term >= 3)
                        data[index] = coefficient / (2 ** 23)
        for key, start in (("ycc_to_rgb_matrix", 2), ("rgb_to_lms_matrix", 6), ("ycc_to_rgb_offset", 5)):
            values = re.search(r"^" + key + r"=(.*)$", fixture, re.M).group(1).split()
            for i, value in enumerate(values):
                numerator, denominator = map(int, value.split("/"))
                row, column = divmod(i, 3)
                data[(start + row) * 4 + column] = numerator / denominator
        for codes, expected in (
            ((500, 512, 512), (0.4442779428, 0.4414434236, 0.4463302449)),
            ((100, 450, 600), (0.2315656897, 0.0215159828, 0.0000007310)),
            ((930, 300, 800), (0.9999914116, 0.6374140748, 0.4514180419)),
        ):
            output = self.run_color(tuple(code / 1023 for code in codes), data, False)
            for actual, reference in zip(output, expected):
                self.assertAlmostEqual(actual, reference, delta=5e-5)


if __name__ == "__main__":
    unittest.main()
