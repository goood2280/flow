"""scripts/seed_knob_meta.py — DB root/knob_ppid.csv + step_matching.csv 샘플 생성.

v8.4.7 추가. /api/splittable/knob-meta 는 다음 두 Base 파일을 읽어 KNOB feature_name 에
func_step(step_id) 역산 라벨을 만든다. data/ 디렉터리는 .gitignore 로 버전 관리되지
않으므로, 새 환경에서 SplitTable KNOB 부제를 쓰려면 이 스크립트로 한 번 시드해두면 됨.

스키마:
  step_matching.csv  : step_id, func_step                 (1:N — 하나의 func_step 에
                                                           여러 step_id 가 매칭)
  knob_ppid.csv      : feature_name, function_step, rule_order, ppid, operator,
                       category, use
                       - rule_order 1..N = 같은 feature_name 의 여러 func_step 결합 순서
                       - operator (+ 등) = 다음 rule 과의 결합 연산자. 마지막 rule 은 빈값
                       - use = Y/N — N 이면 knob-meta 에서 무시

사용:
  python3 scripts/seed_knob_meta.py               # default: 파일탐색기 DB 루트
  python3 scripts/seed_knob_meta.py --force       # 기존 파일 덮어쓰기
"""
import argparse
import sys
from pathlib import Path

HERE = Path(__file__).resolve().parent
sys.path.insert(0, str(HERE.parent / "backend"))

from core.paths import PATHS  # noqa: E402

STEP_MATCHING = """step_id,func_step
AA100010,STI_FORM
AA100020,STI_FORM
AA100030,STI_FORM
AA100100,WELL_IMPLANT
AA100110,WELL_IMPLANT
AA100120,VTH_IMPLANT
AA200010,GATE_OX
AA200020,GATE_POLY_DEP
AA200030,GATE_PATTERN
AA200040,GATE_PATTERN
AA200050,GATE_PATTERN
AA200100,PC_ETCH
AA200110,PC_ETCH
AA300010,SPACER_DEP
AA300020,SPACER_DEP
AA300030,SPACER_ETCH
AA400010,SD_EPI
AA400020,SD_EPI
AA500010,ANNEAL_RTA
AA500020,ANNEAL_LASER
AA600010,ETCH_MAIN
AA600020,ETCH_MAIN
AA600030,ETCH_MAIN
AA700010,CVD_DIEL
AA700020,CVD_DIEL
AA800010,LITHO_EXPOSE
AA800020,LITHO_EXPOSE
AA900010,MOL_CNT_ETCH
AA900020,MOL_CNT_FILL
AB100010,BEOL_M1_LITHO
AB100020,BEOL_M1_ETCH
"""

KNOB_PPID = """feature_name,function_step,rule_order,ppid,operator,category,use
KNOB_GATE_PPID,GATE_PATTERN,1,PP_GATE_01,+,gate,Y
KNOB_GATE_PPID,PC_ETCH,2,PP_PC_01,,gate,Y
KNOB_ETCH_PPID,ETCH_MAIN,1,PP_ETCH_01,,etch,Y
KNOB_CVD_PPID,CVD_DIEL,1,PP_CVD_01,,cvd,Y
KNOB_LITHO_PPID,LITHO_EXPOSE,1,PP_LITHO_01,,litho,Y
KNOB_SPACER_PPID,SPACER_DEP,1,PP_SPACER_01,+,spacer,Y
KNOB_SPACER_PPID,SPACER_ETCH,2,PP_SPACER_ET_01,,spacer,Y
KNOB_ANNEAL_RECIPE,ANNEAL_RTA,1,PP_ANN_RTA_01,+,anneal,Y
KNOB_ANNEAL_RECIPE,ANNEAL_LASER,2,PP_ANN_LSR_01,,anneal,Y
KNOB_SD_EPI_RECIPE,SD_EPI,1,PP_SDEPI_01,,sd_epi,Y
"""


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--force", action="store_true", help="기존 파일 덮어쓰기")
    ap.add_argument("--base-root", default=None, help="대체 경로 (기본: 파일탐색기 DB 루트)")
    args = ap.parse_args()

    root = Path(args.base_root) if args.base_root else PATHS.base_root
    root.mkdir(parents=True, exist_ok=True)
    for name, body in (("step_matching.csv", STEP_MATCHING), ("knob_ppid.csv", KNOB_PPID)):
        fp = root / name
        if fp.exists() and not args.force:
            print(f"skip (exists): {fp}  — rerun with --force to overwrite")
            continue
        fp.write_text(body, encoding="utf-8")
        print(f"wrote: {fp}")


if __name__ == "__main__":
    main()
