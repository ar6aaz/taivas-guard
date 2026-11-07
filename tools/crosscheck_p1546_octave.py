"""Recalculates every P.1546 finding in an export with the upstream MATLAB code, in GNU Octave.

Checks the Python port for these inputs only: the inputs, land/sea split and worst point come from
the rule. Not part of pytest because it needs Docker and an upstream checkout:

    git clone https://github.com/eeveetza/p1546 /path/p1546 && git -C /path/p1546 checkout 0e42639
    uv run python tools/crosscheck_p1546_octave.py --upstream /path/p1546 --cases outputs/ific_3076/cases.json
"""
import argparse
import csv
import json
import subprocess
import tempfile
from pathlib import Path

IMAGE = "gnuoctave/octave:9.2.0"


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--upstream", type=Path, required=True)
    parser.add_argument("--cases", type=Path, required=True)
    args = parser.parse_args()
    cases = [c for c in json.loads(args.cases.read_text()) if c["details"].get("method") == "p1546"]
    with tempfile.TemporaryDirectory() as folder:
        lines = ["pkg_ok=1;", "addpath('/up/matlab');", "fid=fopen('/out/upstream.csv','w');"]
        for case in cases:
            inputs = case["details"]["calc_inputs"]
            lengths = " ".join(repr(s["length_km"]) for s in inputs["segments"])
            paths = ",".join("'Land'" if s["kind"] == "land" else "'Cold'" for s in inputs["segments"])
            lines.append(
                f"[E,L]=P1546FieldStrMixed({inputs['frequency_mhz']!r},10,{inputs['tx_effective_height_m']!r},10,10,'Rural',"
                f"[{lengths}],{{{paths}}},0,'PTx',{inputs['erp_kw']!r},'ha',{inputs['tx_height_agl_m']!r});"
            )
            lines.append(f"fprintf(fid,'{case['notice_ids'][0]},%.6f\\n',E);")
        lines.append("fclose(fid);")
        (Path(folder) / "run.m").write_text("\n".join(lines) + "\n")
        subprocess.run(
            ["docker", "run", "--rm", "--platform", "linux/amd64", "--network", "none",
             "-v", f"{args.upstream.resolve()}:/up:ro", "-v", f"{folder}:/out", IMAGE, "octave", "--no-gui", "--quiet", "/out/run.m"],
            check=True, timeout=600,
        )
        upstream = {key: float(value) for key, value in csv.reader((Path(folder) / "upstream.csv").open())}
    differences = []
    for case in cases:
        key, value = case["notice_ids"][0], case["details"]["value_db"]
        differences.append(abs(upstream[key] - value))
        print(key, f"{value:.3f}", f"{upstream[key]:.3f}", f"{upstream[key] - value:+.4f}")
    print(f"compared {len(differences)}, largest difference {max(differences):.4f} dB")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
