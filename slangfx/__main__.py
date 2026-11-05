import argparse
import sys

from .build import convert, Options, ConvertError


def main():
    ap = argparse.ArgumentParser(prog="slangfx", description="Convert a RetroArch .slangp preset to a ReShade .fx")
    ap.add_argument("preset")
    ap.add_argument("-o", "--output", required=True)
    ap.add_argument("--native", help="native resolution of the system, e.g. 480x272")
    ap.add_argument("--pixel-size", type=float, help="screen pixels per source pixel, e.g. 2.67")
    ap.add_argument("--set", action="append", default=[], metavar="NAME=VALUE", help="override a parameter")
    a = ap.parse_args()
    native = tuple(int(v) for v in a.native.lower().split("x")) if a.native else None
    params = dict((kv.split("=", 1)[0], float(kv.split("=", 1)[1])) for kv in a.set)
    try:
        fx, luts, plist = convert(a.preset, Options(native=native, pixel_size=a.pixel_size, params=params))
    except ConvertError as e:
        print(f"error: {e}", file=sys.stderr)
        return 1
    with open(a.output, "w") as f:
        f.write(fx)
    for src, rel in luts:
        print(f"lut: {src} -> Textures/{rel}")
    print(f"ok: {len(plist)} parameters")
    return 0


if __name__ == "__main__":
    sys.exit(main())
