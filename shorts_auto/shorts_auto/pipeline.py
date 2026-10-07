from pathlib import Path

from .project import Project


def run_pipeline(input_dir: Path, output: Path, assets: Path, cfg, seed=None, log=print):
    p = Project(input_dir, output, assets, cfg, log, seed)
    p.run_all(on_step=lambda k, st: st == "running" and log(f"\n[{k}]"))
    return p
