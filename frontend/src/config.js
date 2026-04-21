export const FEATURE_VERSIONS = {
  filebrowser: "8.0.4", splittable: "8.0.4", dashboard: "8.0.4",
  tracker: "7.0", tablemap: "4.1",
  admin: "8.5.0", devguide: "4.0",
  messages: "8.1.6",
  home: "8.1.6",
  inform: "8.5.1",
};
// v8.4.7: ML 분석 탭 제거 — 아직 활성 기능 아님. 홈의 PLANNED_FEATURES 에 노출.
// v8.5.1: Inform log 추가.
export const TABS = [
  {key:"home",label:"홈",icon:"🏠",group:"main"},
  {key:"filebrowser",label:"파일탐색기",icon:"📂",group:"data",defaultTab:true},
  {key:"dashboard",label:"대시보드",icon:"📊",group:"data",defaultTab:true},
  {key:"splittable",label:"스플릿 테이블",icon:"🗂️",group:"data",defaultTab:true},
  {key:"tracker",label:"이슈 추적",icon:"📋",group:"tool"},
  {key:"inform",label:"인폼 로그",icon:"📢",group:"tool"},
  {key:"tablemap",label:"테이블맵",icon:"🔗",group:"tool"},
  {key:"admin",label:"관리자",icon:"⚙️",group:"system",adminOnly:true},
  {key:"devguide",label:"개발자 가이드",icon:"📖",group:"system"},
];
// v8.4.7: 탭에 올리지 않고 "앞으로 할 것" 으로만 표시.
export const PLANNED_FEATURES = [
  {key:"et_time",label:"ET Time",icon:"⏱",desc:"ET/EDS 시간대 히트맵·트렌드 분석"},
  {key:"ml",label:"ML 분석",icon:"🧠",desc:"TabICL / XGBoost / LightGBM 기반 원인 분석 + SHAP"},
];
// TAB_CONFIG_END
