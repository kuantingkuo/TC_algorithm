"""Internal subprocess entry points; keep environments and thread pools isolated."""
import argparse
import os
from pathlib import Path
from .config import load


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument('stage', choices=['build', 'detect', 'track', 'lifetime'])
    parser.add_argument('--config', required=True)
    parser.add_argument('--case')
    args = parser.parse_args()
    cfg = load(args.config)
    case = args.case or os.environ.get('TC_CASE') or cfg.get('case')
    if not case and len(cfg.get('cases', [])) == 1:
        case = cfg['cases'][0]
    if not case:
        raise ValueError('Specify --case')
    cfg['case'] = case
    out = Path(cfg['output_path']) / case
    if args.stage == 'build':
        from .inputs import open_input
        from tc_algorithm import irt_params, pre
        from .build import prepare
        with open_input(cfg, case) as ds:
            prepare(irt_params(pre(ds)), cfg, out)
    elif args.stage == 'detect':
        from tc_algorithm import main as detect, _sanitize_for_netcdf_write
        # The build stage must have completed before scientific processing.
        cfg['_build_dir'] = (out / '.irt_build_dir').read_text().strip()
        ds = detect(case, cfg.get('case_path', ''), str(out), cfg.get('file_pattern', ''),
                    cfg.get('invert_vorticity_SH', True), config=cfg)
        ds, encoding = _sanitize_for_netcdf_write(ds)
        ds.to_netcdf(out / (case + '.TC.nc'), engine='h5netcdf', encoding=encoding)
        ds.close()
    elif args.stage == 'track':
        from .tracking import run
        run(cfg)
    else:
        from TC_lifetime import main as lifetime
        lifetime(case, None, str(out), config=cfg)


if __name__ == '__main__':
    main()
