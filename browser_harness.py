#!/usr/bin/env python3
"""In-process witness runner for the browser build (Pyodide).

The bundle (make_bundle.py) ships every CLI; in a browser there is no
fork/exec, so the two subprocess call sites (next_step.fetch,
machine_hunt.wanted) run their target CLIs in-process instead:

  bootstrap(bundle_bytes, player_json, workdir)  extract bundle + snapshot
  install()                                      patch subprocess.run
  run_cli(path, args)                            argv + captured stdout

Local check: `python3 browser_harness.py` bootstraps dist/bundle.tar.gz into
a temp dir, runs three witnesses in-process, and asserts byte-equal output
against a real subprocess run of the same commands.
"""
import io
import os
import pathlib
import subprocess
import sys
import tarfile
from contextlib import redirect_stderr, redirect_stdout


def bootstrap(bundle_bytes, player_json, workdir="/tacticus"):
    workdir = str(workdir)
    os.makedirs(workdir, exist_ok=True)
    with tarfile.open(fileobj=io.BytesIO(bundle_bytes)) as tf:
        try:
            tf.extractall(workdir, filter="data")   # trusted, self-built tar
        except TypeError:                           # Python < 3.12
            tf.extractall(workdir)
    pathlib.Path(workdir, "tacticus-player.json").write_text(player_json)
    if workdir not in sys.path:                     # sibling imports
        sys.path.insert(0, workdir)
    os.chdir(workdir)


def run_cli(path, args):
    """Execute one witness CLI like `python3 path args`; stdout/stderr captured."""
    import importlib.util
    path = str(path)
    spec = importlib.util.spec_from_file_location(
        "_witness_" + pathlib.Path(path).stem, path)
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)                    # defs only; main is guarded
    out, err, code = io.StringIO(), io.StringIO(), 0
    old_argv = sys.argv
    sys.argv = [path] + [str(a) for a in args]
    try:
        with redirect_stdout(out), redirect_stderr(err):
            mod.main()
    except SystemExit as e:
        if isinstance(e.code, int):
            code = e.code
        elif e.code is not None:
            code = 1
            err.write(f"{e.code}\n")
    finally:
        sys.argv = old_argv
    return subprocess.CompletedProcess(
        [path] + list(args), code, out.getvalue(), err.getvalue())


def install():
    """subprocess.run: python CLI commands go in-process, anything else real."""
    real = subprocess.run

    def run(cmd, *a, **kw):
        if len(cmd) >= 2 and str(cmd[1]).endswith(".py"):
            return run_cli(cmd[1], cmd[2:])
        return real(cmd, *a, **kw)

    subprocess.run = run


def _selftest():
    here = pathlib.Path(__file__).resolve().parent
    bundle = here / "dist" / "bundle.tar.gz"
    if not bundle.exists():
        subprocess.run([sys.executable, str(here / "make_bundle.py")], check=True)
    player = (here / "tacticus-player.json").read_text()
    cases = [
        ["rank_up_report.py", "--energy", "--json", "--top", "5"],
        ["next_step.py", "--json", "--top", "3"],
        ["machine_hunt.py", "--json", "--top", "5"],
    ]
    # ground truth first: real subprocess from the repo tree
    truth = [subprocess.run([sys.executable, str(here / c[0])] + c[1:],
                            capture_output=True, text=True) for c in cases]
    import tempfile
    with tempfile.TemporaryDirectory() as td:
        bootstrap(bundle.read_bytes(), player, td)
        install()
        for case, want in zip(cases, truth):
            got = run_cli(case[0], case[1:])
            assert got.returncode == want.returncode, (
                f"{case[0]} rc {got.returncode} != {want.returncode}: {got.stderr}")
            assert got.stdout == want.stdout, f"{case[0]} stdout differs"
            print(f"ok  {case[0]} {' '.join(case[1:])}")
    print("SELFTEST PASS")


if __name__ == "__main__":
    _selftest()
