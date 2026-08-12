"""Isolated launcher for the operator-provided Auto report project."""
from __future__ import annotations

import argparse
import os
import sys
import types
from pathlib import Path


def _inline_loader(source: Path, config):
    def load(root_lot_id: str):
        import pandas as pd

        if not source.is_file():
            return pd.DataFrame()
        data = pd.read_csv(source)
        if data.empty:
            return data
        root_col = "root_lot_id" if "root_lot_id" in data.columns else "lot_id"
        if root_col in data.columns:
            data = data[data[root_col].astype(str) == str(root_lot_id)].copy()
        try:
            sheet_path = Path(str(config.get("inline_file_path")))
            sheet_name = str(config.get("inline_file_sheet") or "INLINE_1")
            setting = pd.read_excel(sheet_path, sheet_name=sheet_name, engine="openpyxl")
            from My_Function import _filter_inline_by_vehicle

            setting = _filter_inline_by_vehicle(setting, config.get("vehicle"))
            if "STEP_DESC" in setting.columns and "STEP_DESC" in data.columns:
                data = pd.merge(data, setting, on="STEP_DESC", how="left")
        except Exception as exc:
            print(f"[WARN] Flow INLINE 설정 병합 실패: {exc}")
        if "ITEMNAME" not in data.columns:
            data["ITEMNAME"] = data.get("STEP_DESC", "")
        for name in ("fab_value", "spc_ctrl_spec_high", "spc_ctrl_spec_limit", "spc_ctrl_spec_low"):
            if name in data.columns:
                data[name] = pd.to_numeric(data[name], errors="coerce")
        cd_mask = data.get("item_id", pd.Series(dtype=str)).astype(str).str.match(r"^CD\d+$")
        if len(cd_mask) == len(data):
            for name in ("fab_value", "spc_ctrl_spec_high", "spc_ctrl_spec_limit", "spc_ctrl_spec_low"):
                if name in data.columns:
                    data.loc[cd_mask, name] = data.loc[cd_mask, name] * 1000
        return data

    return load


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--runtime", required=True)
    parser.add_argument("--trigger", required=True)
    parser.add_argument("--inline", required=True)
    args = parser.parse_args()

    runtime = Path(args.runtime).resolve()
    if not (runtime / "Main.py").is_file():
        raise FileNotFoundError(runtime / "Main.py")
    os.chdir(runtime)
    sys.path.insert(0, str(runtime))
    # Generation from the Flow tab is an artifact-only operation.  Network
    # publishing and LLM calls stay outside the development worker run.
    # Empty-but-present values prevent python-dotenv in the operator project
    # from re-enabling network clients while Main is imported.
    os.environ["GPT_API_BASE_URL"] = ""
    os.environ["GPT_CREDENTIAL_KEY"] = ""

    # Main imports the OpenAI SDK eagerly even though artifact-only runs do
    # not use it. Keep that optional dependency out of the worker contract.
    openai_stub = types.ModuleType("openai")

    class DisabledOpenAI:
        def __init__(self, *args, **kwargs):
            raise RuntimeError("OpenAI is disabled for Flow Auto report generation")

    openai_stub.OpenAI = DisabledOpenAI
    sys.modules["openai"] = openai_stub

    import Main as legacy_main

    config = legacy_main.GLOBAL_CONFIG
    config.base_path = str(runtime)
    config.use_email_send = False
    config.use_s3_upload = False
    config.use_gpt_summary = False
    config.use_gpt_multistep = False
    original_get = config.get

    def safe_get(key, default=None):
        if key in {"use_email_send", "use_s3_upload", "use_gpt_summary", "use_gpt_multistep"}:
            return False
        return original_get(key, default)

    config.get = safe_get
    legacy_main.inlinedata_query = _inline_loader(Path(args.inline), config)
    sys.argv = [str(runtime / "Main.py"), args.trigger]
    legacy_main.main()
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
