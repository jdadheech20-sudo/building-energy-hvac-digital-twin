from __future__ import annotations

import argparse
import json
from datetime import datetime
from pathlib import Path


def _aware_datetime(value: str) -> datetime:
    parsed = datetime.fromisoformat(value)
    if parsed.tzinfo is None or parsed.utcoffset() is None:
        raise argparse.ArgumentTypeError("timestamp must include an explicit UTC offset")
    return parsed


def main(argv=None):
    parser = argparse.ArgumentParser(prog="campus-twin")
    commands = parser.add_subparsers(dest="command", required=True)
    serve = commands.add_parser("serve", help="run the campus API")
    serve.add_argument("--host", default="127.0.0.1")
    serve.add_argument("--port", type=int, default=8000)
    benchmark = commands.add_parser("benchmark-ashrae", help="evaluate the labeled ASHRAE training data")
    benchmark.add_argument("dataset_root")
    benchmark.add_argument("--config", required=True, help="JSON config with units, timezones, locked split, and held-out IDs")
    benchmark.add_argument("--output", default="outputs/campus/ashrae_benchmark.json")
    benchmark.add_argument("--n-estimators", type=int, default=350)
    train = commands.add_parser("train-rl", help="train RLlib PPO on synthetic simulation only")
    train.add_argument("--peak-limit-kw", type=float, required=True)
    train.add_argument("--iterations", type=int, default=10)
    train.add_argument("--output", default="outputs/campus/rl_training_summary.json")
    reference = commands.add_parser("reference", help="reproduce the original synthetic 1R1C workflow")
    reference.add_argument("--dataset", default="datasets/example/building_timeseries.csv")
    reference.add_argument("--output", default="outputs/campus/hvac_reference")
    args = parser.parse_args(argv)

    if args.command == "serve":
        import uvicorn
        uvicorn.run("campus_twin.main:app", host=args.host, port=args.port, reload=False)
    elif args.command == "benchmark-ashrae":
        from .forecasting import load_ashrae_training_data, run_ashrae_benchmark
        config = json.loads(Path(args.config).read_text(encoding="utf-8"))
        frame = load_ashrae_training_data(
            args.dataset_root,
            site_timezones={int(k): v for k, v in config["site_timezones"].items()},
            meter_units=config["meter_units"],
        )
        result = run_ashrae_benchmark(
            frame,
            train_end=_aware_datetime(config["train_end"]),
            calibration_end=_aware_datetime(config["calibration_end"]),
            test_start=_aware_datetime(config["test_start"]),
            heldout_building_ids=set(config["heldout_building_ids"]),
            n_estimators=args.n_estimators,
        )
        output = Path(args.output)
        output.parent.mkdir(parents=True, exist_ok=True)
        output.write_text(json.dumps(result, indent=2) + "\n", encoding="utf-8")
        print(json.dumps({"output": str(output.resolve()), "meters": sorted(result["per_meter"])}, indent=2))
    elif args.command == "train-rl":
        from .train_rllib import train
        result = train(args.iterations, peak_limit_kw=args.peak_limit_kw)
        output = Path(args.output)
        output.parent.mkdir(parents=True, exist_ok=True)
        output.write_text(json.dumps(result, indent=2) + "\n", encoding="utf-8")
        print(json.dumps({"output": str(output.resolve()), "scientific_status": result["scientific_status"]}, indent=2))
    else:
        from building_hvac_twin.workflow import run
        result = run(args.dataset, args.output)
        print(json.dumps({"output": str(Path(args.output).resolve()), "validation_metrics": result["validation_metrics"]}, indent=2))


if __name__ == "__main__":
    main()
