import argparse
import json
import os
import pathlib
import sys

from dotenv import load_dotenv


PROJECT_ROOT = pathlib.Path(__file__).resolve().parents[1]
OWL_ROOT = PROJECT_ROOT / "owl"
sys.path.insert(0, str(OWL_ROOT))

from camel.agents import ChatAgent
from camel.models import ModelFactory
from camel.toolkits import (
    CodeExecutionToolkit,
    ExcelToolkit,
    FileToolkit,
    FunctionTool,
    ImageAnalysisToolkit,
    SearchToolkit,
)
from camel.types import ModelPlatformType

from owl.utils import DocumentProcessingToolkit
from owl.utils.gaia import GAIABenchmark


def make_model():
    api_url = os.environ["VLLM_API_URL"]
    api_key = os.environ["VLLM_API_KEY"]
    model_name = os.environ["VLLM_MODEL_NAME"]
    return ModelFactory.create(
        model_platform=ModelPlatformType.OPENAI_COMPATIBLE_MODEL,
        model_type=model_name,
        url=api_url,
        api_key=api_key,
        model_config_dict={"temperature": 0},
    )


def make_agent_kwargs():
    user_model = make_model()
    assistant_model = make_model()
    document_model = make_model()
    image_model = make_model()

    search_toolkit = SearchToolkit()
    document_toolkit = DocumentProcessingToolkit(model=document_model)
    image_toolkit = ImageAnalysisToolkit(model=image_model)
    code_toolkit = CodeExecutionToolkit(sandbox="subprocess", verbose=True)
    excel_toolkit = ExcelToolkit()
    file_toolkit = FileToolkit()

    assistant_tools = [
        FunctionTool(search_toolkit.search_duckduckgo),
        FunctionTool(search_toolkit.search_wiki),
        FunctionTool(document_toolkit.extract_document_content),
        FunctionTool(image_toolkit.ask_question_about_image),
        FunctionTool(code_toolkit.execute_code),
        FunctionTool(excel_toolkit.extract_excel_content),
        *file_toolkit.get_tools(),
    ]

    return (
        {"model": user_model},
        {"model": assistant_model, "tools": assistant_tools},
    )


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument(
        "--env",
        default=str(PROJECT_ROOT / "owl" / "owl" / ".env.gaia"),
    )
    parser.add_argument(
        "--data-dir",
        default=str(PROJECT_ROOT / "dataset" / "gaia"),
    )
    parser.add_argument(
        "--save-to",
        default=r"D:\mas_runs\gaia_owl\gaia_validation_results.json",
    )
    parser.add_argument("--on", choices=["valid", "test"], default="valid")
    parser.add_argument("--level", default="all")
    parser.add_argument("--subset", type=int, default=1)
    parser.add_argument("--idx", type=int, nargs="*", default=None)
    args = parser.parse_args()
    if args.subset is not None and args.subset < 1:
        raise ValueError("--subset must be >= 1")

    env_path = pathlib.Path(args.env)
    load_dotenv(dotenv_path=str(env_path), override=True)

    missing = [
        key
        for key in ["VLLM_API_URL", "VLLM_MODEL_NAME", "VLLM_API_KEY"]
        if not os.environ.get(key)
    ]
    if missing:
        raise RuntimeError(f"Missing required env vars in {env_path}: {missing}")

    save_to = pathlib.Path(args.save_to)
    save_to.parent.mkdir(parents=True, exist_ok=True)

    level = args.level
    if level != "all":
        level = int(level)

    user_agent_kwargs, assistant_agent_kwargs = make_agent_kwargs()

    benchmark = GAIABenchmark(
        data_dir=args.data_dir,
        save_to=str(save_to),
        processes=1,
    ).load(force_download=False)

    summary = benchmark.run(
        user_role_name="user",
        assistant_role_name="assistant",
        user_agent_kwargs=user_agent_kwargs,
        assistant_agent_kwargs=assistant_agent_kwargs,
        on=args.on,
        level=level,
        subset=args.subset,
        idx=args.idx,
        save_result=True,
    )

    summary_path = save_to.with_suffix(".summary.json")
    with open(summary_path, "w", encoding="utf-8") as f:
        json.dump(summary, f, indent=2, ensure_ascii=False)

    print(json.dumps(summary, indent=2, ensure_ascii=False))
    print(f"results: {save_to}")
    print(f"summary: {summary_path}")


if __name__ == "__main__":
    main()
