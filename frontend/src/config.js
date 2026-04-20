export const FEATURE_VERSIONS = {
  filebrowser: "8.0.4", splittable: "8.0.4", dashboard: "8.0.4",
  tracker: "7.0", tablemap: "4.1", ettime: "4.1",
  admin: "8.1.6", devguide: "4.0", ml: "7.0",
  messages: "8.1.6",
  home: "8.1.6",
};
export const TABS = [
  {key:"home",label:"홈",icon:"🏠",group:"main"},
  {key:"filebrowser",label:"파일탐색기",icon:"📂",group:"data",defaultTab:true},
  {key:"dashboard",label:"대시보드",icon:"📊",group:"data",defaultTab:true},
  {key:"splittable",label:"스플릿 테이블",icon:"🗂️",group:"data",defaultTab:true},
  {key:"tracker",label:"이슈 추적",icon:"📋",group:"tool"},
  {key:"tablemap",label:"테이블맵",icon:"🔗",group:"tool"},
  {key:"ml",label:"ML 분석",icon:"🧠",group:"analysis"},
  {key:"admin",label:"관리자",icon:"⚙️",group:"system",adminOnly:true},
  {key:"devguide",label:"개발자 가이드",icon:"📖",group:"system"},
];
// TAB_CONFIG_END
