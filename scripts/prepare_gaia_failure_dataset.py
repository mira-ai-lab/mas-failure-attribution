import argparse
from pathlib import Path

import pandas as pd


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument(
        "--input",
        default=r"D:\code_re\mas-failure-attribution\dataset\gaia\2023\validation\metadata.parquet",
    )
    parser.add_argument(
        "--output",
        default=r"D:\mas_runs\gaia_failure\gaia_validation.parquet",
    )
    parser.add_argument("--max-samples", type=int, default=None)
    args = parser.parse_args()

    src = Path(args.input)
    out = Path(args.output)
    split_dir = src.parent

    df = pd.read_parquet(src)
    if args.max_samples is not None:
        df = df.head(args.max_samples)

    rows = []
    for _, row in df.iterrows():
        file_name = row.get("file_name")
        file_path = ""
        if isinstance(file_name, str) and file_name:
            file_path = str(split_dir / file_name)
        question = str(row["Question"])
        if file_path:
            question = f"{question}\nAttached file path: {file_path}"
        rows.append(
            {
                "task_id": str(row["task_id"]),
                "question_ID": str(row["task_id"]),
                "question": question,
                "reference_solution": "" if pd.isna(row["Final answer"]) else str(row["Final answer"]),
                "test": "",
                "data_source": "gaia",
                "file_name": file_path,
                "level": str(row["Level"]),
            }
        )

    out.parent.mkdir(parents=True, exist_ok=True)
    pd.DataFrame(rows).to_parquet(out, index=False)
    print(f"wrote {len(rows)} rows to {out}")


if __name__ == "__main__":
    main()
