import argparse
import base64
import hashlib
from pathlib import Path

import pandas as pd


QUERY_TEMPLATE = """
{Question}

Your response should be in the following format:
Explanation: {{your explanation for your final answer}}
Exact Answer: {{your succinct, final answer}}
Confidence: {{your confidence score between 0% and 100% for your answer}}
""".strip()


def derive_key(password: str, length: int) -> bytes:
    hasher = hashlib.sha256()
    hasher.update(password.encode())
    key = hasher.digest()
    return key * (length // len(key)) + key[: length % len(key)]


def decrypt(ciphertext_b64: str, password: str) -> str:
    encrypted = base64.b64decode(ciphertext_b64)
    key = derive_key(password, len(encrypted))
    decrypted = bytes(a ^ b for a, b in zip(encrypted, key))
    return decrypted.decode()


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument(
        "--input",
        default=r"D:\code_re\mas-failure-attribution\dataset\browsecomp\browse_comp_test_set.csv",
    )
    parser.add_argument(
        "--output",
        default=r"D:\code_re\mas-failure-attribution\dataset\browsecomp\browsecomp.parquet",
    )
    parser.add_argument("--max-samples", type=int, default=None)
    args = parser.parse_args()

    src = Path(args.input)
    out = Path(args.output)
    df = pd.read_csv(src)
    if args.max_samples is not None:
        df = df.head(args.max_samples)

    rows = []
    for idx, row in df.iterrows():
        problem = decrypt(str(row["problem"]), str(row["canary"]))
        answer = decrypt(str(row["answer"]), str(row["canary"]))
        task_id = f"browsecomp_{idx:04d}"
        rows.append(
            {
                "task_id": task_id,
                "question_ID": task_id,
                "question": QUERY_TEMPLATE.format(Question=problem),
                "reference_solution": answer,
                "test": "",
                "data_source": "browsecomp",
                "file_name": "",
                "problem_topic": "" if pd.isna(row.get("problem_topic")) else str(row.get("problem_topic")),
            }
        )

    out.parent.mkdir(parents=True, exist_ok=True)
    pd.DataFrame(rows).to_parquet(out, index=False)
    print(f"wrote {len(rows)} rows to {out}")


if __name__ == "__main__":
    main()
