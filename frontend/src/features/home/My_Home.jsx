import { useState, useEffect, useRef } from "react";
import BrandLogo from "../../components/BrandLogo";
import { authSrc, dl, postJson, sf } from "../../lib/api";
import { isAdmin as isAdminUser, visibleTabsFor } from "../../lib/permissions";
import { toast } from "../../components/Toast";
import { PageHeader, statusPalette } from "../../components/UXKit";
import BoxStatsTable from "../../components/BoxStatsTable";
import { FlowPlotlyChart, WipStackedBar } from "../../components/PlotlyChart";
import { boxStatsAlignment, boxStatsFromSummary } from "../../lib/boxStats";
import TegValueWaferMap from "../../components/TegValueWaferMap";
import SplitTableSnapshotView from "../../components/SplitTableSnapshotView";
import { ShotZoom } from "../teg/My_TegMap";
const B="#ea580c",M="#f97316",L="#fb923c",D="#9a3412",BK="#171717",W="#fff7ed",PK="#fda4af",G="#fbbf24";
const HOME_UI={
  accent:statusPalette.warn.fg,
  accentBg:statusPalette.warn.bg,
  // 테두리용 토큰. accent 는 "var(--warn)" 이라 `accent+"66"` 같은 알파 접미사를
  // 붙이면 치환 후 `#xxxxxx 66` 두 토큰이 되어 선언이 통째로 무효가 된다.
  accentLine:statusPalette.warn.line,
  ok:statusPalette.ok.fg,
  okBg:statusPalette.ok.bg,
  bad:statusPalette.bad.fg,
  badBg:statusPalette.bad.bg,
  text:"var(--text-primary,#e5e5e5)",
  textSub:"var(--text-secondary,#a3a3a3)",
  textDim:"#737373",
  textSoft:"#d4d4d4",
  border:"var(--border,#333)",
  borderStrong:"#333",
  borderSoft:"#2a2a2a",
  card:"var(--bg-card,#2a2a2a)",
  panel:"#111",
  panelSoft:"#151515",
  terminal:"#171717",
};
// Long-running Flow-i analysis may need cache scans or a development worker.
// Keep the browser alive for ten minutes; the server still reports progress
// through /flowi/progress while the request is running.
const FLOWI_CLIENT_TIMEOUT_MS=600000;
const FLOWI_CLIENT_TIMEOUT_S=Math.round(FLOWI_CLIENT_TIMEOUT_MS/1000);

// v8.3.3: PF_HOME / PixelGlyph / HomeBrandLogo extracted to shared ../components/BrandLogo.jsx.
// Home uses <BrandLogo size="home"/>; nav uses <BrandLogo size="nav"/> (see App.jsx).


const BASE_PX=[[2,5,B],[2,6,B],[2,7,B],[2,8,B],[2,9,B],[2,10,B],[3,4,B],[3,5,M],[3,6,M],[3,7,M],[3,8,M],[3,9,M],[3,10,M],[3,11,B],[4,3,B],[4,4,M],[4,5,L],[4,6,L],[4,7,L],[4,8,L],[4,9,L],[4,10,L],[4,11,M],[4,12,B],[5,3,B],[5,4,M],[5,5,L],[5,6,L],[5,7,L],[5,8,L],[5,9,L],[5,10,L],[5,11,M],[5,12,B],[8,3,B],[8,4,PK],[8,5,L],[8,6,L],[8,7,L],[8,8,L],[8,9,L],[8,10,L],[8,11,PK],[8,12,B],[9,3,B],[9,4,M],[9,5,L],[9,6,L],[9,7,BK],[9,8,BK],[9,9,L],[9,10,L],[9,11,M],[9,12,B],[10,3,B],[10,4,M],[10,5,M],[10,6,M],[10,7,M],[10,8,M],[10,9,M],[10,10,M],[10,11,M],[10,12,B],[11,4,B],[11,5,B],[11,6,B],[11,7,B],[11,8,B],[11,9,B],[11,10,B],[11,11,B],[12,5,B],[12,6,B],[12,9,B],[12,10,B],[13,5,D],[13,6,D],[13,9,D],[13,10,D],[0,7,G],[1,7,G],[0,8,G],[1,8,G]];
const EO=[[6,3,B],[6,4,M],[6,5,W],[6,6,BK],[6,7,L],[6,8,L],[6,9,W],[6,10,BK],[6,11,M],[6,12,B],[7,3,B],[7,4,M],[7,5,W],[7,6,BK],[7,7,L],[7,8,L],[7,9,W],[7,10,BK],[7,11,M],[7,12,B]];
const EC=[[6,3,B],[6,4,M],[6,5,L],[6,6,L],[6,7,L],[6,8,L],[6,9,L],[6,10,L],[6,11,M],[6,12,B],[7,3,B],[7,4,M],[7,5,BK],[7,6,BK],[7,7,L],[7,8,L],[7,9,BK],[7,10,BK],[7,11,M],[7,12,B]];
const AD=[[7,1,M],[7,2,M],[8,1,B],[7,13,M],[7,14,M],[8,14,B]];
const AW=[[7,1,M],[7,2,M],[8,1,B],[5,13,M],[5,14,G],[6,13,M],[6,14,B]];
function Holli({size=72}){const[fr,setFr]=useState("idle");const t=useRef(null);useEffect(()=>{const loop=()=>{t.current=setTimeout(()=>{if(Math.random()<0.6){setFr("blink");setTimeout(()=>{setFr("idle");loop();},150);}else{setFr("wave");setTimeout(()=>{setFr("idle");loop();},600);}},1500+Math.random()*2500);};loop();return()=>clearTimeout(t.current);},[]);const px=[...BASE_PX,...(fr==="blink"?EC:EO),...(fr==="wave"?AW:AD)];return(<div style={{animation:fr==="idle"?"holBob 2s ease-in-out infinite":"none"}}><style>{`@keyframes holBob{0%,100%{transform:translateY(0)}50%{transform:translateY(-3px)}}@keyframes holBlink{0%,100%{opacity:1}50%{opacity:0}}`}</style><svg width={size} height={size} viewBox="0 0 16 16" style={{imageRendering:"pixelated"}}>{px.map(([r,c,color],i)=><rect key={i} x={c} y={r} width={1} height={1} fill={color}/>)}</svg></div>);}
function Cli({cmd,output,delay=0}){const line=`> flow ${cmd}`;const parts=[{text:">",color:HOME_UI.accent},{text:" flow ",color:HOME_UI.textDim},{text:cmd,color:HOME_UI.text}];const[show,setShow]=useState(delay===0);const[typedLen,setTypedLen]=useState(0);const[done,setDone]=useState(false);useEffect(()=>{if(delay){const t=setTimeout(()=>setShow(true),delay);return()=>clearTimeout(t);}},[delay]);useEffect(()=>{if(!show)return;setTypedLen(0);setDone(false);let i=0;const iv=setInterval(()=>{i++;setTypedLen(i);if(i>=line.length){clearInterval(iv);setTimeout(()=>setDone(true),100);}},30);return()=>clearInterval(iv);},[show,line]);if(!show)return null;let remain=typedLen;return(<div style={{marginBottom:4,fontFamily:"'JetBrains Mono',monospace",fontSize:14,lineHeight:1.7}}>{parts.map((p,idx)=>{const s=p.text.slice(0,Math.max(0,Math.min(p.text.length,remain)));remain-=s.length;return s?<span key={idx} style={{color:p.color}}>{s}</span>:null;})}{!done&&<span style={{display:"inline-block",width:8,height:14,background:HOME_UI.accent,marginLeft:2,animation:"holBlink 0.6s step-end infinite"}}/>}{done&&output&&<div style={{color:HOME_UI.textSub,paddingLeft:20,fontSize:14}}>{output}</div>}</div>);}
function WelcomeType({name}){const full=`${name}님, 안녕하세요`;const[len,setLen]=useState(0);useEffect(()=>{const t=setTimeout(()=>{let i=0;const iv=setInterval(()=>{i++;setLen(i);if(i>=full.length)clearInterval(iv);},70);return()=>clearInterval(iv);},800);return()=>clearTimeout(t);},[full]);return(<span><span style={{color:"#fff",fontWeight:700}}>{full.slice(0,len)}</span></span>);}
// Carbon 정렬 (2026-08-02)
//  - 간격은 carbon.css 의 8px 그리드 토큰으로. carbon.css 는 gap 만 다시 쓰고
//    padding/margin 은 건드리지 않아서 20/10/6 이 그대로 남아 있었다.
//  - 클릭 타일을 키보드로 도달 가능하게. role+tabIndex 가 있어야 carbon.css §4 의
//    2px 실선 포커스(`[role="button"]:focus-visible`)가 발동한다.
//  - 모션은 Carbon 70ms/cubic-bezier(0.2,0,0.38,0.9). carbon.css 는 이걸
//    button/.btn 에만 걸어서 카드는 all 0.2s 로 남아 있었다.
//  - 호버 배경은 --warn-50 (accentBg). 이전 `accent+"10"` 은 accent 가 리터럴
//    hex 이던 시절의 알파 접미사인데 지금은 "var(--warn)" 이라 `var(--warn)10` 이
//    되고, 치환 후 invalid → background-color 가 initial(transparent)로 떨어져
//    호버할 때 카드 면이 사라졌다.
function Card({icon,title,desc,tag,onClick,width=220}){
  return(<button type="button" onClick={onClick} className="home-feature-card" style={{width}}>
    <span className="home-feature-card__topline">
      <span className="home-feature-card__icon" aria-hidden="true">{icon}</span>
      {tag&&<span className="home-feature-card__tag">{tag}</span>}
    </span>
    <span className="home-feature-card__content">
      <span className="home-feature-card__title">{title}</span>
      <span className="home-feature-card__description">{desc}</span>
    </span>
    <span className="home-feature-card__arrow" aria-hidden="true">&#8594;</span>
  </button>);
}

// 홈 카드 한 줄 설명. key 는 config.js TABS 의 key — 아이콘과 이름은 TABS 가 원천이고
// 여기에는 설명만 둔다. 새 탭을 추가하면 카드는 자동으로 뜨고, 이 표에 한 줄을 넣으면
// 설명까지 채워진다.
// v8.8.5: 카드별 tag(개별 버전) 제거 — 통합 버전만 의미 있음.
// v10.1.x: desc 를 현재 화면 기준으로 현행화. 특히 대시보드(차트보드 → WIP×Split
//   단일 화면, v9.2)와 에이전트(LLM 설정 → 카탈로그/실행추적), ET 추적(이슈 게시판 →
//   ET DB 측정이력 스캔)은 예전 설명이 그대로 남아 있었다.
const CARD_DESC={
  filebrowser:"DB·Files 탐색, SQL·AI SQL, CSV",
  dashboard:"WIP × Split 현황, step 구간 스택",
  splittable:"Plan vs actual, XLSX·스냅샷",
  lotmanage:"주요 랏 표 관리, SplitTable 바로보기",
  ramcache:"캐시 현황·예산·스캔·검색 속도",
  matchfill:"매칭 CSV product·module 채우기",
  diagnosis:"기능 카탈로그, 실행 추적, 워크플로",
  chartbuilder:"쿼리·JOIN 으로 차트 정의와 저장",
  templatereport:"저장한 차트로 PPT 템플릿 생성",
  autoreport:"ET·INLINE·FAB 기반 PPT 자동 생성",
  lotrequest:"랏 배정·Hot grade 요청과 처리",
  valve:"Valve 알람 판정과 룰북 반영",
  teg:"TEG 좌표, Mapfile 체크·생성",
  ettime:"PGM(pt)별 측정시간, 월별 추이",
  inform:"모듈 인폼 등록, 스냅샷·메일",
  reformatize:"REAL·ADDP index 계산, CSV",
  dcop:"붙여넣은 DCOP 표 규칙 검사",
  tracker:"이슈별 ET 측정이력 자동 스캔",
  meeting:"차수·아젠다·회의록·액션",
  calendar:"월 달력, 상태·회의 연동",
  admin:"사용자·권한·모니터·설정",
  devguide:"아키텍처·API·운영 레퍼런스",
};

// Feature guide content shown to users (non-admin) instead of release history.
// v10.1.x: 화면 현행화 — 대시보드는 차트보드에서 WIP×Split 단일 화면으로 바뀌었고(v9.2),
//   에이전트 탭은 LLM 설정이 아니라 카탈로그/추적이며(LLM 설정은 관리자 탭), ET 추적은
//   lot watch 폴링이 아니라 ET DB 일일 스캔이다. 각 화면의 실제 소탭 이름으로 맞춘다.
const FEATURE_GUIDES={
  filebrowser:{icon:"📂",title:"파일탐색기",steps:["좌측에서 DB 제품 또는 Files 폴더 선택","스키마가 먼저 뜨고 최신 파티션 샘플이 이어서 채워짐","SQL 필터 입력 (예: PRODUCT_TYPE = 'A', LOT_ID LIKE '%ABC%') — 자연어는 AI SQL 버튼","컬럼 선택 → CSV 다운로드 (화면은 최대 100행, 다운로드 한도는 별도)"]},
  dashboard:{icon:"📊",title:"대시보드",steps:["제품(또는 전체)과 Lot Type 선택 — 전체는 제품별 색으로 표시","X축 기준(step_id 구간 / step_desc 앞 숫자)과 bin 간격 지정","Split 기준 열을 고르면 구간별 스택으로 분해","막대 클릭 → 드릴다운 목록 확인, TSV 복사"]},
  splittable:{icon:"🗂️",title:"스플릿 테이블",steps:["Product 선택 → Root Lot + Wafer IDs 입력 → 검색","Plan 입력: 편집 클릭 후 셀 입력 (Excel 붙여넣기 지원)","셀 표시: 값별 고유색 / 주황 테두리+기울임(plan만) / 빨강 테두리(plan≠actual), 미진행 공정 행은 회색","CSV·XLSX 다운로드, Inform 스냅샷 생성","이력 탭에서 변경 이력 확인"]},
  ramcache:{icon:"🧠",title:"캐시 관리",steps:["제품별 현황 — 캐시 적재량·예열 결과 확인, 주요 Lot 등록","캐싱 진행 및 로그 — 수동 스캔 실행, 진행 단계·대기 큐·이벤트 로그","검색 속도 & 설정 — 히트율·p50/p90, 미스 반복 root 확인","⚙️ 예산 설정에서 캐시 예산 조정 (운영/개발 서버 각각)"]},
  valve:{icon:"🚨",title:"매칭알람",steps:["RO ppid / 미매칭 step 알람 목록 확인","미매칭 step 은 추천 function step 의 근거(ppid·설비·area) 확인 후 적용","판정 입력 → ppid_knob.csv / Vehicle_matching.csv 에 반영","반영이 필요 없으면 '불필요'로 재알람 억제, 판정 이력에서 추적"]},
  teg:{icon:"📐",title:"TEG 위치 조회",steps:["위치 조회 — vehicle 선택 후 TEG(module) 또는 top_cell 검색","WF MAP에서 위치·shot 격자좌표(chip_x/y_adj)·radius 확인","TEG Mapfile 체크 — 설비 레시피 원문을 Teg_location 과 대조 (신호등)","Mapfile용 좌표 생성 — 기준 PCHK(0,0) 상대좌표와 MAIN die 격자 생성"]},
  ettime:{icon:"⏱️",title:"ET 측정시간",steps:["제품과 Root Lot ID 입력 후 조회","step · PGM(pt)별 측정시간(tkout − tkin) 확인","측정 패키지 상세로 wafer 단위 내역 확인","제품만으로 '추이 조회' → 월별 평균 측정시간 추세"]},
  reformatize:{icon:"🧮",title:"ET 다운로드",steps:["제품(DB ET) 선택 → 매칭된 vehicle CSV 확인","REAL / ADDP index 항목 선택","최근 N일·root lot·step 으로 범위를 좁힐수록 빨라짐","조회 후 전체 결과 CSV 다운로드","(관리자) 톱니바퀴에서 value 열·scale 설정, ADDP 수식 테스트"]},
  diagnosis:{icon:"🤖",title:"에이전트",steps:["기능 카탈로그 — Flow-i 가 쓸 수 있는 unit AI 와 실행 조건 확인","실행 추적 — 질문별 LangGraph 노드·State·질문 이력 확인","Workflow 템플릿 — 다단계 질문 흐름 등록·수정","LLM 연결과 모델 설정은 관리자 탭에 있습니다"]},
  tracker:{icon:"📋",title:"ET 추적",steps:["새 이슈 — 제목·설명(이미지 붙여넣기)과 product / root_lot_id / wafer 입력","'조회'로 등록 전에 ET 측정이력 미리 확인","지정 시각 자동 스캔이 새 PGM(pt)을 측정이력에 누적하고 메일 발송","메일 수신 그룹은 이슈별로 지정 (스캔 시각·PGM 필터는 톱니바퀴)","간트 탭에서 전체 진행 현황 확인"]},
  inform:{icon:"📢",title:"인폼 로그",steps:["인폼 탭에서 lot 선택 → 모듈·사유·기한 입력 (등록 마법사)","SplitTable 스냅샷 자동 첨부 확인","메일 미리보기에서 수신자·본문 확인 후 발송","댓글 스레드로 후속 대화, 매트릭스·로그 탭에서 전체 현황"]},
  meeting:{icon:"🗓",title:"회의관리",steps:["회의 선택 또는 신규 생성 → 차수 탭에서 회차 관리","아젠다·회의록·결정사항 입력","액션아이템 등록 → 트래커·달력과 상태 동기화","메일 미리보기에서 보낼 섹션만 골라 회의록 공유"]},
  calendar:{icon:"📅",title:"변경점 관리",steps:["월 달력에서 변경 일정 확인","카테고리별 이벤트 필터","회의 액션·결정사항 연동 확인","상태(pending/in_progress/done) 관리"]},
  devguide:{icon:"📖",title:"개발자 가이드",steps:["아키텍처 · 파일 구조 · 탭 구성","API 레퍼런스 · 표준 스키마 · 데이터 루트/DB","Flow-i 규칙 · 대용량 운영 · UX/테마 시스템","기능 추가와 업데이트/배포 절차"]},
  lotmanage:{icon:"🏷️",title:"랏 관리",steps:["제품 선택 → purpose 검색으로 필요한 랏만 확인","편집을 눌러 셀 입력, 행·열 추가 (셀 오른쪽 원으로 색상 지정)","lot_id 행의 '보기'로 해당 랏 SplitTable 바로 확인","CUSTOM SET 으로 SplitTable 에 보일 컬럼 묶음 지정","버전 기록에서 변경항목 비교·롤백"]},
  matchfill:{icon:"🧩",title:"매칭 채우기",steps:["대상 CSV(ppid_knob / Vehicle_matching) 선택","채울 열 선택 — product(DB 스캔) 또는 module(step 구간)","검사 실행 → 채워질 값 제안 목록 확인","반영은 관리자만 — 제안 확인 후 CSV 에 반영"]},
  chartbuilder:{icon:"📈",title:"차트생성",steps:["쿼리 소스를 정의하고 필요하면 JOIN(기본 left)으로 결합","X·Y 열과 Trend 단위(Shot raw / Wafer / 일별 / 주별) 선택","'SQL 실행 · JOIN 및 저장' → 차트 이름으로 저장","저장 차트는 Template Report 와 홈 Flow-i 에서 재사용"]},
  templatereport:{icon:"🖼️",title:"Template Report",steps:["템플릿 생성 → 페이지에 블록(차트·Split표·글·통계표) 배치","번호를 드래그해 슬라이드 안 위치 지정","변수에 랏을 여러 개 넣으면 페이지 묶음이 랏마다 반복","미리보기 확인 후 PPTX 또는 차트 PNG ZIP 다운로드"]},
  autoreport:{icon:"📑",title:"Auto report",steps:["제품 key 입력 (제품_LOT_STEP 형식)","'생성 요청' → 공유 큐에 전달, 실제 생성은 개발 서버 worker","이력 표에서 진행 단계·오류 확인","완료된 건은 PPT 다운로드"]},
  lotrequest:{icon:"📨",title:"랏 배정/요청",steps:["신규 요청 등록 — 유형(랏 배정 / Hot grade / 기타)·중요도·요청팀 입력","목록에서 제품·상태로 필터링, 제목을 눌러 상세 확인","담당자(페이지 위임)와 관리자가 답글과 배정/처리 랏 등록","PI 처리 상태를 등록 → 처리완료 / 반려 로 마감","요청·처리 이력은 메일로 공유"]},
  dcop:{icon:"✅",title:"양산DCOP 검사",steps:["작성한 DCOP 를 Excel 에서 복사해 A1 셀에 붙여넣기 (첫 행=열 이름)","'검사 실행' → 규칙 위반 행 확인, 상태로 필터","Excel 다운로드로 검사 결과 공유","(관리자) 톱니바퀴에서 검사 규칙 추가·수정"]},
};
function shortFlowiVerifyError(value){
  const text=String(value||"").replace(/Bearer\s+[A-Za-z0-9._~+/=-]{12,}/gi,"Bearer <redacted>").replace(/ya29\.[A-Za-z0-9._~+/=-]+/g,"ya29.<redacted>").replace(/sk-[A-Za-z0-9._~+/=-]{12,}/g,"sk-<redacted>").replace(/\s+/g," ").trim();
  return text.length>120?`${text.slice(0,117)}...`:text;
}
function flowiOutputSummaryForContext(result){
  const tool=result?.tool||{};
  const table=tool?.table&&typeof tool.table==="object"?tool.table:{};
  const split=tool?.split_view&&typeof tool.split_view==="object"?tool.split_view:{};
  const chart=tool?.chart_result&&typeof tool.chart_result==="object"?tool.chart_result:(tool?.chart&&typeof tool.chart==="object"?tool.chart:{});
  const blocks=Array.isArray(tool?.blocks)?tool.blocks:[];
  return {
    table:table?.kind?{kind:table.kind||"",title:table.title||"",total:table.total??(Array.isArray(table.rows)?table.rows.length:0)}:{},
    split_view:split?.kind?{kind:split.kind||"",title:split.title||"",total:split.total??(Array.isArray(split.rows)?split.rows.length:0),row_label:split.row_label||""}:{},
    chart:chart?.kind||chart?.status?{kind:chart.kind||chart.status||"",title:chart.title||"",status:chart.status||""}:{},
    blocks:blocks.slice(0,6).map(b=>({kind:b?.kind||"",title:b?.title||""})).filter(b=>b.kind||b.title),
  };
}
function flowiContextCell(value){
  if(value===null||value===undefined)return "";
  if(typeof value==="number"||typeof value==="boolean")return value;
  if(typeof value==="string")return value.slice(0,300);
  try{return JSON.stringify(value).slice(0,300);}catch{return String(value).slice(0,300);}
}
function flowiResultTableForContext(result){
  const tool=result?.tool||{};
  const table=tool?.table&&Array.isArray(tool.table.rows)?tool.table:null;
  if(table){
    const rawCols=Array.isArray(table.columns)?table.columns:[];
    const keys=(rawCols.length?rawCols:Object.keys(table.rows[0]||{})).map(column=>typeof column==="string"?column:String(column?.key||column?.field||column?.name||"")).filter(Boolean).slice(0,20);
    return {
      kind:table.kind||"table",title:table.title||"직전 조회",total:table.total??table.rows.length,
      columns:keys.map(key=>({key,label:String(rawCols.find(c=>typeof c==="object"&&(c.key||c.field||c.name)===key)?.label||key)})),
      rows:table.rows.slice(0,40).map(row=>Object.fromEntries(keys.map(key=>[key,flowiContextCell(row?.[key])]))),
    };
  }
  const split=tool?.split_view;
  if(split&&Array.isArray(split.rows)){
    const headers=(Array.isArray(split.headers)?split.headers:[]).slice(0,19).map(String);
    const columns=[{key:"parameter",label:split.row_label||"항목"},...headers.map((header,index)=>({key:`wf_${index+1}`,label:header}))];
    const rows=split.rows.slice(0,40).map(row=>{
      const out={parameter:flowiContextCell(row?.display||row?.parameter||"")};
      headers.forEach((_header,index)=>{
        const cell=row?.cells?.[index]||{};
        out[`wf_${index+1}`]=flowiContextCell(cell.actual??cell.plan??"");
      });
      return out;
    });
    return {kind:split.kind||"split_view",title:split.title||"직전 SplitTable",total:split.total??rows.length,columns,rows};
  }
  return {};
}
function FlowiConsole({onNavigate,user,onActiveChange}){
  const isAdmin=user?.role==="admin";
  const[active,setActive]=useState(false);
  const[connState,setConnState]=useState("idle");
  const[prompt,setPrompt]=useState("");
  const[busy,setBusy]=useState(false);
  const[result,setResult]=useState(null);
  const[lastPrompt,setLastPrompt]=useState("");
  const[err,setErr]=useState("");
  const[modelLabel,setModelLabel]=useState("");
  const[verifyError,setVerifyError]=useState("");
  const[messages,setMessages]=useState([]);
  const[liveSteps,setLiveSteps]=useState([]);
  const[liveElapsed,setLiveElapsed]=useState(0);
  const[activeChartSessionId,setActiveChartSessionId]=useState("");
  const promptRef=useRef(null);
  const scrollRef=useRef(null);
  const verifySeq=useRef(0);
  const runIdRef=useRef("");
  const CTX_LIMIT=12000;

  useEffect(()=>{if(active&&promptRef.current)setTimeout(()=>promptRef.current?.focus(),30);},[active]);
  useEffect(()=>{if(active&&scrollRef.current)scrollRef.current.scrollTop=scrollRef.current.scrollHeight;},[active,messages,busy]);
  useEffect(()=>{
    if(!busy){setLiveElapsed(0);return undefined;}
    const started=Date.now();
    const tick=()=>setLiveElapsed(Math.max(0,Math.floor((Date.now()-started)/1000)));
    tick();
    const iv=setInterval(tick,1000);
    return()=>clearInterval(iv);
  },[busy]);
  // 실행 단계 폴링 — 어떤 단위기능/오케스트레이터/모델 호출이 떴는지 실시간 표시.
  // 서버는 공개 이벤트(이름·상태·소요시간)만 내려주며 내부 추론은 담지 않는다.
  // run_id 가 없거나 응답이 비면 아래 FlowiLiveTrace 가 기존 문구로 폴백한다.
  useEffect(()=>{
    const runId=busy?runIdRef.current:"";
    if(!runId)return undefined;
    let alive=true;
    let cursor=0;
    const poll=()=>{
      sf(`/api/llm/flowi/progress/${encodeURIComponent(runId)}?after=${cursor}`)
        .then(d=>{
          if(!alive||runIdRef.current!==runId)return;
          const events=Array.isArray(d?.events)?d.events:[];
          if(!events.length)return;
          cursor=Number(d?.last_seq)||cursor;
          setLiveSteps(prev=>mergeFlowiLiveSteps(prev,events));
        }).catch(()=>{});
    };
    poll();
    const iv=setInterval(poll,700);
    return()=>{alive=false;clearInterval(iv);};
  },[busy]);
  useEffect(()=>{
    let alive=true;
    sf("/api/llm/status").then(d=>{
      if(!alive)return;
      const cfg=d?.config||{};
      const model=String(cfg.model||"").trim();
      setModelLabel(d?.available&&model?model:"");
      if(d&&!d.available)setConnState("unavailable");
    }).catch(()=>{if(alive)setModelLabel("");});
    return()=>{alive=false;};
  },[]);
  const activate=()=>{
    setActive(true);setErr("");setVerifyError("");
    onActiveChange&&onActiveChange(true);
    const seq=++verifySeq.current;
    setConnState("checking");
    postJson("/api/llm/flowi/verify",{token:""})
      .then(d=>{
        if(seq!==verifySeq.current)return;
        const msg=String(d?.message||d?.text||"");
        if(d?.status==="connected"||(d?.ok&&msg.includes("확인완료"))){
          setConnState("connected");
          setVerifyError("");
        }else if(d?.status==="delayed"){
          setConnState("delayed");
          setVerifyError(shortFlowiVerifyError(d?.error||d?.message||"LLM 연결 확인 지연"));
        }else if(d?.status==="unavailable"||d?.unavailable||d?.error==="llm unavailable"){
          setConnState("unavailable");
          setVerifyError("");
        }else{
          setConnState("verify_failed");
          setVerifyError(shortFlowiVerifyError(d?.error||d?.message||"unknown"));
        }
      })
      .catch(e=>{
        if(seq===verifySeq.current){
          setConnState("verify_failed");
          setVerifyError(shortFlowiVerifyError(e?.message||"verify request failed"));
        }
      });
    return true;
  };
  const close=()=>{setActive(false);setErr("");onActiveChange&&onActiveChange(false);};
  const contextMessages=messages.slice(-8).map(m=>{
    const outputSummary=flowiOutputSummaryForContext(m.result);
    const resultTable=flowiResultTableForContext(m.result);
    const resultSources=flowiSourceValues(m.result?.tool||{},m.result?.tool?.table||{});
    return {
      role:m.role,
      prompt:m.prompt||"",
      text:String(m.answer||m.text||"").slice(0,900),
      answer_excerpt:String(m.answer||m.result?.answer||m.text||"").slice(0,600),
      intent:m.intent||m.result?.tool?.intent||"",
      feature:m.result?.tool?.feature||"",
      action:m.result?.tool?.action||"",
      blocked:!!m.result?.tool?.blocked,
      created_record:m.result?.tool?.created_record||null,
      missing:m.result?.tool?.missing||[],
      arguments_choices:m.result?.tool?.arguments_choices||{},
      missing_freetext:m.result?.tool?.missing_freetext||m.result?.missing_freetext||[],
      arguments_partial:m.result?.tool?.arguments_partial||m.result?.tool?.arguments||{},
      last_partial_prompt:m.result?.tool?.last_partial_prompt||m.result?.last_partial_prompt||"",
      walkthrough:m.result?.tool?.walkthrough||{},
      slots:m.result?.tool?.slots||{},
      filters:m.result?.tool?.filters||{},
      table_kind:outputSummary.table?.kind||"",
      split_view_kind:outputSummary.split_view?.kind||"",
      split_view_summary:outputSummary.split_view||{},
      chart_session_id:m.result?.tool?.chart_session_id||m.result?.tool?.chart_result?.chart_session_id||"",
      workflow_state:m.result?.workflow_state||m.result?.tool?.workflow_state||{},
      output_summary:outputSummary,
      result_table:resultTable,
      result_sources:resultSources,
      pending_prompt:m.result?.tool?.pending_prompt||"",
    };
  });
  const contextText=contextMessages.map(m=>`${m.role}: ${m.prompt||m.text||""} ${m.intent?`(${m.intent})`:""}`).join("\n");
  const contextRemaining=Math.max(0,CTX_LIMIT-String(contextText||"").length-String(prompt||"").length);
  const contextUsed=CTX_LIMIT-contextRemaining;
  const contextPct=Math.max(0,Math.min(100,Math.round(contextRemaining/CTX_LIMIT*100)));
  const ask=(overridePrompt="",options={})=>{
    if(busy)return;
    const q=String(overridePrompt||prompt||"").trim();
    if(!q){setErr("질문을 입력해주세요.");return;}
    const displayText=String(options?.displayText||"").trim()||q;
    if(overridePrompt)setPrompt("");
    const userMsg={id:`u-${Date.now()}`,role:"user",text:displayText,prompt:q,ts:new Date().toISOString()};
    const context={type:"home_flowi_chat",limit_chars:CTX_LIMIT,remaining_chars:contextRemaining,messages:contextMessages,chart_session_id:activeChartSessionId||""};
    setMessages(prev=>[...prev,userMsg]);
    setActive(true);setBusy(true);setErr("");setLastPrompt(q);setLiveSteps([]);
    const started=Date.now();
    let endpoint="/api/llm/flowi/chat";
    const runId=newFlowiRunId();
    runIdRef.current=runId;
    let body={prompt:q,product:"",max_rows:12,run_id:runId,context};
    if(q.toUpperCase().startsWith("FLOWI_EDM_PROPOSE ")){
      endpoint="/api/llm/flowi/edm/propose";
      runIdRef.current="";
      try{body=JSON.parse(q.slice("FLOWI_EDM_PROPOSE ".length).trim());}
      catch(e){setErr("FLOWI_EDM_PROPOSE JSON parse 실패: "+e.message);setBusy(false);return;}
    }
    const controller=typeof AbortController!=="undefined"?new AbortController():null;
    const timeoutId=controller?setTimeout(()=>controller.abort(),FLOWI_CLIENT_TIMEOUT_MS):null;
    sf(endpoint,{
      method:"POST",
      headers:{"Content-Type":"application/json"},
      body:JSON.stringify(body||{}),
      signal:controller?.signal,
    })
      .then(d=>{
        const enriched={...(d||{}),elapsed_ms:Date.now()-started};
        const sid=enriched?.tool?.chart_session_id||enriched?.tool?.chart_result?.chart_session_id||"";
        if(sid)setActiveChartSessionId(sid);
        setResult(enriched);
        setMessages(prev=>[...prev,{id:`a-${Date.now()}`,role:"assistant",answer:enriched?.answer||"",prompt:q,result:enriched,intent:enriched?.tool?.intent||"",ts:new Date().toISOString()}]);
        setPrompt("");
        // Flow-i 결과는 검색창 안에서 먼저 보여준다. 전체 페이지 이동은 사용자가
        // 결과 상단의 명시적 버튼을 눌렀을 때만 수행한다.
      }).catch(e=>{
        const timedOut=e?.name==="AbortError";
        const msg=timedOut
          ?`${FLOWI_CLIENT_TIMEOUT_S}초 안에 결과가 없어 요청을 중단했습니다. 조건을 더 좁히거나 같은 질문을 다시 실행해 주세요.`
          :(e.message||String(e));
        const failure={
          ok:false,
          answer:msg,
          elapsed_ms:Date.now()-started,
          tool:{handled:false,blocked:timedOut,intent:timedOut?"client_timeout":"request_error",action:timedOut?"client.timeout":"request.error",feature:"home",answer:msg},
          llm:{available:!!modelLabel,used:false},
        };
        setResult(failure);
        setMessages(prev=>[...prev,{id:`a-${Date.now()}`,role:"assistant",answer:msg,prompt:q,result:failure,intent:failure.tool.intent,ts:new Date().toISOString()}]);
        setErr("");
      }).finally(()=>{
        if(timeoutId)clearTimeout(timeoutId);
        setBusy(false);
      });
  };
  const connLabel=connState==="checking"?"연결확인중":connState==="connected"?"연결":connState==="delayed"?"연결 확인 지연":connState==="verify_failed"?"LLM 확인 실패":connState==="unavailable"?"LLM 미설정":"";
  const connColor=connState==="connected"?HOME_UI.ok:(connState==="checking"||connState==="delayed"||connState==="verify_failed")?HOME_UI.accent:HOME_UI.bad;
  return(<section style={{marginTop:12,fontFamily:"'JetBrains Mono',monospace"}}>
    <style>{`@keyframes flowiPanelWake{0%{opacity:0;transform:translateY(-8px) scaleY(.96)}100%{opacity:1;transform:translateY(0) scaleY(1)}}@keyframes flowiConnBlink{0%,100%{opacity:.45}50%{opacity:1}}`}</style>
    <form onSubmit={e=>{e.preventDefault();activate();}} style={{margin:0}}>
      <div style={{display:"flex",alignItems:"center",gap:7,minWidth:0,fontSize:14,lineHeight:1.7,flexWrap:"wrap"}}>
        <span style={{color:HOME_UI.accent}}>{">"}</span>
        <span style={{color:HOME_UI.textDim,whiteSpace:"nowrap"}}>flow-i</span>
        {active&&connLabel&&<span title={verifyError?`LLM 확인 실패: ${verifyError}`:(modelLabel?`LLM ${modelLabel}`:"LLM 연결 확인")} style={{display:"inline-flex",alignItems:"center",gap:5,color:connColor,border:`1px solid ${connColor}66`,background:`${connColor}14`,borderRadius:999,padding:"1px 8px",fontSize:14,fontFamily:"monospace",fontWeight:800,whiteSpace:"nowrap"}}>
          <span style={{width:6,height:6,borderRadius:"50%",background:connColor,animation:connState==="checking"?"flowiConnBlink .75s ease-in-out infinite":"none"}}/>{connLabel}
        </span>}
        {active&&verifyError&&<span style={{color:HOME_UI.textDim,fontSize:14,fontFamily:"monospace",minWidth:0,maxWidth:420,overflow:"hidden",textOverflow:"ellipsis",whiteSpace:"nowrap"}}>
          {verifyError}
        </span>}
        {!active&&<button type="submit" aria-label="start flowi"
          style={{padding:"2px 8px",borderRadius:5,border:`1px solid ${HOME_UI.borderStrong}`,background:HOME_UI.terminal,color:HOME_UI.accent,fontSize:14,fontFamily:"monospace",fontWeight:800,cursor:"pointer"}}>START</button>}
        {active&&<button type="button" onClick={close} aria-label="close flowi"
          style={{padding:"1px 6px",borderRadius:5,border:`1px solid ${HOME_UI.borderStrong}`,background:"transparent",color:HOME_UI.textDim,fontSize:14,fontFamily:"monospace",cursor:"pointer"}}>CLOSE</button>}
      </div>
    </form>
    {active&&<div style={{marginTop:10,border:`1px solid ${HOME_UI.borderSoft}`,borderRadius:10,background:"#101010",overflow:"hidden",animation:"flowiPanelWake .32s ease-out",transformOrigin:"top"}}>
      <div ref={scrollRef} style={{height:messages.length?"clamp(520px, 72vh, 860px)":340,maxHeight:"calc(100vh - 230px)",overflow:"auto",padding:"14px 16px",borderBottom:"1px solid #262626",scrollBehavior:"smooth"}}>
        {messages.length===0&&!busy&&<div style={{height:"100%",display:"flex",alignItems:"center",justifyContent:"center",color:"#d4d4d4",fontSize:14,fontWeight:800,textAlign:"center"}}>
          오늘 어떤 도움을 드릴까요?
        </div>}
        {messages.map(m=>m.role==="user"
          ?<div key={m.id} style={{display:"flex",justifyContent:"flex-end",margin:"0 0 10px"}}>
            <div style={{maxWidth:"92%",background:"#1f130b",border:"1px solid #7c2d12",borderRadius:"10px 10px 2px 10px",padding:"8px 10px",color:"#f5f5f5",fontSize:14,lineHeight:1.55,whiteSpace:"pre-wrap",overflowWrap:"anywhere"}}>{m.text}</div>
          </div>
          :<div key={m.id} style={{margin:"0 0 16px",maxWidth:"100%"}}>
            <div style={{fontSize:14,color:HOME_UI.textDim,fontFamily:"monospace",marginBottom:4}}>flow-i{isAdmin&&m.intent?` · ${m.intent}`:""}</div>
            <FlowiResult busy={false} error="" result={m.result} prompt={m.prompt} onNavigate={onNavigate} onChoice={ask} embedded isAdmin={isAdmin} activeChartSessionId={activeChartSessionId} onUseChartSession={setActiveChartSessionId}/>
          </div>)}
        {busy&&<FlowiLiveTrace steps={liveSteps} elapsed={liveElapsed} prompt={lastPrompt}/>}
      </div>
      <form onSubmit={e=>{e.preventDefault();ask();}} style={{margin:0,padding:"10px 10px 10px 0"}}>
      <div style={{display:"flex",alignItems:"stretch",gap:8,minWidth:0}}>
        <span style={{color:HOME_UI.accent}}>{">"}</span>
        <div style={{position:"relative",flex:1,minWidth:0}}>
          {/* flowi-chat-input: carbon.css 가 모든 textarea 배경을 --bg-hover
              (라이트 테마에선 거의 흰색)로 !important 고정한다 — 이 클래스로
              어두운 회색을 되돌린다. */}
          <textarea ref={promptRef} value={prompt} onChange={e=>setPrompt(e.target.value)}
            placeholder=""
            aria-label="Flowi prompt"
            className="flowi-chat-input"
            rows={5}
            onKeyDown={e=>{if(e.key==="Enter"&&!e.shiftKey){if(e.nativeEvent?.isComposing||e.keyCode===229)return;e.preventDefault();ask();}}}
            style={{width:"100%",minWidth:0,padding:isAdmin?"10px 12px 48px":"10px 12px",borderRadius:8,border:"1px solid #525252",background:"#3a3a3a",color:"#f5f5f5",fontSize:14,lineHeight:1.55,fontFamily:"'JetBrains Mono',monospace",outline:"none",resize:"vertical",boxSizing:"border-box",display:"block"}}/>
          {isAdmin&&<div title="현재 연결 모델과 남은 대화 context 추정치" style={{position:"absolute",right:10,bottom:8,display:"flex",gap:6,alignItems:"center",justifyContent:"flex-end",maxWidth:"calc(100% - 20px)",pointerEvents:"none",fontFamily:"'JetBrains Mono',monospace"}}>
            <span style={{minWidth:0,maxWidth:260,overflow:"hidden",textOverflow:"ellipsis",whiteSpace:"nowrap",fontSize:14,lineHeight:1.1,color:modelLabel?HOME_UI.textSoft:HOME_UI.textDim,border:`1px solid ${HOME_UI.borderStrong}`,background:"#0f0f0f",borderRadius:999,padding:"6px 9px",fontWeight:900}}>
              MODEL {modelLabel||"미연결"}
            </span>
            <span style={{whiteSpace:"nowrap",fontSize:14,lineHeight:1.1,color:contextPct<20?"#fb923c":HOME_UI.textSoft,border:`1px solid ${contextPct<20?HOME_UI.accentLine:HOME_UI.borderStrong}`,background:contextPct<20?"#2a1207":"#0f0f0f",borderRadius:999,padding:"6px 9px",fontWeight:900}}>
              CTX {contextUsed.toLocaleString()} / {CTX_LIMIT.toLocaleString()}
            </span>
          </div>}
        </div>
        {busy&&<div aria-live="polite" style={{alignSelf:"center",color:HOME_UI.accent,fontSize:14,fontFamily:"monospace",fontWeight:800,whiteSpace:"nowrap"}}>RUNNING</div>}
      </div>
      </form>
    </div>}
    {err&&<FlowiResult busy={false} error={err} result={null} prompt={lastPrompt} onNavigate={onNavigate} onChoice={ask} isAdmin={isAdmin} activeChartSessionId={activeChartSessionId} onUseChartSession={setActiveChartSessionId}/>}
  </section>);
}

const FLOWI_ACTION_BTN={fontSize:14,color:HOME_UI.accent,fontFamily:"monospace",border:"1px solid #7c2d12",borderRadius:6,padding:"4px 8px",background:"#1f130b",cursor:"pointer",fontWeight:800,whiteSpace:"nowrap"};

function flowiShortText(value,max=140){
  const text=String(value??"").replace(/\s+/g," ").trim();
  return text.length>max?`${text.slice(0,max-1)}...`:text;
}

function flowiUniqueLines(lines,max=6){
  const seen=new Set();
  const out=[];
  (Array.isArray(lines)?lines:[]).forEach(line=>{
    const text=String(line||"").replace(/\s+/g," ").trim();
    if(!text||seen.has(text))return;
    seen.add(text);
    out.push(text);
  });
  return out.slice(0,max);
}

function flowiIsStepIdToken(value){
  return /^[A-Z]{2}\d{6}(?:[A-Z]{1,4})?$/i.test(String(value||"").trim());
}

function flowiIsRootLotToken(value){
  const token=String(value||"").trim();
  return /^[A-Z0-9]{5}$/i.test(token)&&/[A-Z]/i.test(token)&&/\d/.test(token)&&!flowiIsStepIdToken(token);
}

function flowiPromptEntities(prompt){
  const text=String(prompt||"").replace(/\s+/g," ").trim();
  if(!text)return {text:"",rootLot:"",stepId:"",knob:"",hasSplit:false,hasChart:false,hasFab:false,hasFile:false};
  const tokens=[...(text.matchAll(/\b[A-Z0-9][A-Z0-9_.-]*\b/gi))].map(m=>m[0]);
  const stepId=tokens.find(flowiIsStepIdToken)||"";
  const rootLot=tokens
    .find(v=>!String(v).includes(".")&&flowiIsRootLotToken(v))||"";
  const hasSplit=/(split\s*table|split|knob|스플릿|노브)/i.test(text);
  const hasChart=/(chart|plot|scatter|trend|그래프|차트|산점도|추이)/i.test(text);
  const hasFab=/(fab|current\s*location|progress|현재\s*위치|진행\s*상태|공정\s*진행)/i.test(text);
  const hasFile=/(filebrowser|sql|raw\s*data|csv|parquet|파일|원천\s*데이터|로우\s*데이터)/i.test(text);
  const hasMeasurement=/(측정값|값\s*(?:몇|보여|알려)|몇이야|measurement)/i.test(text);
  let knob="";
  if(hasSplit){
    let scope=text;
    if(rootLot){
      const idx=text.toLowerCase().indexOf(rootLot.toLowerCase());
      if(idx>=0)scope=text.slice(idx+rootLot.length).trim();
    }
    const beforeKeyword=scope.match(/^(.{1,90}?)(?=\s*(?:split\s*table|split|knob|스플릿\s*테이블|스플릿테이블|스플릿|노브|\(|보여|찾아|조회|검색|$))/i);
    const raw=beforeKeyword?.[1]||"";
    knob=raw.replace(/\([^)]*\)/g," ")
      .replace(/\b(?:split|table|knob|or|show|find|search)\b/gi," ")
      .replace(/(?:스플릿\s*테이블|스플릿테이블|스플릿|노브)/g," ")
      .replace(/(?:보여줘|보여|찾아줘|찾아|조회해줘|조회|검색해줘|검색)/g," ")
      .replace(/^(?:은|는|이|가|을|를|의|에서|으로|로)\s+/,"")
      .replace(/\s+(?:은|는|이|가|을|를|의|에서|으로|로)$/,"")
      .replace(/\s+/g," ")
      .trim();
    if(rootLot&&knob.toLowerCase().startsWith(rootLot.toLowerCase()))knob=knob.slice(rootLot.length).trim();
    if(/^(?:은|는|이|가|을|를|의|에서|으로|로)?$/i.test(knob))knob="";
  }
  return {text,rootLot,stepId,knob,hasSplit,hasChart,hasFab,hasFile,hasMeasurement};
}

function flowiPromptProgressLines(prompt,tool={},phase="result"){
  const entity=flowiPromptEntities(prompt);
  const feature=String(tool?.feature||"").toLowerCase();
  const kind=String(tool?.table?.kind||tool?.split_view?.kind||tool?.type||"").toLowerCase();
  const splitRequested=entity.hasSplit||feature==="splittable"||kind.includes("split")||kind.includes("knob");
  const chartRequested=entity.hasChart||feature==="dashboard"||kind.includes("chart");
  const fabRequested=entity.hasFab||feature==="fab"||kind.includes("fab");
  const fileRequested=entity.hasFile||feature==="filebrowser"||kind.includes("sql");
  const measurementRequested=entity.hasMeasurement||kind.includes("semantic_measurement")||String(tool?.action||"").includes("semantic_measurement");
  const running=phase==="live";
  const lines=[];
  const subject=entity.stepId?`${entity.stepId} step_id`:entity.rootLot?`${entity.rootLot} root lot`:"질문";
  if(measurementRequested){
    lines.push(`${subject} 기준으로 측정값과 관련 source/item 매핑을 확인하는 요청으로 이해했습니다.`);
    lines.push(running?"측정 용어와 데이터 위치를 확인한 뒤 결과를 준비하고 있습니다.":"측정 용어와 source/item 매핑을 확인해 결과를 정리했습니다.");
  }else if(splitRequested){
    lines.push(entity.knob
      ?`${subject} 기준으로 SplitTable 조회와 ${entity.knob} knob 조건 확인 요청을 이해했습니다.`
      :`${subject} 기준으로 SplitTable 조회 요청을 이해했습니다.`);
    lines.push(running?"SplitTable 데이터를 조회해 화면에 바로 보여줄 결과를 준비하고 있습니다.":"SplitTable 조회 결과를 요약과 인라인 표로 정리했습니다.");
  }else if(chartRequested){
    lines.push(`${subject} 기준으로 Dashboard 차트 요청을 확인했습니다.`);
    lines.push(running?"필요한 데이터와 차트 구성을 확인하고 있습니다.":"차트 결과를 화면에서 확인할 수 있게 정리했습니다.");
  }else if(fabRequested){
    lines.push(`${subject} 기준으로 FAB 진행 상태나 현재 위치를 확인하는 요청으로 이해했습니다.`);
    lines.push(running?"진행 상태 데이터를 조회하고 있습니다.":"FAB 조회 결과를 정리했습니다.");
  }else if(fileRequested){
    lines.push(`${subject} 기준으로 FileBrowser/SQL 데이터 확인 요청을 이해했습니다.`);
    lines.push(running?"읽기 전용 조회 경로로 데이터를 확인하고 있습니다.":"조회 결과를 화면에 정리했습니다.");
  }else if(entity.stepId){
    lines.push(`${subject}의 공정/기능 step 정보를 확인하는 요청으로 이해했습니다.`);
    if(running)lines.push("step 기준 데이터를 조회하고 답변을 준비하고 있습니다.");
  }else if(entity.rootLot){
    lines.push(`${subject} 관련 데이터를 확인하는 요청으로 이해했습니다.`);
    if(running)lines.push("필요한 단위기능을 확인하고 답변을 준비하고 있습니다.");
  }
  return flowiUniqueLines(lines,3);
}

function flowiInterpretationLines(trace,tool){
  const interpretation=trace?.interpretation||{};
  const slots=interpretation.input_slots||tool?.slots||{};
  const missing=Array.isArray(interpretation.missing_slots)?interpretation.missing_slots:(Array.isArray(tool?.missing)?tool.missing:[]);
  const terms=Array.isArray(interpretation.term_resolution)?interpretation.term_resolution:[];
  const knowledge=Array.isArray(trace?.retrieved_knowledge)?trace.retrieved_knowledge:[];
  const slotLabels=[
    ["product","제품"],["root_lot_id","Root Lot"],["root_lot","Root Lot"],["lot","Lot"],["wafer","Wafer"],["step","Step"],["knob","Knob"],["knobs","Knob"],["semantic_term","측정용어"],["agg","집계"],["item","항목"],["source_candidates","소스"],
  ];
  const slotParts=slotLabels.map(([key,label])=>{
    const raw=slots[key];
    const text=Array.isArray(raw)?raw.filter(Boolean).join(", "):String(raw??"").trim();
    return text?`${label} ${flowiShortText(text,70)}`:"";
  }).filter(Boolean);
  const termParts=terms.map(row=>{
    const token=flowiShortText(row?.token||row?.term||"",48);
    const meaning=flowiShortText(row?.meaning||row?.query_filter||row?.status||"",90);
    return token&&meaning?`${token} -> ${meaning}`:"";
  }).filter(Boolean);
  const lines=[];
  if(slotParts.length)lines.push(`질문에서 ${slotParts.slice(0,6).join(", ")}를 확인했습니다.`);
  if(termParts.length)lines.push(`${termParts.slice(0,5).join(" · ")}로 해석했습니다.`);
  if(missing.length)lines.push(`추가 확인이 필요합니다: ${missing.slice(0,5).join(", ")}.`);
  if(knowledge.length)lines.push(`Wiki/schema 근거 ${knowledge.length}건을 참고했습니다.`);
  return lines.slice(0,4);
}

function flowiMethodLine(trace,tool){
  const activation=trace?.activation||{};
  const evidence=trace?.evidence||{};
  const feature=tool?.feature||evidence.used_feature_ai||activation.feature||"Flow-i";
  const action=tool?.action||activation.action||"";
  const table=tool?.table&&typeof tool.table==="object"?tool.table:null;
  const split=tool?.split_view&&typeof tool.split_view==="object"?tool.split_view:null;
  if(tool?.blocked)return "권한과 정책을 먼저 확인해 허용되지 않은 작업은 실행하지 않았습니다.";
  if(split)return "SplitTable 화면 API를 read-only로 호출해 같은 셀 기준의 인라인 결과로 표시합니다.";
  if(table)return `${feature} 기능을 read-only로 호출하고 ${table.total??(Array.isArray(table.rows)?table.rows.length:0)}건의 결과 표를 구성합니다.`;
  if(action)return `${feature} 기능의 ${action} 결과를 홈 화면에서 바로 확인합니다.`;
  return "";
}

function FlowiPlainProgressText({trace,tool,prompt,execution={}}){
  const explicitNotes=Array.isArray(tool?.interpretation_notes)
    ?tool.interpretation_notes.map(x=>String(x||"").trim()).filter(Boolean)
    :[];
  const understood=flowiUniqueLines([...explicitNotes,...flowiPromptProgressLines(prompt,tool,"result"),...flowiInterpretationLines(trace,tool)],1)[0]||"질문을 확인했습니다.";
  const activation=trace?.activation||{};
  const unit=String(tool?.unit_ai||tool?.orchestrator||activation?.unit_ai||"").trim();
  const feature=String(tool?.feature||activation?.feature||"Flow-i").trim();
  const action=String(tool?.action||activation?.action||"").trim();
  const target=execution?.target==="development_worker"?"개발 worker":execution?.target==="production_api_fallback"?"운영 API (worker 폴백)":execution?.target==="production_api"?"운영 API":"";
  const orchestrated=[unit,feature&&action?`${feature}.${action}`:(action||feature),target].filter(Boolean).join(" → ");
  const sources=flowiSourceValues(tool,tool?.table||{}).slice(0,2);
  const rows=[
    ["이해",understood],
    ["처리",orchestrated||flowiMethodLine(trace,tool)||"Flow-i 오케스트레이터"],
    ...(sources.length?[["근거",sources.join(" · ")]]:[]),
  ];
  return <div style={{marginTop:9,border:"1px solid #2a2a2a",borderRadius:8,background:"#141414",padding:"8px 10px",display:"grid",gap:5}}>
    {rows.map(([label,text])=><div key={label} style={{display:"grid",gridTemplateColumns:"42px minmax(0,1fr)",gap:8,fontSize:13,lineHeight:1.5}}>
      <span style={{color:"#f97316",fontWeight:900,fontFamily:"monospace"}}>{label}</span>
      <span style={{color:label==="이해"?"#d4d4d4":"#a3a3a3",overflowWrap:"anywhere"}}>{text}</span>
    </div>)}
  </div>;
}

function flowiResultShellStyle(embedded=false,isClarificationOnly=false){
  if(isClarificationOnly){
    return {width:"100%",boxSizing:"border-box",marginTop:embedded?0:8,padding:"2px 0 0",background:"transparent",border:"0",borderRadius:0,overflow:"visible"};
  }
  return {width:"100%",boxSizing:"border-box",marginTop:embedded?0:12,border:embedded?"1px solid #2a2a2a":"1px solid #333",borderRadius:10,padding:12,background:"#111",overflow:"visible"};
}

function FlowiResult({busy,error,result,prompt,onNavigate,onChoice,embedded=false,isAdmin=false,activeChartSessionId="",onUseChartSession=null}){
  if(busy)return <div style={{marginTop:embedded?0:10,fontSize:14,color:"#a3a3a3",fontFamily:"monospace"}}>local tools + llm 처리 중...</div>;
  if(error)return <div style={{marginTop:10,padding:"9px 10px",borderRadius:6,background:"#7f1d1d33",color:"#fca5a5",fontSize:14,border:"1px solid #7f1d1d"}}>{error}</div>;
  if(!result)return null;
  const tool=result.tool||{};
  const table=tool.table&&Array.isArray(tool.table.rows)?tool.table:null;
  const choices=Array.isArray(tool?.clarification?.choices)?tool.clarification.choices.slice(0,3):[];
  const argumentChoices=tool.arguments_choices||result.arguments_choices||{};
  const hasArgumentChoices=argumentChoices&&Array.isArray(argumentChoices.fields)&&argumentChoices.fields.length>0;
  const missingFreetext=Array.isArray(tool.missing_freetext)?tool.missing_freetext:(Array.isArray(result.missing_freetext)?result.missing_freetext:[]);
  const hasMissingFreetext=missingFreetext.length>0;
  const partialPrompt=tool.last_partial_prompt||result.last_partial_prompt||prompt;
  const walkthrough=tool.walkthrough||{};
  const workflow=tool.workflow_state||result.workflow_state||{};
  const chart=tool?.chart&&typeof tool.chart==="object"?tool.chart:null;
  const chartResult=tool?.chart_result&&typeof tool.chart_result==="object"?tool.chart_result:null;
  const chartSessionId=tool?.chart_session_id||chartResult?.chart_session_id||"";
  const summary=flowiResultSummary(tool,result);
  const actions=flowiResultActions(tool,table,chartResult,onNavigate);
  const hasResultArtifact=!!(table||chart||chartResult||tool.split_view
    ||(Array.isArray(tool.lot_list)&&tool.lot_list.length)
    ||(Array.isArray(tool.rows)&&tool.rows.length)
    ||(Array.isArray(tool.knobs)&&tool.knobs.length)
    ||(Array.isArray(tool.blocks)&&tool.blocks.length)
    ||tool.sql_draft
    ||(walkthrough&&walkthrough.session_id)
    ||(result.proposal&&result.confirm));
  const hasInputControls=!!(choices.length||hasArgumentChoices||hasMissingFreetext);
  const hasMissing=!!((Array.isArray(tool.missing)&&tool.missing.length)||(Array.isArray(result.missing)&&result.missing.length));
  const isClarificationOnly=!!(!hasResultArtifact
    &&(hasInputControls||hasMissing||tool.needs_input||result.needs_input||String(tool.action||"").startsWith("clarify_")||String(workflow.status||"").startsWith("awaiting")));
  const plain=!hasResultArtifact&&!!result.answer;
  const showDiagnostics=!!(isAdmin&&hasResultArtifact);
  const emptyHint=!result.answer&&(tool.missing||hasArgumentChoices||hasMissingFreetext)
    ?"필요한 조건이 조금 더 있어요. 아래 선택지나 직접 입력으로 이어서 알려주세요."
    :"표시할 결과가 비어 있습니다. 조건을 조금 더 좁혀서 다시 물어봐 주세요.";
  return(<div className="flow-fixed" style={flowiResultShellStyle(embedded,isClarificationOnly||plain)}>
    {!isClarificationOnly&&(!plain||actions.length>0)&&<div style={{display:"flex",alignItems:"center",justifyContent:"space-between",gap:10,marginBottom:8}}>
      <div style={{minWidth:0,fontSize:14,color:"#e5e5e5",fontWeight:900,fontFamily:"'JetBrains Mono',monospace",whiteSpace:"nowrap",overflow:"hidden",textOverflow:"ellipsis"}}>{plain?"":summary}</div>
      {actions.length>0&&<div style={{display:"flex",gap:6,alignItems:"center",justifyContent:"flex-end",flexWrap:"wrap"}}>{actions.map(a=><button key={a.key} type="button" onClick={a.onClick} title={a.title} style={FLOWI_ACTION_BTN}>{a.label}</button>)}</div>}
    </div>}
    <FlowiMarkdown text={result.answer||emptyHint}/>
    <FlowiInlineContent tool={tool} table={table} chart={chart} chartResult={chartResult}/>
    {!isClarificationOnly&&!hasResultArtifact&&<FlowiPlainProgressText trace={result.trace} tool={tool} prompt={prompt} execution={result.execution||{}}/>}
    {choices.length>0&&!hasArgumentChoices&&!hasMissingFreetext&&<FlowiChoices question={tool.clarification?.question} choices={choices} onChoice={onChoice} onNavigate={onNavigate}/>}
    {hasArgumentChoices&&<FlowiArgumentChoices data={argumentChoices} basePrompt={partialPrompt} onChoice={onChoice}/>}
    {hasMissingFreetext&&<FlowiMissingFreetext fields={missingFreetext} basePrompt={partialPrompt} onChoice={onChoice}/>}
    <FlowiSourceEvidence tool={tool} table={table}/>
    {chartSessionId&&<div style={{marginTop:8,display:"flex",gap:7,alignItems:"center",flexWrap:"wrap"}}>
      <button type="button" onClick={()=>onUseChartSession&&onUseChartSession(chartSessionId)}
        style={{fontSize:14,color:"#f97316",fontFamily:"monospace",border:"1px solid #7c2d12",borderRadius:999,padding:"3px 9px",background:activeChartSessionId===chartSessionId?"#2a1608":"#1f130b",cursor:"pointer",fontWeight:900}}>
        수정 요청
      </button>
      <span style={{fontSize:14,color:"#737373",fontFamily:"monospace"}}>{String(chartSessionId).slice(0,12)}</span>
    </div>}
    {walkthrough&&walkthrough.session_id&&<FlowiWalkthrough data={walkthrough}/>}
    {isAdmin&&result.proposal&&result.confirm&&<FlowiEdmProposal result={result}/>}
    {showDiagnostics&&<FlowiDiagnosticsDetails result={result} tool={tool} trace={result.trace} prompt={prompt} isAdmin={isAdmin} plain={plain}/>}
    {!isClarificationOnly&&<FlowiFeedback result={result} tool={tool} prompt={prompt} isAdmin={isAdmin}/>}
  </div>);
}

function FlowiDiagnosticsDetails({result,tool,trace,prompt,isAdmin=false,plain=false}){
  const workflow=tool?.workflow_state||result?.workflow_state||{};
  const actionLog=result?.action_log||{};
  const timeline=Array.isArray(actionLog?.timeline)?actionLog.timeline.filter(Boolean):[];
  const summary=Array.isArray(actionLog?.summary)?actionLog.summary.filter(Boolean):[];
  const splitCall=flowiSplitApiCall(trace);
  const splitApi=tool?.split_api&&typeof tool.split_api==="object"?tool.split_api:null;
  const splitIntent=String([tool?.feature,tool?.intent,tool?.action,tool?.table?.kind,tool?.split_view?.kind].filter(Boolean).join(" ")).toLowerCase();
  const hasSplit=!!(tool?.split_view||splitCall||splitApi||splitIntent.includes("split"));
  const hasTrace=!!(trace&&(trace.interpretation||trace.evidence||trace.validation||(Array.isArray(trace.steps)&&trace.steps.length)||Array.isArray(trace.api_calls)));
  const hasRun=!!(!plain&&(result?.run_id||result?.runtime_status));
  const hasAdmin=!!(isAdmin&&!plain&&(tool?.intent||workflow.status||result?.llm));
  if(!hasRun&&!hasSplit&&!hasTrace&&!summary.length&&!timeline.length&&!hasAdmin)return null;
  return <details style={{marginTop:10,border:"1px solid #262626",borderRadius:8,background:"#101010",padding:"8px 9px",fontFamily:"'JetBrains Mono',monospace"}}>
    <summary style={{cursor:"pointer",fontSize:14,color:"#a3a3a3",fontWeight:900}}>
      실행 정보 <span style={{fontWeight:400,color:"#737373"}}>필요할 때 펼쳐보기</span>
    </summary>
    {hasRun&&<div style={{display:"flex",gap:6,alignItems:"center",margin:"8px 0 0",fontFamily:"monospace",fontSize:14,color:"#737373",flexWrap:"wrap"}}>
      {result.run_id&&<span style={{border:"1px solid #333",borderRadius:999,padding:"2px 7px",background:"#151515"}}>run {String(result.run_id).slice(0,22)}</span>}
      {result.runtime_status&&<span style={{color:flowiTraceStatusColor(result.runtime_status)}}>{result.runtime_status}</span>}
    </div>}
    <FlowiPlainProgressText trace={trace} tool={tool} prompt={prompt} execution={result?.execution||{}}/>
    <FlowiExecutionProof tool={tool} trace={trace}/>
    <FlowiActionLogPanel actionLog={actionLog} trace={trace}/>
    {hasAdmin&&<div style={{display:"flex",gap:6,marginTop:8,flexWrap:"wrap"}}>
      {tool.intent&&<span style={{fontSize:14,color:"#a3a3a3",fontFamily:"monospace",border:"1px solid #333",borderRadius:999,padding:"2px 7px"}}>{tool.intent}</span>}
      {workflow.status&&<span style={{fontSize:14,color:workflow.status.startsWith("awaiting")?"#f97316":workflow.status==="blocked"?"#ef4444":"#22c55e",fontFamily:"monospace",border:"1px solid #333",borderRadius:999,padding:"2px 7px"}}>{workflow.status}</span>}
      {result.llm&&<span style={{fontSize:14,color:result.llm.used?"#22c55e":"#737373",fontFamily:"monospace",border:"1px solid #333",borderRadius:999,padding:"2px 7px"}}>{result.llm.used?"llm used":"local result"}</span>}
    </div>}
    {isAdmin&&<FlowiTrace trace={trace}/>}
  </details>;
}

function FlowiMarkdown({text}){
  const lines=String(text||"").split("\n");
  return <div style={{whiteSpace:"pre-wrap",fontSize:14,lineHeight:1.75,color:"#d4d4d4",overflowWrap:"anywhere"}}>
    {lines.map((line,i)=>{
      const m=line.match(/^([^:：]{1,24})[:：]\s*(.*)$/);
      if(m&&m[2])return <div key={i} style={{marginTop:i?4:0}}><span style={{color:"#f5f5f5",fontWeight:900}}>{m[1]}: </span><span>{m[2]}</span></div>;
      return <div key={i} style={{marginTop:i&&line.trim()?4:0}}>{line}</div>;
    })}
  </div>;
}

function flowiTableColumns(table){
  const rows=Array.isArray(table?.rows)?table.rows:[];
  const cols=Array.isArray(table?.columns)?table.columns:[];
  if(cols.length)return cols.map(c=>typeof c==="string"?{key:c,label:c}:{key:c.key||c.label,label:c.label||c.key}).filter(c=>c.key);
  if(rows.length&&rows[0]&&typeof rows[0]==="object")return Object.keys(rows[0]).filter(k=>!String(k).startsWith("__")).map(k=>({key:k,label:k}));
  return [];
}

function flowiResultType(tool,table,chartResult){
  if(tool?.type)return String(tool.type);
  if(tool?.teg_shot_view)return "teg_shot_view";
  if(tool?.download_job)return "download_job";
  if(chartResult||tool?.chart)return "chart";
  if(tool?.split_view)return "split_view";
  if(Array.isArray(tool?.lot_list))return "lot_list";
  if(table||Array.isArray(tool?.rows)||Array.isArray(tool?.knobs))return "table";
  return "message";
}

function flowiResultSummary(tool,result){
  if(tool?.inline_summary)return tool.inline_summary;
  const table=tool?.table;
  const chart=tool?.chart_result||tool?.chart;
  if(tool?.raw_data_download)return `Chart raw data ${tool.raw_data_download.row_count??""} rows`;
  if(tool?.split_view)return `${tool.split_view.title||"SplitTable"} ${tool.split_view.total??(tool.split_view.rows||[]).length}개 셀`;
  if(Array.isArray(tool?.lot_list)&&tool.lot_list.length)return `Lot list ${tool.lot_list.length}건`;
  if(table&&Array.isArray(table.rows)){
    const cols=flowiTableColumns(table);
    return `${table.title||table.kind||"Flowi table"} ${table.total??table.rows.length} rows · ${cols.length} columns`;
  }
  if(chart)return chart.title||chart.kind||"Flowi chart";
  return result?.answer?"Flowi 응답":"Flowi 결과";
}

// tool.navigate={tab,search,auto} — 백엔드 유닛이 준 딥링크로 탭을 연다 (query 유지).
function flowiNavigate(navigate){
  const tab=String(navigate?.tab||"").trim();
  if(!tab)return;
  try{window.dispatchEvent(new CustomEvent("flow:navigate",{detail:{tab,search:String(navigate?.search||"")}}));}
  catch(_){/* noop */}
}

function flowiResultActions(tool,table,chartResult,onNavigate){
  const items=[];
  const canNav=typeof onNavigate==="function";
  const feature=tool?.feature||"";
  const kind=String(table?.kind||tool?.split_view?.kind||"").toLowerCase();
  const rawDownload=tool?.raw_data_download&&typeof tool.raw_data_download==="object"?tool.raw_data_download:null;
  const chartSessionId=tool?.chart_session_id||chartResult?.chart_session_id||rawDownload?.chart_session_id||"";
  const addNav=(key,label,title)=>{if(canNav&&!items.some(x=>x.key===`nav-${key}`))items.push({key:`nav-${key}`,label,title,onClick:()=>onNavigate(key)});};
  const navigate=tool?.navigate&&typeof tool.navigate==="object"?tool.navigate:null;
  if(navigate?.tab)items.push({key:`nav-deep-${navigate.tab}`,label:navigate.label||"SplitTable 열기",title:`${navigate.tab} 화면 열기${navigate.search?` (${navigate.search})`:""}`,onClick:()=>flowiNavigate(navigate)});
  if(!navigate&&(feature==="splittable"||kind.includes("split")||kind.includes("knob")))addNav("splittable","전체화면 SplitTable","SplitTable 화면에서 전체 결과 보기");
  if(rawDownload?.url)items.push({key:"chart-raw-csv",label:"Raw CSV",title:`Chart raw data CSV 다운로드 · ${rawDownload.row_count??"-"}행`,onClick:()=>flowiDownloadChartRaw(rawDownload)});
  else if(chartSessionId)items.push({key:"chart-raw-csv",label:"Raw CSV",title:"직전 chart session raw data를 CSV로 내려받기",onClick:()=>flowiDownloadChartRaw({chart_session_id:chartSessionId})});
  if(feature==="dashboard"||chartResult||tool?.chart)addNav("dashboard","차트 페이지","Dashboard 화면에서 차트 보기");
  if(table&&Array.isArray(table.rows)&&table.rows.length)items.push({key:"export-table",label:"엑셀 내보내기",title:"현재 인라인 표를 CSV로 내려받기",onClick:()=>flowiDownloadTable(table)});
  const entries=Array.isArray(tool?.feature_entrypoints)?tool.feature_entrypoints:[];
  entries.slice(0,2).forEach(ep=>{if(ep?.key&&ep.key!==feature)addNav(ep.key,`${ep.title||ep.key} 열기`,ep.description||"관련 화면 열기");});
  return items.slice(0,4);
}

function flowiDownloadChartRaw(rawDownload){
  const sid=String(rawDownload?.chart_session_id||"").trim();
  const url=rawDownload?.url||`/api/llm/flowi/chart-session/raw-data.csv?chart_session_id=${encodeURIComponent(sid)}`;
  const filename=rawDownload?.filename||`flowi_chart_raw_${sid.slice(0,8)||"data"}.csv`;
  if(!sid&&!rawDownload?.url)return;
  dl(url,filename).catch(e=>toast.error(e?.message||"chart raw CSV 다운로드 실패"));
}

function flowiDownloadTable(table){
  const rows=Array.isArray(table?.rows)?table.rows:[];
  const cols=flowiTableColumns(table);
  if(!rows.length||!cols.length||typeof document==="undefined")return;
  const esc=(v)=>`"${String(v??"").replace(/"/g,'""')}"`;
  const csv=[cols.map(c=>esc(c.label||c.key)).join(","),...rows.map(r=>cols.map(c=>esc(r[c.key])).join(","))].join("\n");
  const blob=new Blob(["\uFEFF"+csv],{type:"text/csv;charset=utf-8"});
  const url=URL.createObjectURL(blob);
  const a=document.createElement("a");
  a.href=url;
  a.download=`flowi_${String(table.kind||"result").replace(/[^A-Za-z0-9_-]+/g,"_")}.csv`;
  document.body.appendChild(a);
  a.click();
  a.remove();
  URL.revokeObjectURL(url);
}

function flowiSplitStView(tool){
  const st=tool?.splittable_view&&typeof tool.splittable_view==="object"?tool.splittable_view:null;
  if(st&&Array.isArray(st.headers)&&Array.isArray(st.rows)&&st.rows.some(r=>r&&typeof r==="object"&&r._cells))return st;
  return null;
}

function flowiSplitProduct(tool,stView){
  return stView?.product||tool?.filters?.product||tool?.arguments?.product||"";
}

function FlowiInlineContent({tool,table,chart,chartResult}){
  const type=flowiResultType(tool,table,chartResult);
  const explicitBlocks=Array.isArray(tool?.blocks)?tool.blocks:[];
  if(explicitBlocks.length)return <>{explicitBlocks.map((block,i)=><FlowiResultBlock key={block?.id||`${block?.kind||"block"}-${i}`} block={block}/>)}</>;
  const lotList=Array.isArray(tool?.lot_list)?tool.lot_list:[];
  const rows=Array.isArray(tool?.rows)?tool.rows:[];
  const knobs=Array.isArray(tool?.knobs)?tool.knobs:[];
  const sqlDraft=tool?.sql_draft&&typeof tool.sql_draft==="object"?tool.sql_draft:null;
  const blocks=[];
  if(sqlDraft)blocks.push(<FlowiSqlDraft key="sql" draft={sqlDraft}/>);
  if((type==="teg_shot_view"||tool?.teg_shot_view)&&tool?.teg_shot_view){
    blocks.push(<FlowiTegShotView key="teg-shot" view={tool.teg_shot_view}/>);
    if(table)blocks.push(<FlowiDataTable key="teg-table" table={table}/>);
  }
  else if((type==="download_job"||tool?.download_job)&&tool?.download_job)blocks.push(<FlowiDownloadJob key="download-job" initial={tool.download_job}/>);
  else if((type==="chart"||chartResult)&&chartResult){
    const contract=tool?.chart_data_contract||chartResult?.chart_data_contract;
    blocks.push(<FlowiScatterResult key="chart-result" data={chartResult}/>);
    if(contract)blocks.push(<FlowiChartDataContract key="chart-data-contract" contract={contract}/>);
    if(contract?.mode==="rows"&&table)blocks.push(<FlowiDataTable key="chart-data-table" table={table}/>);
  }
  else if(type==="chart"&&chart)blocks.push(<FlowiChartPlan key="chart-plan" chart={chart}/>);
  else if((type==="split_view"||tool?.split_view)&&tool?.split_view){
    const stView=flowiSplitStView(tool);
    blocks.push(stView
      ? <SplitTableSnapshotView key="split" stView={stView} product={flowiSplitProduct(tool,stView)} source="Home Flow-i" maxHeight={360}/>
      : <FlowiSplitView key="split" view={tool.split_view}/>);
  }
  // A structured table is the primary artifact. Some lookup tools also carry a
  // legacy lot_list for compatibility; rendering that first turned location
  // answers such as "AB111 어디있어?" into hard-to-scan cards.
  else if(table)blocks.push(<FlowiDataTable key="table" table={table}/>);
  else if((type==="lot_list"||lotList.length>0)&&lotList.length>0)blocks.push(<FlowiLotList key="lots" items={lotList}/>);
  else if(rows.length>0)blocks.push(<FlowiDataTable key="rows" table={{kind:"flowi_rows",title:"Flowi rows",columns:_legacyRowColumns(rows),rows,total:rows.length}}/>);
  else if(knobs.length>0)blocks.push(<FlowiKnobCards key="knobs" knobs={knobs}/>);
  if(blocks.length)return <>{blocks}</>;
  return null;
}

function FlowiChartDataContract({contract}){
  if(!contract||typeof contract!=="object")return null;
  const sql=String(contract.sql||"").trim();
  const rowCount=Number(contract.row_count||0);
  const inlineLimit=Number(contract.inline_row_limit||500);
  const sqlOnly=contract.mode==="sql";
  const copySql=()=>{
    if(!sql)return;
    navigator.clipboard?.writeText(sql).then(()=>toast.ok("SQL을 복사했습니다.")).catch(()=>toast.error("SQL 복사에 실패했습니다."));
  };
  return <div style={{marginTop:10,border:"1px solid #d1d5db",borderRadius:8,background:"#fff",color:"#111827",padding:"10px 12px"}}>
    <div style={{display:"flex",alignItems:"center",justifyContent:"space-between",gap:8,flexWrap:"wrap"}}>
      <strong style={{fontSize:14}}>차트 원본 데이터</strong>
      <span style={{fontSize:12,color:"#475569",fontFamily:"monospace"}}>{rowCount.toLocaleString()} rows · {contract.db||"DB SQL"}</span>
    </div>
    <div style={{marginTop:5,fontSize:13,color:"#475569"}}>
      {sqlOnly?`데이터가 ${inlineLimit.toLocaleString()}행을 초과해 재현 가능한 SQL을 제공합니다.`:"차트에 사용한 행을 아래 표로 제공합니다."}
    </div>
    {sql&&<details open={sqlOnly} style={{marginTop:8}}>
      <summary style={{cursor:"pointer",fontSize:13,fontWeight:800,color:"#2563eb"}}>조회 SQL</summary>
      <div style={{display:"flex",justifyContent:"flex-end",margin:"6px 0"}}><button type="button" onClick={copySql} style={{border:"1px solid #cbd5e1",borderRadius:6,background:"#f8fafc",color:"#0f172a",padding:"4px 9px",cursor:"pointer",fontSize:12}}>SQL 복사</button></div>
      <pre style={{margin:0,padding:10,border:"1px solid #e2e8f0",borderRadius:6,background:"#f8fafc",color:"#0f172a",fontSize:12,whiteSpace:"pre-wrap",overflowWrap:"anywhere",overflow:"auto"}}>{sql}</pre>
    </details>}
  </div>;
}

function FlowiTegShotView({view}){
  const data=view?.map&&typeof view.map==="object"?view.map:null;
  const names=Array.isArray(view?.selected_tegs)?view.selected_tegs:[];
  const[shapeData,setShapeData]=useState(null);
  const vehicle=String(view?.vehicle||data?.vehicle||"");
  useEffect(()=>{
    if(!vehicle||data?.display?.mode!=="dev_grid"){setShapeData(null);return undefined;}
    let alive=true;
    sf(`/api/teg-map/image/shapes?vehicle=${encodeURIComponent(vehicle)}`).then(value=>{if(alive)setShapeData(value);}).catch(()=>{});
    return()=>{alive=false;};
  },[vehicle,data?.display?.mode]);
  if(!data||!data.geometry)return null;
  const selected=new Set(names);
  const color=(name)=>{
    const i=Math.max(0,names.indexOf(name));
    return ["#ef4444","#3b82f6","#22c55e","#f59e0b","#a855f7","#ec4899"][i%6];
  };
  const hasImage=Boolean(data?.display?.has_image);
  const imageUrl=hasImage?authSrc(`/api/teg-map/image?vehicle=${encodeURIComponent(vehicle)}`):"";
  return <div style={{marginTop:10,border:"1px solid #333",borderRadius:9,background:"#151515",padding:10,overflow:"auto"}}>
    <div style={{display:"flex",justifyContent:"space-between",gap:8,alignItems:"center",marginBottom:8,flexWrap:"wrap"}}>
      <strong style={{color:"#f97316",fontSize:14}}>{vehicle} · Shot 확대</strong>
      <span style={{fontSize:12,color:"#a3a3a3"}}>탭의 geometry · display 설정 그대로</span>
    </div>
    <div style={{minWidth:380,display:"flex",justifyContent:"center"}}>
      <ShotZoom data={data} selectedTegs={selected} tegColor={color} imgUrl={imageUrl} dieCells={shapeData?.dev_cells||[]} showPicture={hasImage} size={420}/>
    </div>
  </div>;
}

function FlowiDownloadJob({initial}){
  const[job,setJob]=useState(initial||{});
  const[error,setError]=useState("");
  const jobId=String(initial?.job_id||"");
  useEffect(()=>{
    if(!jobId)return undefined;
    let alive=true;
    let timer=null;
    const poll=()=>sf(initial?.status_url||`/api/reformatize/download/status?job_id=${encodeURIComponent(jobId)}`)
      .then(next=>{
        if(!alive)return;
        setJob(next);setError("");
        if(next?.state==="queued"||next?.state==="running")timer=setTimeout(poll,1200);
      })
      .catch(e=>{if(alive)setError(e?.message||"진행 상태 확인 실패");});
    poll();
    return()=>{alive=false;if(timer)clearTimeout(timer);};
  },[jobId,initial?.status_url]);
  const state=String(job?.state||"queued");
  const active=state==="queued"||state==="running";
  const ready=state==="ready";
  const pct=job?.percent===null||job?.percent===undefined?null:Number(job.percent);
  const download=()=>dl(initial?.file_url||`/api/reformatize/download/file?job_id=${encodeURIComponent(jobId)}`,job?.filename||initial?.filename||"et_download.csv")
    .catch(e=>toast.error(e?.message||"ET CSV 다운로드 실패"));
  return <div style={{marginTop:10,border:"1px solid #333",borderRadius:9,background:"#151515",padding:"10px 12px"}}>
    <div style={{display:"flex",alignItems:"center",justifyContent:"space-between",gap:10,flexWrap:"wrap"}}>
      <div>
        <div style={{color:"#f97316",fontWeight:900,fontSize:14}}>ET 다운로드</div>
        <div style={{color:"#d4d4d4",fontSize:13,marginTop:4}}>{job?.phase||(state==="queued"?`대기 중${job?.ahead>0?` · 앞에 ${job.ahead}건`:""}`:state)}</div>
      </div>
      {ready&&<button type="button" onClick={download} style={{border:"1px solid #ea580c",borderRadius:7,background:"#2a1608",color:"#fb923c",fontWeight:900,padding:"7px 11px",cursor:"pointer"}}>CSV 다운로드</button>}
    </div>
    {active&&<div style={{height:6,background:"#262626",borderRadius:999,overflow:"hidden",marginTop:9}}><div style={{height:"100%",width:`${pct===null?18:Math.max(3,Math.min(100,pct))}%`,background:"#f97316",transition:"width .25s"}}/></div>}
    <div style={{fontSize:12,color:error||state==="error"?"#fca5a5":"#737373",marginTop:7,fontFamily:"monospace"}}>
      {error||(state==="error"?(job?.error||"다운로드 작업 실패"):`job ${jobId.slice(0,12)}${job?.rows?` · ${Number(job.rows).toLocaleString()}행`:""}`)}
    </div>
  </div>;
}

function flowiSourceValues(tool,table){
  const values=[];
  const add=(value)=>{
    if(Array.isArray(value)){value.forEach(add);return;}
    if(value&&typeof value==="object"){
      [value.path,value.source,value.source_id,value.callee,value.generated_at].forEach(add);
      return;
    }
    const text=String(value??"").trim();
    if(text&&!values.includes(text))values.push(text);
  };
  add(tool?.source_ids);
  add(tool?.sources);
  add(tool?.source_detail);
  add(tool?.source);
  add(table?.source_files);
  add(table?.source);
  add(tool?.split_api?.callee);
  add(tool?.split_api?.path);
  add(tool?.cache_generated_at);
  return values.slice(0,16);
}

function FlowiSourceEvidence({tool,table}){
  const sources=flowiSourceValues(tool,table);
  const sql=String(tool?.sql_draft?.sql||tool?.filters?.sql||"").trim();
  if(!sources.length&&!sql)return null;
  return <div style={{marginTop:9,border:"1px solid #2f2f2f",borderRadius:8,background:"#141414",padding:"8px 10px"}}>
    <div style={{fontSize:13,color:"#a3a3a3",fontWeight:900,marginBottom:5}}>출처</div>
    {sources.map((source,i)=><div key={`${source}-${i}`} title={source} style={{fontSize:12,color:"#d4d4d4",fontFamily:"monospace",lineHeight:1.5,overflowWrap:"anywhere"}}>{source}</div>)}
    {sql&&<div style={{marginTop:sources.length?5:0,fontSize:12,color:"#93c5fd",fontFamily:"monospace",lineHeight:1.5,whiteSpace:"pre-wrap",overflowWrap:"anywhere"}}>재현 SQL: {sql}</div>}
  </div>;
}

function FlowiResultBlock({block}){
  if(!block||typeof block!=="object")return null;
  const kind=String(block.kind||"");
  const payload=block.payload&&typeof block.payload==="object"?block.payload:{};
  const title=block.title||payload.title||"Flowi block";
  if(kind==="lot_table"){
    return <FlowiDataTable table={{...payload,title,highlight:block.highlight||payload.highlight}}/>;
  }
  if(kind==="chart_scatter"||kind==="chart_trend"){
    return <FlowiScatterResult data={{...payload,title}}/>;
  }
  if(kind==="sql_draft")return <FlowiSqlDraft draft={payload}/>;
  if(kind==="evidence_note"){
    return <div style={{marginTop:10,border:"1px solid #333",borderRadius:8,background:"#151515",padding:"9px 10px",fontSize:14,color:"#d4d4d4",lineHeight:1.5,whiteSpace:"pre-wrap"}}>{payload.text||block.text||""}</div>;
  }
  if(Array.isArray(payload.series)||Array.isArray(payload.points)||Array.isArray(payload.groups)||Array.isArray(payload.boxes)){
    return <FlowiScatterResult data={{...payload,title}}/>;
  }
  if(Array.isArray(payload.rows))return <FlowiDataTable table={{...payload,title}}/>;
  return null;
}

function FlowiSqlDraft({draft}){
  const cols=Array.isArray(draft?.selected_columns)?draft.selected_columns:[];
  const warnings=Array.isArray(draft?.warnings)?draft.warnings:[];
  const sql=String(draft?.sql||"");
  return <div style={{marginTop:10,border:"1px solid #333",borderRadius:8,background:"#151515",padding:"9px 10px",fontFamily:"monospace"}}>
    <div style={{display:"flex",gap:8,alignItems:"center",justifyContent:"space-between",flexWrap:"wrap",marginBottom:7}}>
      <span style={{fontSize:14,color:"#f97316",fontWeight:900}}>FileBrowser SQL draft</span>
      <span style={{fontSize:14,color:draft?.fallback?"#f97316":"#22c55e"}}>{draft?.fallback?"fallback":"validated"}</span>
    </div>
    <div style={{fontSize:14,color:"#d4d4d4",lineHeight:1.55,whiteSpace:"pre-wrap",overflowWrap:"anywhere"}}>{sql||"(필터 없음)"}</div>
    {cols.length>0&&<div style={{marginTop:7,display:"flex",gap:5,flexWrap:"wrap"}}>
      {cols.slice(0,18).map(c=><span key={c} style={{fontSize:14,color:"#a3a3a3",border:"1px solid #333",borderRadius:999,padding:"2px 7px"}}>{c}</span>)}
    </div>}
    {warnings.length>0&&<div style={{marginTop:7,fontSize:14,color:"#fbbf24",lineHeight:1.45}}>
      {warnings.slice(0,4).map((w,i)=><div key={i}>{w}</div>)}
    </div>}
  </div>;
}

function _legacyRowColumns(rows){
  const keys=rows.length&&rows[0]?Object.keys(rows[0]).filter(k=>!String(k).startsWith("__")):["product","step_id","item_id","wafer_id","median","mean","count"];
  return keys.map(k=>({key:k,label:k}));
}

function FlowiKnobCards({knobs}){
  return <div style={{marginTop:10,display:"grid",gridTemplateColumns:"repeat(auto-fit,minmax(180px,1fr))",gap:8}}>
    {knobs.slice(0,8).map(k=><div key={k.knob} style={{border:"1px solid #333",borderRadius:8,padding:"8px 10px",background:"#151515"}}>
      <div style={{fontSize:14,fontWeight:800,color:"#e5e5e5",marginBottom:4}}>{k.display_name||k.knob}</div>
      {(k.values||[]).slice(0,3).map(v=><div key={String(v.value)} style={{fontSize:14,color:"#a3a3a3",fontFamily:"monospace",lineHeight:1.55}}>{String(v.value)} · {v.count}wf{Array.isArray(v.wafers)&&v.wafers.length?" · "+v.wafers.slice(0,8).join(","):""}</div>)}
    </div>)}
  </div>;
}

function FlowiLotList({items}){
  return <div style={{marginTop:10,display:"grid",gridTemplateColumns:"repeat(auto-fit,minmax(190px,1fr))",gap:8}}>
    {items.slice(0,24).map((item,i)=><div key={`${item.root_lot||item.root_lot_id||i}-${item.wafer||item.wafer_id||""}`} style={{border:"1px solid #333",borderRadius:8,padding:"9px 10px",background:"#151515",minWidth:0}}>
      <div style={{display:"flex",justifyContent:"space-between",gap:8,alignItems:"baseline",marginBottom:5}}>
        <span style={{fontSize:14,color:"#f97316",fontWeight:900,fontFamily:"monospace",whiteSpace:"nowrap",overflow:"hidden",textOverflow:"ellipsis"}}>{item.root_lot||item.root_lot_id||"-"}</span>
        <span style={{fontSize:14,color:"#a3a3a3",fontFamily:"monospace"}}>{item.product||""}</span>
      </div>
      <FlowiLotLine label="fab" value={item.fab_lot||item.fab_lot_id||item.lot_id}/>
      <FlowiLotLine label="wf" value={item.wafer||item.wafer_id}/>
      <FlowiLotLine label="step" value={item.current_step||item.current_func_step}/>
      <FlowiLotLine label="time" value={item.tkout_time}/>
      {(item.knob||item.knob_value)&&<FlowiLotLine label="knob" value={[item.knob,item.knob_value].filter(Boolean).join(" = ")}/>}
    </div>)}
  </div>;
}

function FlowiLotLine({label,value}){
  if(value===undefined||value===null||value==="")return null;
  return <div style={{display:"grid",gridTemplateColumns:"42px minmax(0,1fr)",gap:6,fontSize:14,lineHeight:1.45,fontFamily:"monospace"}}>
    <span style={{color:"#737373"}}>{label}</span><span style={{color:"#d4d4d4",whiteSpace:"nowrap",overflow:"hidden",textOverflow:"ellipsis"}} title={String(value)}>{String(value)}</span>
  </div>;
}

function FlowiSplitView({view}){
  const headers=Array.isArray(view?.headers)?view.headers:[];
  const rows=Array.isArray(view?.rows)?view.rows:[];
  if(!rows.length)return <div style={{marginTop:10,padding:"9px 10px",border:"1px solid #333",borderRadius:8,background:"#141414",fontSize:14,color:"#a3a3a3"}}>인라인으로 표시할 SplitTable 셀이 없습니다.</div>;
  const values=[];
  rows.forEach(r=>(r.cells||[]).forEach(c=>{[c.actual,c.plan].forEach(v=>{if(v!==undefined&&v!==null&&v!=="")values.push(String(v));});}));
  const uniq=[...new Set(values)].slice(0,18);
  const palette=["#1f2937","#3b2f16","#1f3a2d","#26324a","#3a2535","#243b3f","#3a2a20","#2f3340"];
  const colorFor=(v)=>{const idx=uniq.indexOf(String(v));return idx>=0?palette[idx%palette.length]:"#171717";};
  return <div style={{marginTop:10,border:"1px solid #333",borderRadius:8,overflow:"hidden",background:"#121212"}}>
    <div style={{display:"flex",justifyContent:"space-between",gap:8,padding:"8px 10px",borderBottom:"1px solid #2a2a2a",background:"#171717"}}>
      <div style={{fontSize:14,fontWeight:900,color:"#e5e5e5",fontFamily:"'JetBrains Mono',monospace"}}>{view.title||"SplitTable inline"}</div>
      <div style={{fontSize:14,color:"#737373",fontFamily:"monospace"}}>{view.total??rows.length} cells</div>
    </div>
    <div style={{overflow:"auto",maxHeight:320}}>
      <table style={{width:"100%",borderCollapse:"collapse",fontSize:14,fontFamily:"monospace",tableLayout:"fixed",minWidth:Math.max(360,120+(headers.length||1)*92)}}>
        <thead><tr>
          <th style={{position:"sticky",left:0,top:0,zIndex:2,textAlign:"left",padding:"7px 8px",borderBottom:"1px solid #333",borderRight:"1px solid #333",background:"#1f1f1f",color:"#a3a3a3",width:120}}>{view.row_label||"항목"}</th>
          {headers.map((h,i)=><th key={`${h}-${i}`} style={{position:"sticky",top:0,zIndex:1,textAlign:"center",padding:"7px 8px",borderBottom:"1px solid #333",background:"#1f1f1f",color:"#a3a3a3",width:92}}>{h}</th>)}
        </tr></thead>
        <tbody>{rows.map((r,ri)=><tr key={r.parameter||ri}>
          <td style={{position:"sticky",left:0,zIndex:1,padding:"6px 8px",borderBottom:"1px solid #262626",borderRight:"1px solid #333",background:"#151515",color:"#e5e5e5",fontWeight:900,whiteSpace:"nowrap",overflow:"hidden",textOverflow:"ellipsis"}} title={r.parameter||r.display}>{r.display||r.parameter}</td>
          {(r.cells||[]).map((c,ci)=>{
            const actual=c.actual??"";
            const plan=c.plan??"";
            const show=actual?String(actual):(plan?`plan ${plan}`:"");
            const mismatch=!!(c.mismatch||c.highlight);
            return <td key={ci} style={{padding:"6px 8px",borderBottom:"1px solid #262626",borderRight:"1px solid #262626",textAlign:"center",background:colorFor(actual||plan),color:"#e5e5e5",boxShadow:mismatch?"inset 0 0 0 2px rgba(239,68,68,0.9)":"none",whiteSpace:"normal",wordBreak:"break-word",lineHeight:1.35}}>
              {show}{mismatch&&plan&&actual&&<span style={{display:"block",fontSize:14,color:"#fca5a5"}}>plan {plan}</span>}
            </td>;
          })}
        </tr>)}</tbody>
      </table>
    </div>
  </div>;
}
const FR_TD={padding:"5px 6px",borderBottom:"1px solid #262626",color:"#d4d4d4",whiteSpace:"nowrap"};

function flowiSplitApiCall(trace){
  const calls=Array.isArray(trace?.api_calls)?trace.api_calls:[];
  return calls.find(c=>String(c?.path||"")==="/api/splittable/view"||String(c?.name||"").toLowerCase().includes("splittable view"))||null;
}

function flowiCacheLabel(cache){
  if(!cache||typeof cache!=="object")return "";
  const bits=[];
  ["status","state","source"].forEach(k=>{if(cache[k]!==undefined&&cache[k]!==null&&cache[k]!=="")bits.push(`${k} ${cache[k]}`);});
  if(cache.hit!==undefined)bits.push(`hit ${cache.hit?"yes":"no"}`);
  if(cache.fresh!==undefined)bits.push(`fresh ${cache.fresh?"yes":"no"}`);
  return bits.slice(0,3).join(" · ");
}

function FlowiExecutionProof({tool,trace}){
  const splitCall=flowiSplitApiCall(trace);
  const splitApi=tool?.split_api&&typeof tool.split_api==="object"?tool.split_api:null;
  const splitIntent=String([tool?.feature,tool?.intent,tool?.action,tool?.table?.kind,tool?.split_view?.kind].filter(Boolean).join(" ")).toLowerCase();
  const hasSplit=!!(tool?.split_view||splitCall||splitApi||splitIntent.includes("split"));
  if(!hasSplit)return null;
  const meta=splitCall?.metadata&&typeof splitCall.metadata==="object"?splitCall.metadata:{};
  const runtime=tool?.runtime_profile&&typeof tool.runtime_profile==="object"?tool.runtime_profile:(meta.runtime_profile&&typeof meta.runtime_profile==="object"?meta.runtime_profile:{});
  const cache=tool?.view_cache&&typeof tool.view_cache==="object"?tool.view_cache:(meta.view_cache&&typeof meta.view_cache==="object"?meta.view_cache:{});
  const elapsed=tool?.elapsed_ms??splitApi?.elapsed_ms??meta.elapsed_ms;
  const rows=Array.isArray(tool?.split_view?.rows)?tool.split_view.rows.length:null;
  const chips=[
    "/api/splittable/view",
    splitCall?.callee||splitApi?.callee||"routers.splittable.view_split",
    elapsed!==undefined&&elapsed!==null&&elapsed!==""?`${elapsed}ms`:"",
    rows!==null?`${rows} rows`:"",
    flowiCacheLabel(cache),
    runtime.total_ms!==undefined?`runtime ${runtime.total_ms}ms`:"",
  ].filter(Boolean);
  return <div style={{marginTop:10,border:"1px solid #2f3b2f",borderRadius:8,background:"#101611",padding:"8px 9px",fontFamily:"'JetBrains Mono',monospace"}}>
    <div style={{fontSize:14,color:"#d9f99d",fontWeight:900}}>실제 실행</div>
    <div style={{display:"flex",gap:6,flexWrap:"wrap",marginTop:7}}>
      {chips.slice(0,6).map(chip=><span key={chip} style={{fontSize:14,color:"#d4d4d4",border:"1px solid #334155",borderRadius:999,padding:"2px 7px",background:"#151515",whiteSpace:"nowrap"}}>{chip}</span>)}
    </div>
  </div>;
}

function flowiTraceStatusColor(status){
  return status==="done"||status==="success"?"#22c55e":status==="blocked"||status==="error"||status==="failed"?"#ef4444":status==="skipped"||status==="available"?"#737373":"#f97316";
}

export function newFlowiRunId(){
  const rand=Math.random().toString(36).slice(2,10);
  return `flowi-${Date.now().toString(36)}-${rand}`;
}

const FLOWI_LIVE_STEP_LIMIT=12;

/** 서버 진행 이벤트(start/end/note/done)를 화면 한 줄씩으로 접는다.
 *
 * start 는 "실행 중" 줄을 만들고, 같은 group+label 의 end 가 오면 그 줄을
 * 상태/소요시간과 함께 닫는다. note/done 은 짝이 없는 순간 이벤트라 그대로 한 줄.
 * 이벤트에는 이름·상태·소요시간만 들어 있어 내부 동작은 화면에 나오지 않는다. */
export function mergeFlowiLiveSteps(prev,events){
  const rows=Array.isArray(prev)?prev.slice():[];
  const keyOf=e=>`${e?.group||""}|${e?.label||""}`;
  for(const e of Array.isArray(events)?events:[]){
    if(!e||typeof e!=="object")continue;
    const phase=String(e.phase||"");
    if(phase==="start"){
      rows.push({id:`s${e.seq}`,gkey:keyOf(e),label:e.label||"",detail:e.detail||"",status:"running",ms:0});
    }else if(phase==="end"){
      const key=keyOf(e);
      let matched=false;
      for(let i=rows.length-1;i>=0;i--){
        if(rows[i].gkey===key&&rows[i].status==="running"){
          rows[i]={...rows[i],status:e.status||"success",ms:Number(e.ms)||0,detail:e.detail||rows[i].detail};
          matched=true;
          break;
        }
      }
      if(!matched)rows.push({id:`e${e.seq}`,gkey:key,label:e.label||"",detail:e.detail||"",status:e.status||"success",ms:Number(e.ms)||0});
    }else{
      rows.push({id:`n${e.seq}`,gkey:"",label:e.label||"",detail:e.detail||"",status:e.status||"success",ms:0});
    }
  }
  return rows.slice(-FLOWI_LIVE_STEP_LIMIT);
}

function FlowiLiveStepRow({row}){
  const color=flowiTraceStatusColor(row.status);
  const running=row.status==="running";
  return <div style={{display:"flex",alignItems:"center",gap:7,minWidth:0,fontSize:14,lineHeight:1.5}}>
    <span style={{width:6,height:6,borderRadius:999,background:color,flexShrink:0,animation:running?"flowiConnBlink .75s ease-in-out infinite":"none"}}/>
    <span style={{color:"#d4d4d4",fontWeight:800,whiteSpace:"nowrap"}}>{row.label}</span>
    {row.detail&&<span style={{color:"#8f8f8f",minWidth:0,overflow:"hidden",textOverflow:"ellipsis",whiteSpace:"nowrap"}}>{row.detail}</span>}
    <span style={{marginLeft:"auto",color:running?"#f97316":"#737373",whiteSpace:"nowrap",flexShrink:0}}>
      {running?"실행 중":row.ms?`${row.ms} ms`:row.status}
    </span>
  </div>;
}

function FlowiLiveTrace({steps=[],elapsed=0,prompt=""}){
  const rows=Array.isArray(steps)?steps:[];
  // 서버 이벤트가 아직 없을 때만 기존 추정 문구로 폴백한다 (구버전 백엔드·EDM 경로).
  const lines=rows.length?[]:flowiPromptProgressLines(prompt,{},"live");
  const delayed=elapsed>=60;
  const active=rows.find(row=>row.status==="running");
  const progressText=rows.length
    ?(active?`${active.label}${active.detail?` · ${active.detail}`:""}`:"결과를 정리하고 있습니다.")
    :elapsed<2
      ?"질문을 이해하고 있습니다."
      :elapsed<18
        ?"필요한 데이터와 단위기능을 확인하고 있습니다."
        :"답변을 정리하고 있습니다.";
  return(<div style={{marginTop:8,border:"1px solid #2a2a2a",borderRadius:8,background:"#111",padding:"9px 10px",fontFamily:"monospace"}}>
    <div style={{display:"flex",alignItems:"center",gap:8,justifyContent:"space-between",minWidth:0}}>
      <div style={{display:"flex",alignItems:"center",gap:8,minWidth:0}}>
        <span style={{width:7,height:7,borderRadius:999,background:"#f97316",display:"inline-block",animation:"flowiConnBlink .75s ease-in-out infinite",flexShrink:0}}/>
        <span style={{fontSize:14,fontWeight:900,color:"#e5e5e5",whiteSpace:"nowrap"}}>답변 준비 중</span>
        <span style={{fontSize:14,color:"#a3a3a3",minWidth:0,overflow:"hidden",textOverflow:"ellipsis",whiteSpace:"nowrap"}}>{progressText}</span>
      </div>
      <span style={{fontSize:14,color:delayed?"#fb923c":"#737373",whiteSpace:"nowrap"}}>{elapsed}s / {FLOWI_CLIENT_TIMEOUT_S}s</span>
    </div>
    {rows.length>0&&<div style={{marginTop:7,display:"grid",gap:3,borderTop:"1px solid #1f1f1f",paddingTop:7}}>
      {rows.map(row=><FlowiLiveStepRow key={row.id} row={row}/>)}
    </div>}
    {lines.length>0&&<div style={{marginTop:7,display:"grid",gap:3}}>
      {lines.map((line,i)=><div key={i} style={{fontSize:14,lineHeight:1.45,color:i===0?"#d4d4d4":"#8f8f8f",whiteSpace:"normal",overflowWrap:"anywhere"}}>{line}</div>)}
    </div>}
    {delayed&&<div style={{marginTop:7,fontSize:14,color:"#fb923c",lineHeight:1.45}}>
      응답이 길어지고 있습니다. 클라이언트는 {FLOWI_CLIENT_TIMEOUT_S}초에서 요청을 중단하고 다시 시도할 수 있게 합니다.
    </div>}
  </div>);
}

function FlowiActionLogPanel({actionLog,trace}){
  const summary=Array.isArray(actionLog?.summary)?actionLog.summary.filter(Boolean):[];
  const timeline=Array.isArray(actionLog?.timeline)?actionLog.timeline.filter(Boolean):[];
  const fallbackSummary=!summary.length?flowiInterpretationLines(trace,{}):[];
  const lines=(summary.length?summary:fallbackSummary).slice(0,6);
  if(!lines.length&&!timeline.length)return null;
  const disclaimer=actionLog?.disclaimer||trace?.note||"내부 추론 원문이 아니라 검증 가능한 실행 요약입니다.";
  return <details style={{marginTop:10,border:"1px solid #262626",borderRadius:8,background:"#101010",padding:"8px 9px",fontFamily:"'JetBrains Mono',monospace"}}>
    <summary style={{cursor:"pointer",fontSize:14,color:"#a3a3a3",fontWeight:900}}>
      실행 근거 <span style={{fontWeight:400,color:"#737373"}}>필요할 때 펼쳐보기</span>
    </summary>
    {lines.length>0&&<div style={{display:"grid",gap:4,marginTop:8}}>
      {lines.map((line,i)=><div key={i} style={{fontSize:14,lineHeight:1.5,color:i===0?"#d4d4d4":"#a3a3a3",whiteSpace:"normal",overflowWrap:"anywhere"}}>{line}</div>)}
    </div>}
    {timeline.length>0&&<details style={{marginTop:8}}>
      <summary style={{cursor:"pointer",fontSize:14,color:"#a3a3a3",fontWeight:800}}>상세 흐름</summary>
      <div style={{marginTop:8,display:"grid",gap:6}}>
        {timeline.slice(0,8).map((item,i)=><FlowiActionLogStep key={item.stage||i} item={item}/>)}
      </div>
    </details>}
    {disclaimer&&<div style={{marginTop:7,fontSize:14,color:"#737373",lineHeight:1.4}}>{disclaimer}</div>}
  </details>;
}

function FlowiActionLogStep({item}){
  const color=flowiTraceStatusColor(item?.status);
  const apiRefs=Array.isArray(item?.api_refs)?item.api_refs:[];
  const evidenceRefs=Array.isArray(item?.evidence_refs)?item.evidence_refs:[];
  const detail=[item?.detail,evidenceRefs.length?`근거 ${evidenceRefs.slice(0,4).join(", ")}`:"",apiRefs.length?`API ${apiRefs.length}`:""].filter(Boolean).join(" · ");
  return <div style={{display:"grid",gridTemplateColumns:"18px minmax(104px,150px) minmax(0,1fr)",gap:7,alignItems:"baseline",fontSize:14,lineHeight:1.38}}>
    <span style={{width:14,height:14,borderRadius:999,display:"inline-flex",alignItems:"center",justifyContent:"center",fontSize:10,border:`1px solid ${color}99`,color}}>{item?.status==="done"?"✓":item?.status==="blocked"?"!":"•"}</span>
    <span style={{color:"#d4d4d4",fontWeight:900,whiteSpace:"nowrap",overflow:"hidden",textOverflow:"ellipsis"}} title={item?.stage||""}>{item?.stage||item?.title||"-"}</span>
    <span style={{color:"#8f8f8f",whiteSpace:"nowrap",overflow:"hidden",textOverflow:"ellipsis"}} title={detail}>{item?.title||""}{detail?` · ${detail}`:""}</span>
  </div>;
}

function FlowiTraceStrip({trace}){
  const steps=Array.isArray(trace?.steps)?trace.steps.filter(Boolean):[];
  const visibleSteps=steps.filter(s=>s.visible!==false);
  const activation=trace?.activation||{};
  const evidence=trace?.evidence||{};
  const validation=trace?.validation||{};
  const interpretation=trace?.interpretation||{};
  const missing=Array.isArray(interpretation?.missing_slots)?interpretation.missing_slots:[];
  const knowledge=Array.isArray(trace?.retrieved_knowledge)?trace.retrieved_knowledge:[];
  const apiCalls=Array.isArray(trace?.api_calls)?trace.api_calls:(Array.isArray(evidence?.api_calls)?evidence.api_calls:[]);
  const llmStep=steps.find(s=>s.key==="llm");
  if(!visibleSteps.length&&!activation.feature&&!evidence.used_feature_ai&&!knowledge.length&&!missing.length)return null;
  const chips=[
    evidence.used_feature_ai||activation.feature?[`기능 ${evidence.used_feature_ai||activation.feature}`,"#d4d4d4"]:null,
    activation.action?[`action ${activation.action}`,"#a3a3a3"]:null,
    validation.rows!==undefined?[`rows ${validation.rows}`,"#22c55e"]:null,
    knowledge.length?[`Wiki ${knowledge.length}건`,"#f97316"]:null,
    llmStep?[`LLM ${llmStep.status||"pending"}`,flowiTraceStatusColor(llmStep.status)]:null,
  ].filter(Boolean);
  const primary=visibleSteps.find(s=>s.key==="knowledge")||visibleSteps.find(s=>s.key==="tool")||visibleSteps[visibleSteps.length-1]||{};
  const primaryText=[primary.label||primary.title||primary.key,primary.detail].filter(Boolean).join(" · ");
  return <div style={{margin:"10px 0 0",border:"1px solid #262626",borderRadius:8,background:"#101010",padding:"7px 9px",fontFamily:"monospace"}}>
    <div style={{display:"flex",alignItems:"center",gap:7,flexWrap:"wrap"}}>
      <span style={{fontSize:14,color:"#737373",fontWeight:900}}>실행 로그</span>
      {primaryText&&<span style={{fontSize:14,color:"#a3a3a3",minWidth:0,flex:"1 1 260px",whiteSpace:"nowrap",overflow:"hidden",textOverflow:"ellipsis"}} title={primaryText}>{primaryText}</span>}
    </div>
    {chips.length>0&&<div style={{display:"flex",gap:5,flexWrap:"wrap",marginTop:7}}>
      {chips.slice(0,5).map(([label,color])=><span key={label} style={{fontSize:14,color,border:"1px solid #2a2a2a",borderRadius:999,padding:"2px 7px",background:"#151515",whiteSpace:"nowrap"}}>{label}</span>)}
      {apiCalls.length>0&&<span style={{fontSize:14,color:"#737373",border:"1px solid #2a2a2a",borderRadius:999,padding:"2px 7px",background:"#151515",whiteSpace:"nowrap"}}>API {apiCalls.length}회</span>}
    </div>}
    {missing.length>0&&<div style={{marginTop:7,fontSize:14,color:"#f97316",lineHeight:1.4}}>
      필요한 값: {missing.join(", ")}. 아래 선택지나 직접 입력으로 이어서 진행합니다.
    </div>}
  </div>;
}

function FlowiTrace({trace}){
  const steps=Array.isArray(trace?.steps)?trace.steps:[];
  if(!steps.length&&!trace?.interpretation&&!trace?.evidence&&!trace?.validation)return null;
  const activation=trace?.activation||{};
  const interpretation=trace?.interpretation||{};
  const inputSlots=interpretation?.input_slots||{};
  const evidence=trace?.evidence||{};
  const validation=trace?.validation||{};
  const subagentChildren=Array.isArray(trace?.subagent_context?.children)?trace.subagent_context.children:[];
  const missing=Array.isArray(interpretation?.missing_slots)?interpretation.missing_slots:[];
  const warnings=Array.isArray(validation?.warnings)?validation.warnings:[];
  const knowledge=Array.isArray(trace?.retrieved_knowledge)?trace.retrieved_knowledge:[];
  const termResolution=Array.isArray(interpretation?.term_resolution)?interpretation.term_resolution:[];
  const apiCalls=Array.isArray(trace?.api_calls)?trace.api_calls:(Array.isArray(evidence?.api_calls)?evidence.api_calls:[]);
  return(<details style={{marginTop:8,border:"1px solid #2a2a2a",borderRadius:8,background:"#111",padding:"7px 9px"}}>
    <summary style={{cursor:"pointer",fontSize:14,color:"#a3a3a3",fontFamily:"monospace",fontWeight:800}}>
      실행 로그 <span style={{fontWeight:400,color:"#737373"}}>사용한 근거와 호출한 기능</span>
    </summary>
    <div style={{marginTop:8,display:"grid",gap:8,fontFamily:"monospace"}}>
      <div style={{display:"grid",gridTemplateColumns:"repeat(auto-fit,minmax(170px,1fr))",gap:6}}>
        <FlowiTraceKV label="기능 AI" value={evidence.used_feature_ai||activation.feature}/>
        <FlowiTraceKV label="Unit action" value={activation.action}/>
        <FlowiTraceKV label="Endpoint" value={evidence.endpoint||activation.api||activation.endpoint}/>
        <FlowiTraceKV label="검증" value={[validation.rows!==undefined?`rows ${validation.rows}`:"",validation.chart_readiness?`chart ${validation.chart_readiness}`:"",validation.fallback?"fallback":""].filter(Boolean).join(" · ")}/>
      </div>
      <div style={{display:"grid",gridTemplateColumns:"repeat(auto-fit,minmax(170px,1fr))",gap:6}}>
        <FlowiTraceKV label="product" value={inputSlots.product}/>
        <FlowiTraceKV label="lot" value={Array.isArray(inputSlots.lot)?inputSlots.lot.join(", "):inputSlots.lot}/>
        <FlowiTraceKV label="wafer" value={Array.isArray(inputSlots.wafer)?inputSlots.wafer.join(", "):inputSlots.wafer}/>
        <FlowiTraceKV label="step/item" value={[inputSlots.step,inputSlots.item].filter(Boolean).map(v=>Array.isArray(v)?v.join(", "):v).join(" / ")}/>
        <FlowiTraceKV label="meeting" value={[inputSlots.meeting,inputSlots.session].filter(Boolean).join(" · ")}/>
        <FlowiTraceKV label="source" value={Array.isArray(inputSlots.source_candidates)?inputSlots.source_candidates.join(", "):inputSlots.source_candidates}/>
      </div>
      {evidence.sql&&<FlowiTraceKV label="SQL/filter" value={evidence.sql} wide/>}
      {Array.isArray(evidence.selected_columns)&&evidence.selected_columns.length>0&&<FlowiTraceKV label="선택 컬럼" value={evidence.selected_columns.slice(0,12).join(", ")} wide/>}
      {Array.isArray(evidence.source_ids)&&evidence.source_ids.length>0&&<FlowiTraceKV label="source ids" value={evidence.source_ids.slice(0,6).join(", ")} wide/>}
      {Array.isArray(evidence.relation_ids)&&evidence.relation_ids.length>0&&<FlowiTraceKV label="confirmed relations" value={evidence.relation_ids.slice(0,6).join(", ")} wide/>}
      {Array.isArray(evidence.join_keys)&&evidence.join_keys.length>0&&<FlowiTraceKV label="join keys" value={evidence.join_keys.slice(0,8).join(", ")} wide/>}
      {missing.length>0&&<FlowiTraceKV label="빈칸 보완" value={missing.join(", ")} wide tone="#f97316"/>}
      {warnings.length>0&&<FlowiTraceKV label="warnings" value={warnings.slice(0,4).join(" · ")} wide tone="#fbbf24"/>}
      <FlowiTermResolution rows={termResolution}/>
      {knowledge.length>0&&<FlowiKnowledgeTrace rows={knowledge}/>}
      <FlowiFilterTrace rows={termResolution} filters={evidence.filters}/>
      <FlowiValidationTrace validation={validation}/>
      {subagentChildren.length>0&&<div style={{display:"grid",gap:4,border:"1px solid #262626",borderRadius:6,background:"#151515",padding:"6px 7px"}}>
        <div style={{fontSize:14,color:"#737373",marginBottom:2}}>subagent chain</div>
        {subagentChildren.slice(0,8).map((c,i)=><div key={`${c.name||"child"}-${i}`} style={{display:"grid",gridTemplateColumns:"18px minmax(90px,150px) 72px minmax(0,1fr)",gap:7,alignItems:"baseline",fontSize:14,lineHeight:1.35}}>
          <span style={{width:14,height:14,borderRadius:999,display:"inline-flex",alignItems:"center",justifyContent:"center",fontSize:14,border:`1px solid ${flowiTraceStatusColor(c.status)}99`,color:flowiTraceStatusColor(c.status)}}>{c.status==="done"?"✓":c.status==="error"?"!":i+1}</span>
          <span style={{color:"#d4d4d4",fontWeight:800,whiteSpace:"nowrap",overflow:"hidden",textOverflow:"ellipsis"}} title={c.name||""}>{c.name||"-"}</span>
          <span style={{color:"#a3a3a3"}}>{Number(c.took_ms||0)}ms</span>
          <span style={{color:c.error?"#fca5a5":"#8f8f8f",whiteSpace:"nowrap",overflow:"hidden",textOverflow:"ellipsis"}} title={c.error||c.action||c.intent||""}>{c.error||c.action||c.intent||""}</span>
        </div>)}
      </div>}
      {steps.map((s,i)=><div key={s.key||i} style={{display:"grid",gridTemplateColumns:"18px 118px minmax(0,1fr)",gap:7,alignItems:"baseline",fontSize:14,lineHeight:1.4}}>
        <span style={{width:14,height:14,borderRadius:999,display:"inline-flex",alignItems:"center",justifyContent:"center",fontSize:14,border:`1px solid ${flowiTraceStatusColor(s.status)}99`,color:flowiTraceStatusColor(s.status)}}>{s.status==="done"?"✓":s.status==="blocked"?"!":i+1}</span>
        <span style={{color:"#d4d4d4",fontWeight:800}}>{s.label||s.title||s.key}</span>
        <span style={{color:"#8f8f8f",whiteSpace:"nowrap",overflow:"hidden",textOverflow:"ellipsis"}} title={s.detail||""}>{s.detail||""}</span>
      </div>)}
      {apiCalls.length>0&&<div style={{display:"grid",gap:4}}>
        {apiCalls.slice(0,4).map((c,i)=><div key={i} style={{display:"grid",gridTemplateColumns:"92px minmax(0,1fr) 72px",gap:7,fontSize:14,lineHeight:1.35}}>
          <span style={{color:"#737373"}}>{c.method||c.stage}</span>
          <span style={{color:"#a3a3a3",whiteSpace:"nowrap",overflow:"hidden",textOverflow:"ellipsis"}} title={c.path||c.callee||""}>{c.path||c.callee||"-"}</span>
          <span style={{color:flowiTraceStatusColor(c.status)}}>{c.status||""}</span>
        </div>)}
      </div>}
      {trace.note&&<div style={{marginTop:4,fontSize:14,color:"#737373",lineHeight:1.45}}>{trace.note}</div>}
    </div>
  </details>);
}

function FlowiTermResolution({rows}){
  const items=Array.isArray(rows)?rows.filter(Boolean).slice(0,8):[];
  if(!items.length)return null;
  return <div style={{display:"grid",gap:5,border:"1px solid #262626",borderRadius:6,background:"#151515",padding:"7px 8px"}}>
    <div style={{fontSize:14,color:"#737373"}}>단어 해석</div>
    {items.map((row,i)=>{
      const refs=Array.isArray(row.wiki_refs)?row.wiki_refs.filter(Boolean).slice(0,3).join(", "):"";
      const meta=[row.meaning,row.status].filter(Boolean).join(" · ");
      return <div key={`${row.token||"term"}-${i}`} style={{display:"grid",gridTemplateColumns:"minmax(76px,140px) minmax(0,1fr)",gap:8,alignItems:"baseline",fontSize:14,lineHeight:1.35}}>
        <span style={{color:"#e5e5e5",fontWeight:900,whiteSpace:"nowrap",overflow:"hidden",textOverflow:"ellipsis"}} title={row.token||""}>{row.token||"-"}</span>
        <span style={{color:"#a3a3a3",whiteSpace:"nowrap",overflow:"hidden",textOverflow:"ellipsis"}} title={[meta,refs].filter(Boolean).join(" / ")}>{meta}{refs?` / ${refs}`:""}</span>
      </div>;
    })}
  </div>;
}

function FlowiKnowledgeTrace({rows}){
  const items=Array.isArray(rows)?rows.filter(Boolean).slice(0,8):[];
  if(!items.length)return null;
  return <div style={{display:"grid",gap:5,border:"1px solid #262626",borderRadius:6,background:"#151515",padding:"7px 8px"}}>
    <div style={{display:"flex",gap:7,alignItems:"baseline",justifyContent:"space-between"}}>
      <div style={{fontSize:14,color:"#737373"}}>참고한 Wiki / Schema</div>
      <div style={{fontSize:14,color:"#737373"}}>{items.length} hits</div>
    </div>
    {items.map((row,i)=>{
      const id=String(row.id||row.doc_id||"");
      const title=String(row.title||id||"knowledge");
      const meta=[row.kind,row.term?`term ${row.term}`:"",row.relation_id&&row.column?`${row.relation_id}.${row.column}`:"",row.source].filter(Boolean).join(" · ");
      return <div key={`${id}-${i}`} style={{display:"grid",gridTemplateColumns:"minmax(0,1fr) minmax(90px,160px)",gap:8,alignItems:"baseline",fontSize:14,lineHeight:1.35}}>
        <span style={{color:"#d4d4d4",fontWeight:800,whiteSpace:"nowrap",overflow:"hidden",textOverflow:"ellipsis"}} title={title}>{title}</span>
        <span style={{color:"#8f8f8f",whiteSpace:"nowrap",overflow:"hidden",textOverflow:"ellipsis",textAlign:"right"}} title={meta||id}>{meta||id}</span>
      </div>;
    })}
  </div>;
}

function FlowiFilterTrace({rows,filters}){
  const lines=[];
  for(const row of Array.isArray(rows)?rows:[]){
    if(row?.query_filter)lines.push(`${row.token||"term"}: ${row.query_filter}`);
  }
  const filterKeys=filters&&typeof filters==="object"?Object.entries(filters).filter(([,v])=>v!==undefined&&v!==null&&v!==""&&!(Array.isArray(v)&&!v.length)).slice(0,8):[];
  if(!lines.length&&!filterKeys.length)return null;
  return <div style={{display:"grid",gap:5,border:"1px solid #262626",borderRadius:6,background:"#151515",padding:"7px 8px"}}>
    <div style={{fontSize:14,color:"#737373"}}>실행 필터</div>
    {lines.slice(0,8).map((line,i)=><div key={`filter-${i}`} style={{fontSize:14,color:"#d4d4d4",whiteSpace:"nowrap",overflow:"hidden",textOverflow:"ellipsis"}} title={line}>{line}</div>)}
    {filterKeys.length>0&&<div style={{fontSize:14,color:"#8f8f8f",whiteSpace:"nowrap",overflow:"hidden",textOverflow:"ellipsis"}} title={JSON.stringify(Object.fromEntries(filterKeys))}>
      filters {JSON.stringify(Object.fromEntries(filterKeys))}
    </div>}
  </div>;
}

function FlowiValidationTrace({validation}){
  if(!validation||typeof validation!=="object")return null;
  const warnings=Array.isArray(validation.warnings)?validation.warnings:[];
  const lines=[
    validation.rows!==undefined?`결과 ${validation.rows}건`:"",
    validation.chart_readiness?`chart ${validation.chart_readiness}`:"",
    validation.source_count!==undefined?`근거 ${validation.source_count}건`:"",
    validation.fallback?"fallback 사용":"",
  ].filter(Boolean);
  if(!lines.length&&!warnings.length)return null;
  return <div style={{display:"grid",gap:5,border:"1px solid #262626",borderRadius:6,background:"#151515",padding:"7px 8px"}}>
    <div style={{fontSize:14,color:"#737373"}}>결과 검증</div>
    <div style={{fontSize:14,color:"#d4d4d4"}}>{lines.join(" · ")}</div>
    {warnings.slice(0,4).map((w,i)=><div key={`warn-${i}`} style={{fontSize:14,color:"#fbbf24",whiteSpace:"nowrap",overflow:"hidden",textOverflow:"ellipsis"}} title={w}>{w}</div>)}
  </div>;
}

function FlowiTraceKV({label,value,wide=false,tone="#d4d4d4"}){
  const text=Array.isArray(value)?value.join(", "):String(value??"");
  if(!text)return null;
  return <div style={{gridColumn:wide?"1 / -1":undefined,border:"1px solid #262626",borderRadius:6,background:"#151515",padding:"6px 7px",minWidth:0}}>
    <div style={{fontSize:14,color:"#737373",marginBottom:3}}>{label}</div>
    <div style={{fontSize:14,color:tone,whiteSpace:"nowrap",overflow:"hidden",textOverflow:"ellipsis"}} title={text}>{text}</div>
  </div>;
}

const FLOWI_CHOICE_BTN={textAlign:"left",border:`1px solid ${HOME_UI.accent}`,borderRadius:6,background:"#1f130b",padding:"7px 10px",cursor:"pointer",color:HOME_UI.textSoft,fontSize:14,fontFamily:"'JetBrains Mono',monospace",lineHeight:1.35};

function FlowiChoices({question,choices,onChoice,onNavigate}){
  return(<div style={{marginTop:8}}>
    <div style={{fontSize:14,fontWeight:900,color:"#e5e5e5",fontFamily:"'JetBrains Mono',monospace",marginBottom:7}}>{question||"어떻게 진행할까요?"}</div>
    <div style={{display:"flex",gap:7,flexWrap:"wrap"}}>
      {choices.map((c,i)=><button key={c.id||i} type="button" onClick={()=>{
        const tab=c.tab||c.feature||"";
        if(tab&&typeof onNavigate==="function")onNavigate(tab);
        else if(onChoice)onChoice(c.submit_prompt||c.prompt||c.value||c.title||"",{displayText:c.title||c.value||c.label||"선택"});
      }}
        onMouseEnter={e=>{e.currentTarget.style.background="#3a3a3a";}}
        onMouseLeave={e=>{e.currentTarget.style.background="#2a2a2a";}}
        style={{...FLOWI_CHOICE_BTN,minWidth:150,maxWidth:"100%"}}>
        <span style={{fontWeight:900,color:"#f97316",marginRight:7}}>{c.label||i+1}</span>
        <span style={{fontWeight:900,color:"#e5e5e5"}}>{c.title||c.value}</span>
      </button>)}
    </div>
  </div>);
}

function FlowiWalkthrough({data}){
  const entries=Array.isArray(data.entries)?data.entries:[];
  const remaining=Array.isArray(data.modules_remaining)?data.modules_remaining:[];
  return(<div style={{marginTop:10,border:"1px solid #333",borderRadius:8,background:"#111",padding:"9px 10px"}}>
    <div style={{display:"flex",gap:7,alignItems:"center",flexWrap:"wrap",marginBottom:7}}>
      <span style={{fontSize:14,color:"#f97316",fontWeight:900,fontFamily:"monospace"}}>inform walkthrough</span>
      {data.current_module&&<span style={{fontSize:14,color:"#e5e5e5",fontFamily:"monospace"}}>현재 {data.current_module}</span>}
      <span style={{fontSize:14,color:"#a3a3a3",fontFamily:"monospace"}}>완료 {entries.length} · 남음 {remaining.length}</span>
    </div>
    {entries.length>0&&<div style={{display:"grid",gridTemplateColumns:"repeat(auto-fit,minmax(150px,1fr))",gap:6}}>
      {entries.slice(0,8).map((e,i)=><div key={i} style={{border:"1px solid #2a2a2a",borderRadius:6,padding:"6px 7px",background:"#151515",fontSize:14,lineHeight:1.45}}>
        <div style={{color:"#e5e5e5",fontWeight:800}}>{e.module||"-"}</div>
        <div style={{color:"#a3a3a3",fontFamily:"monospace"}}>{e.split_set||e.reason||"-"}</div>
      </div>)}
    </div>}
  </div>);
}

function FlowiArgumentChoices({data,basePrompt,onChoice}){
  const fields=Array.isArray(data?.fields)?data.fields:[];
  const[free,setFree]=useState({});
  if(!fields.length)return null;
  const submit=(field,value)=>{
    const val=String(value||"").trim();
    if(!val)return;
    const payload=field?`${field}: ${val}`:val;
    if(onChoice)onChoice(payload,{displayText:val});
  };
  return(<div style={{marginTop:12,border:"1px solid #333",borderRadius:8,background:"#151515",padding:"10px 11px"}}>
    <div style={{display:"grid",gap:9}}>
      {fields.map(f=>{
        const choices=Array.isArray(f.choices)?f.choices:[];
        return <div key={f.field} style={{display:"grid",gap:6}}>
          <div style={{fontSize:14,color:"#e5e5e5",fontWeight:900,fontFamily:"'JetBrains Mono',monospace"}}>{f.question||flowiFieldQuestion(f.field)}</div>
          <div style={{display:"flex",gap:7,flexWrap:"wrap"}}>
            {choices.filter(c=>!c.free_input).slice(0,3).map(c=><button key={c.id||c.value} type="button" onClick={()=>submit(f.field,c.value)}
              onMouseEnter={e=>{e.currentTarget.style.background="#3a3a3a";}}
              onMouseLeave={e=>{e.currentTarget.style.background="#2a2a2a";}}
              style={{...FLOWI_CHOICE_BTN,minWidth:112}}>
              <span style={{color:"#f97316",fontWeight:900,marginRight:7}}>{c.label}</span>{c.title||c.value}
            </button>)}
          </div>
          <div style={{fontSize:14,color:"#a3a3a3",fontFamily:"'JetBrains Mono',monospace"}}>{data.message||"또는 직접 입력해 주세요"}</div>
          <div style={{display:"flex",gap:6,minWidth:0,alignItems:"stretch"}}>
            <input value={free[f.field]||""} onChange={e=>setFree(v=>({...v,[f.field]:e.target.value}))} onKeyDown={e=>{if(e.key==="Enter"){if(e.nativeEvent?.isComposing||e.keyCode===229)return;e.preventDefault();submit(f.field,free[f.field]||"");}}} placeholder={f.free_input_label||"직접 입력"} style={{flex:1,minWidth:0,border:"1px solid #333",borderRadius:7,background:"#171717",color:"#e5e5e5",fontSize:14,padding:"8px 10px",fontFamily:"'JetBrains Mono',monospace",boxSizing:"border-box"}}/>
            <button type="button" onClick={()=>submit(f.field,free[f.field]||"")} style={{border:"1px solid #f97316",borderRadius:7,background:"#2a2a2a",color:"#f97316",fontSize:14,fontWeight:900,padding:"8px 12px",cursor:"pointer",fontFamily:"'JetBrains Mono',monospace"}}>보내기</button>
          </div>
        </div>;
      })}
    </div>
  </div>);
}

function FlowiMissingFreetext({fields,basePrompt,onChoice}){
  const items=Array.isArray(fields)?fields.filter(Boolean):[];
  const[values,setValues]=useState({});
  if(!items.length)return null;
  const submit=(item)=>{
    const key=item.key||item.label||"value";
    const val=String(values[key]||"").trim();
    if(!val)return;
    const label=String(item.label||key||"내용").trim();
    const payload=`${label}: ${val}`;
    if(onChoice)onChoice(payload,{displayText:payload});
  };
  return(<div style={{marginTop:12,border:"1px solid #333",borderRadius:8,background:"#151515",padding:"10px 11px"}}>
    <div style={{display:"grid",gap:9}}>
      {items.map(item=>{
        const key=item.key||item.label||"value";
        return <div key={key} style={{display:"grid",gap:6}}>
          <label style={{fontSize:14,color:"#e5e5e5",fontWeight:900,fontFamily:"'JetBrains Mono',monospace"}}>{item.label||flowiFieldQuestion(key)}</label>
          <div style={{display:"flex",gap:6,minWidth:0,alignItems:"stretch"}}>
            <input value={values[key]||""}
              onChange={e=>setValues(v=>({...v,[key]:e.target.value}))}
              onKeyDown={e=>{if(e.key==="Enter"){if(e.nativeEvent?.isComposing||e.keyCode===229)return;e.preventDefault();submit(item);}else if(e.key==="Escape"){e.preventDefault();setValues(v=>({...v,[key]:""}));}}}
              placeholder={item.placeholder||"내용을 입력해 주세요"}
              autoFocus={items.length===1}
              style={{flex:1,minWidth:0,border:"1px solid #333",borderRadius:7,background:"#171717",color:"#e5e5e5",fontSize:14,padding:"8px 10px",fontFamily:"'JetBrains Mono',monospace",boxSizing:"border-box"}}/>
            <button type="button" onClick={()=>submit(item)} style={{border:"1px solid #f97316",borderRadius:7,background:"#2a2a2a",color:"#f97316",fontSize:14,fontWeight:900,padding:"8px 12px",cursor:"pointer",fontFamily:"'JetBrains Mono',monospace"}}>보내기</button>
          </div>
        </div>;
      })}
    </div>
  </div>);
}

function flowiFieldQuestion(field){
  const map={product:"어느 제품인가요?",module:"어느 모듈인가요?",root_lot_ids:"어느 Root Lot인가요?",root_lot_id:"어느 Root Lot인가요?",lot_ids:"어느 Lot인가요?",fab_lot_ids:"어느 Fab Lot인가요?",root_lot_id_or_fab_lot_id:"어느 Lot인가요?",step:"어느 Step인가요?",metric:"어느 항목인가요?",metrics_or_items:"어느 항목인가요?",knob_value:"어떤 KNOB 값인가요?",source_type:"어느 Source인가요?",split_set:"SplitTable은 어떤 Split으로 진행할까요?",wafer_ids:"어느 Wafer인가요?"};
  return map[field]||`${field} 값을 알려주세요.`;
}

function FlowiNextActions({actions,onNavigate,onChoice}){
  return(<div style={{marginTop:10,border:"1px solid #2a2a2a",borderRadius:8,background:"#111",padding:"8px 9px"}}>
    <div style={{fontSize:14,fontWeight:900,color:"#a3a3a3",fontFamily:"'JetBrains Mono',monospace",marginBottom:6}}>후속 작업</div>
    <div style={{display:"flex",gap:6,flexWrap:"wrap"}}>
      {actions.map((a,i)=>{
        const clickable=(a.type==="open_tab"&&a.tab&&typeof onNavigate==="function")||(a.prompt&&typeof onChoice==="function");
        const click=()=>{if(a.type==="open_tab"&&a.tab&&onNavigate)onNavigate(a.tab);else if(a.prompt&&onChoice)onChoice(a.prompt,{displayText:a.title||a.label||"선택"});};
        return <button key={a.id||i} type="button" onClick={click} disabled={!clickable} title={a.description||""}
          style={{fontSize:14,color:clickable?"#f97316":"#a3a3a3",fontFamily:"monospace",border:"1px solid "+(clickable?"#7c2d12":"#333"),borderRadius:999,padding:"3px 8px",background:clickable?"#1f130b":"#171717",cursor:clickable?"pointer":"default",opacity:clickable?1:.82}}>
          {a.title||a.type}
        </button>;
      })}
    </div>
  </div>);
}

function FlowiEdmProposal({result}){
  const proposal=result.proposal||{};
  const confirm=result.confirm||"";
  const[busy,setBusy]=useState(false);
  const[execResult,setExecResult]=useState(null);
  const[err,setErr]=useState("");
  const run=()=>{
    if(!proposal.action_id||!confirm||busy)return;
    if(!window.confirm(`${proposal.summary||proposal.action_type}\n\nEDM 작업을 실행할까요?`))return;
    setBusy(true);setErr("");
    postJson("/api/llm/flowi/edm/execute",{proposal_id:proposal.action_id,confirm})
      .then(setExecResult)
      .catch(e=>setErr(e.message||String(e)))
      .finally(()=>setBusy(false));
  };
  return(<div style={{marginTop:10,border:"1px solid #7c2d12",borderRadius:8,background:"#1f130b",padding:"9px 10px"}}>
    <div style={{display:"flex",gap:8,alignItems:"center",flexWrap:"wrap"}}>
      <b style={{fontSize:14,color:"#fb923c",fontFamily:"monospace"}}>EDM proposal</b>
      <span style={{fontSize:14,color:"#e5e5e5",fontFamily:"monospace"}}>{proposal.action_type}</span>
      {proposal.file&&<span style={{fontSize:14,color:"#a3a3a3",fontFamily:"monospace"}}>{proposal.file}</span>}
      <button onClick={run} disabled={busy||!!execResult?.ok} style={{marginLeft:"auto",padding:"4px 10px",borderRadius:5,border:"none",background:execResult?.ok?"#64748b":"#f97316",color:"#fff",fontSize:14,fontWeight:800,cursor:busy||execResult?.ok?"default":"pointer"}}>{busy?"실행중":execResult?.ok?"실행됨":"확인 실행"}</button>
    </div>
    <div style={{fontSize:14,color:"#d4d4d4",marginTop:6,lineHeight:1.5}}>{proposal.summary}</div>
    <div style={{fontSize:12,color:"#a3a3a3",marginTop:5,fontFamily:"monospace"}}>confirm {confirm}</div>
    {err&&<div style={{fontSize:14,color:"#fca5a5",marginTop:6}}>{err}</div>}
    {execResult&&<pre style={{margin:"8px 0 0",maxHeight:160,overflow:"auto",fontSize:12,color:"#d4d4d4",whiteSpace:"pre-wrap"}}>{JSON.stringify(execResult.result||execResult,null,2)}</pre>}
  </div>);
}

function FlowiChartPlan({chart}){
  const metrics=Array.isArray(chart.metrics)?chart.metrics:[];
  const ops=Array.isArray(chart.operations)?chart.operations:[];
  const requires=Array.isArray(chart.requires)?chart.requires:[];
  return(<div style={{marginTop:10,border:"1px solid #333",borderRadius:8,background:"#101827",padding:"9px 10px"}}>
    <div style={{display:"flex",justifyContent:"space-between",gap:8,alignItems:"center",marginBottom:7}}>
      <div style={{fontSize:14,fontWeight:900,color:"#dbeafe",fontFamily:"'JetBrains Mono',monospace"}}>Dashboard chart plan</div>
      <span style={{fontSize:14,color:requires.length?"#f97316":"#22c55e",fontFamily:"monospace"}}>{requires.length?"needs confirmation":"ready to route"}</span>
    </div>
    <div style={{display:"grid",gridTemplateColumns:"repeat(auto-fit,minmax(150px,1fr))",gap:6,fontSize:14,color:"#bfdbfe",fontFamily:"monospace"}}>
      <div>kind: {chart.kind||"scatter"}</div>
      <div>source: {(chart.sources||[]).join(", ")||"-"}</div>
      <div>ops: {ops.join(", ")||"-"}</div>
      <div>join: {chart.join_key||"lot_wf"}</div>
      <div>INLINE: {chart.aggregations?.INLINE||"avg"}</div>
      <div>ET: {chart.aggregations?.ET||"median"}</div>
    </div>
    {metrics.length>0&&<div style={{marginTop:7,display:"flex",gap:5,flexWrap:"wrap"}}>
      {metrics.slice(0,10).map(m=><span key={m.metric} style={{fontSize:14,color:"#dbeafe",background:"#1e3a8a66",border:"1px solid #3b82f666",borderRadius:999,padding:"2px 7px"}}>{m.metric}</span>)}
    </div>}
  </div>);
}

// 제목은 카드 머리글이 맡는다 — plotly 안쪽 제목까지 켜면 같은 글이 두 번 나온다.
function flowiChartCfg(data,extra={}){
  return {...(data.chart_config||data.config||data.config_overrides||data),hide_title:true,emphasize_axes:true,...extra};
}

// flow-i 차트 카드 공통 껍데기 — 제목/요약 줄 + 흰 카드. 안쪽 그림은 전부
// FlowPlotlyChart 가 그린다(ChartBuilder 와 같은 렌더러·같은 비율 규칙).
function FlowiChartCard({title,note,children,chips=[]}){
  return(<div style={{marginTop:10,border:"1px solid #d1d5db",borderRadius:8,background:"#ffffff",padding:"10px 12px",color:"#111827",minWidth:0}}>
    {(title||note)&&<div style={{display:"flex",alignItems:"baseline",justifyContent:"space-between",gap:8,marginBottom:6,flexWrap:"wrap"}}>
      <div style={{fontSize:14,fontWeight:900,color:"#111827",minWidth:0,flex:"1 1 320px",overflowWrap:"anywhere"}}>{title}</div>
      {note&&<div style={{fontSize:14,color:"#475569",fontFamily:"monospace",minWidth:0,flex:"1 1 220px",textAlign:"right",overflowWrap:"anywhere"}}>{note}</div>}
    </div>}
    {children}
    {chips.filter(Boolean).length>0&&<div style={{marginTop:7,display:"flex",gap:5,flexWrap:"wrap",fontSize:14,color:"#475569",fontFamily:"monospace"}}>
      {chips.filter(Boolean).map((chip,i)=><span key={i} style={{border:"1px solid #cbd5e1",borderRadius:999,padding:"2px 7px"}}>{chip}</span>)}
    </div>}
  </div>);
}

function FlowiChartEmpty({label}){
  return <div style={{marginTop:10,padding:"9px 10px",border:"1px solid #333",borderRadius:8,background:"#141414",fontSize:14,color:"#a3a3a3"}}>차트로 표시할 {label}가 없습니다.</div>;
}

function FlowiScatterResult({data}){
  const plotlyType=String(data?.chart_type||data?.chart_config?.chart_type||data?.config?.chart_type||data?.kind||"").replace("dashboard_","");
  if(data?.kind==="dashboard_wip_split"&&Array.isArray(data.bins))return <FlowiWipDashboardResult data={data}/>;
  if(["pie","donut","bar"].includes(plotlyType)&&Array.isArray(data.groups)&&data.groups.length)return <FlowiChartCard
    title={data.title||"Flowi chart"} note={`groups=${data.total||data.groups.length}`}>
    <FlowPlotlyChart chart={data} cfg={flowiChartCfg(data)} dark={false} />
  </FlowiChartCard>;
  if(Array.isArray(data.series)&&data.series.length)return <FlowiLineResult data={data}/>;
  if(Array.isArray(data.groups)&&data.groups.length)return <FlowiGroupBarResult data={data}/>;
  if(Array.isArray(data.boxes)&&data.boxes.length)return <FlowiBoxResult data={data}/>;
  if((data.kind==="dashboard_wafer_map"||plotlyType==="wafer_map")&&Array.isArray(data.points))return <FlowiWaferMapResult data={data}/>;
  if(!Array.isArray(data.points)||!data.points.length)return <FlowiChartEmpty label="point"/>;
  const fit=data.fit&&Number.isFinite(Number(data.fit.slope))?data.fit:null;
  return <FlowiChartCard
    title={data.title||"Flowi scatter"}
    note={`n=${data.total||data.points.length} · corr=${data.corr??"-"}${fit?` · R²=${fit.r2}`:""}`}
    chips={[
      `join ${Array.isArray(data.join_cols)?data.join_cols.join("+"):"lot_wf"} · ${data.join_how||"left"}`,
      data.aggregations?.INLINE&&`INLINE ${data.aggregations.INLINE}`,
      data.aggregations?.ET&&`ET ${data.aggregations.ET}`,
      data.color_by&&`color ${data.color_by}`,
    ]}>
    <FlowPlotlyChart chart={data} cfg={flowiChartCfg(data)} dark={false} />
  </FlowiChartCard>;
}

function FlowiWipDashboardResult({data}){
  const bins=Array.isArray(data?.bins)?data.bins:[];
  const splitValues=Array.isArray(data?.split_values)?data.split_values:[];
  const total=Number(data?.total_wafers||0);
  return <FlowiChartCard
    title={data?.title||"WIP Dashboard"}
    note={`${total.toLocaleString()} wafers · ${bins.length.toLocaleString()} bins`}
    chips={[
      data?.product&&`product ${data.product}`,
      data?.split_col&&`split ${data.split_col}`,
      data?.axis&&`axis ${data.axis}`,
    ]}>
    {bins.length&&splitValues.length
      ?<WipStackedBar
        bins={bins}
        splitValues={splitValues}
        height={380}
        dark={false}
        unassignedLabel={data?.unassigned_label||"(unassigned)"}
        norm="count"
        axis={data?.axis||"step_id"}
      />
      :<div style={{padding:"28px 12px",textAlign:"center",color:"#64748b",fontSize:13}}>표시할 WIP 데이터가 없습니다.</div>}
  </FlowiChartCard>;
}

function FlowiBoxResult({data}){
  const[showStats,setShowStats]=useState(true);
  const[geometry,setGeometry]=useState(null);
  const boxes=(Array.isArray(data.boxes)?data.boxes:[]).filter(b=>["q1","median","q3"].every(k=>Number.isFinite(Number(b[k]))));
  if(!boxes.length)return <FlowiChartEmpty label="box 값"/>;
  const valueLabel=data.y_label||data.metric||"value";
  // flow-i 는 원 측정값 대신 5수 요약을 내려준다 — 그림도 표도 그 값에서 나온다.
  const statsBoxes=boxes.map((b,i)=>({key:`${b.label||i}`,label:String(b.label||"-"),stats:boxStatsFromSummary(b)}));
  return <FlowiChartCard
    title={data.title||"Flowi box plot"}
    note={`groups=${data.total||boxes.length} · ${data.metric||""}`}
    chips={[`median / IQR`,data.x_label||"group",valueLabel]}>
    <label style={{display:"inline-flex",alignItems:"center",gap:6,fontSize:12,fontWeight:800,color:"#334155",cursor:"pointer",marginBottom:4}}>
      <input type="checkbox" checked={showStats} onChange={e=>setShowStats(e.target.checked)}/>통계표 표시
    </label>
    <FlowPlotlyChart chart={{...data,chart_type:"box",boxes,y_label:valueLabel}} cfg={flowiChartCfg(data,{chart_type:"box",hide_x_ticks:showStats&&boxStatsAlignment(geometry,boxes.length).aligned})} dark={false} onGeometry={setGeometry} />
    {showStats&&<BoxStatsTable boxes={statsBoxes} valueLabel={valueLabel} geometry={geometry}/>}
  </FlowiChartCard>;
}

function FlowiWaferMapResult({data}){
  const pts=(Array.isArray(data.points)?data.points:[]).filter(p=>Number.isFinite(Number(p.x))&&Number.isFinite(Number(p.y))&&Number.isFinite(Number(p.value))).slice(0,900);
  if(!pts.length)return <div style={{marginTop:10,padding:"9px 10px",border:"1px solid #333",borderRadius:8,background:"#141414",fontSize:14,color:"#a3a3a3"}}>차트로 표시할 WF map point가 없습니다.</div>;
  const vehicle=data.product||data.vehicle||data.config?.product||data.chart_config?.product||"";
  return <div style={{marginTop:10}}><TegValueWaferMap vehicle={vehicle} points={pts} title={data.title||"Flowi WF MAP"} valueLabel={data.value_label||data.metric||"value"}/></div>;
}

function FlowiLineResult({data}){
  const series=(Array.isArray(data.series)?data.series:[]).map(s=>({...s,points:(Array.isArray(s.points)?s.points:[]).filter(p=>Number.isFinite(Number(p.y)))})).filter(s=>s.points.length);
  if(!series.length)return <FlowiChartEmpty label="trend point"/>;
  const metric=data.metric||data.y_label||"value";
  // 여러 계열은 color_value 로 묶어 넘긴다 — FlowPlotlyChart 가 계열마다 trace 를 만든다.
  const points=series.flatMap(s=>s.points.map(p=>({...p,x_label:p.x_label??p.bucket??p.x,y:Number(p.y),color_value:s.name||metric})));
  const chart={
    chart_type:"line",
    x_label:data.x_label||"x",
    y_label:data.y_label||metric,
    color_by:series.length>1?(data.series_label||"series"):"",
    trend_grain:"bucket",
    points,
  };
  return <FlowiChartCard
    title={data.title||"Flowi trend"}
    note={`points=${data.total||points.length} · ${data.metric||""}`}
    chips={[data.color_by&&`color ${data.color_by}`,data.basis_label||"bucket trend"]}>
    <FlowPlotlyChart chart={chart} cfg={flowiChartCfg(data,{chart_type:"line",color_by:chart.color_by,trend_grain:"bucket"})} dark={false} />
  </FlowiChartCard>;
}

function FlowiGroupBarResult({data}){
  const groups=(Array.isArray(data.groups)?data.groups:[]).map(g=>({...g,label:String(g.label||"-"),value:Number(g.value??g.median??g.mean),count:Number(g.metric_n??g.wafer_groups??g.n??0)})).filter(g=>Number.isFinite(g.value)).slice(0,40);
  if(!groups.length)return <FlowiChartEmpty label="group 값"/>;
  const chart={chart_type:"bar_horizontal",x_label:Array.isArray(data.group_by)?data.group_by.join("+"):(data.x_label||"group"),y_label:data.y_label||data.metric||"value",groups};
  return <FlowiChartCard
    title={data.title||"Flowi group chart"}
    note={`groups=${data.total||groups.length} · ${data.metric||""}`}
    chips={[`group ${Array.isArray(data.group_by)?data.group_by.join("+"):"-"}`,"median",`join ${Array.isArray(data.join_cols)?data.join_cols.join("+"):"root_lot_id+wafer_id"}`]}>
    <FlowPlotlyChart chart={chart} cfg={flowiChartCfg(data,{chart_type:"bar_horizontal"})} dark={false} />
  </FlowiChartCard>;
}

const FLOWI_FEEDBACK_TAGS=[
  ["correct","정확함"],
  ["explanation_gap","설명 부족"],
  ["wrong_data_source","잘못된 DB/컬럼"],
  ["wrong_workflow","workflow 다름"],
  ["missed_clarification","질문 필요"],
  ["too_slow","느림"],
  ["permission_risk","권한 우려"],
  ["output_issue","출력 문제"],
  ["hallucination","없는 값"],
  ["key_matching_error","key 매칭"],
  ["aggregation_error","집계 오류"],
];
const FLOWI_USER_FEEDBACK_KEYS=new Set(["correct","explanation_gap","missed_clarification","too_slow","output_issue","hallucination"]);
function FlowiFeedback({result,tool,prompt,isAdmin=false}){
  const[rating,setRating]=useState("");
  const[msg,setMsg]=useState("");
  const[busy,setBusy]=useState(false);
  const send=(nextRating)=>{
    if(busy)return;
    const r=nextRating==="down"?"down":"up";
    setRating(r);setMsg("");setBusy(true);
    postJson("/api/llm/flowi/feedback",{
      rating:r,
      prompt:prompt||"",
      answer:result?.answer||"",
      run_id:result?.run_id||"",
      intent:tool?.intent||"",
      note:"",
      tags:r==="up"?["correct"]:["output_issue"],
      expected_workflow:"",
      correct_route:"",
      data_refs:"",
      golden_candidate:false,
      tool:tool||{},
      llm:result?.llm||{},
      elapsed_ms:result?.elapsed_ms||null,
    }).then(()=>setMsg("저장됨")).catch(e=>setMsg(e.message||"저장 실패")).finally(()=>setBusy(false));
  };
  const button=(value,label)=>{
    const on=rating===value;
    const color=value==="up"?"#22c55e":"#ef4444";
    return <button type="button" disabled={busy} onClick={()=>send(value)} aria-label={label} title={label}
      style={{width:30,height:28,borderRadius:6,border:`1px solid ${on?color:"#333"}`,background:on?`${color}22`:"transparent",color:on?color:"#a3a3a3",fontSize:15,cursor:busy?"wait":"pointer"}}>{value==="up"?"👍":"👎"}</button>;
  };
  return <div style={{marginTop:8,display:"flex",alignItems:"center",gap:6}}>
    {button("up","좋아요")}{button("down","싫어요")}
    {msg&&<span style={{fontSize:12,color:msg.includes("실패")?"#fca5a5":"#737373",fontFamily:"monospace"}}>{msg}</span>}
  </div>;
}

function FlowiDataTable({table}){
  const cols=flowiTableColumns(table);
  const rows=Array.isArray(table.rows)?table.rows:[];
  const maxHeight=Number(table.max_height||table.maxHeight||320);
  const cellStyle=(row,c)=>{
    const key=String(c.key||"");
    const isSplit=/^(KNOB|MASK|FAB)_/i.test(key)||["parameter","actual","plan","status"].includes(key);
    const highlighted=!!(row.__highlight||row._highlight||row.highlight||table.highlight);
    return {
      padding:"6px 8px",
      borderBottom:"1px solid #262626",
      color:isSplit?"#e5e5e5":"#c7c7c7",
      whiteSpace:"nowrap",
      fontWeight:isSplit?800:500,
      background:highlighted?"rgba(127,29,29,0.18)":"transparent",
      boxShadow:highlighted&&["actual","plan","status"].includes(key)?"inset 0 0 0 2px rgba(239,68,68,0.85)":"none",
    };
  };
  return(<div style={{marginTop:10,border:"1px solid #333",borderRadius:8,overflow:"hidden",background:"#121212"}}>
    <div style={{display:"flex",justifyContent:"space-between",alignItems:"center",gap:8,padding:"8px 10px",borderBottom:"1px solid #2a2a2a",background:"#171717"}}>
      <div style={{fontSize:14,fontWeight:800,color:"#e5e5e5",fontFamily:"'JetBrains Mono',monospace"}}>{table.title||"Flowi table"}</div>
      <div style={{fontSize:14,color:"#737373",fontFamily:"monospace"}}>{rows.length}{table.total&&table.total!==rows.length?` / ${table.total}`:""} rows</div>
    </div>
    <div style={{overflow:"auto",maxHeight}}>
      <table style={{width:"100%",borderCollapse:"collapse",fontSize:14,fontFamily:"monospace"}}>
        <thead><tr>{cols.map(c=><th key={c.key} style={{position:"sticky",top:0,zIndex:1,textAlign:"left",padding:"7px 8px",borderBottom:"1px solid #333",background:"#1f1f1f",color:"#a3a3a3",whiteSpace:"nowrap"}}>{c.label||c.key}</th>)}</tr></thead>
        <tbody>{rows.map((r,i)=><tr key={i}>
          {cols.map(c=><td key={c.key} style={cellStyle(r,c)}>{r[c.key]??""}</td>)}
        </tr>)}</tbody>
      </table>
    </div>
  </div>);
}

export default function My_Home({onNavigate,user,visibleTabs}){
  const nav=(k)=>onNavigate&&onNavigate(k);
  const isAdmin=isAdminUser(user);
  const[flowiActive,setFlowiActive]=useState(false);

  // 카드 목록은 App 이 넘겨준 visibleTabs(= nav 에 뜨는 탭) 그대로다. 아이콘·이름은
  // config.js TABS 가 원천이라 새 탭을 등록하면 홈 카드도 같이 늘어난다 — 예전처럼
  // 홈에만 있던 별도 목록이 뒤처져 랏 관리·차트생성·랏 요청 카드가 빠지는 일이 없다.
  // desc 만 여기서 붙인다 (없으면 아이콘+이름만 있는 카드).
  const cards=(Array.isArray(visibleTabs)?visibleTabs:visibleTabsFor(user,isAdmin?"__all__":(user?.tabs||"")));
  const visibleCards=cards.map(t=>({key:t.key,icon:t.icon,title:t.label,desc:CARD_DESC[t.key]||"",tag:t.tag}));

  return(<div style={{minHeight:"calc(100vh - 52px)",width:"100%",boxSizing:"border-box",padding:flowiActive?"20px 12px 96px":"32px 32px 96px",background:"var(--bg-primary,#1a1a1a)",color:"var(--text-primary,#e5e5e5)",fontFamily:"var(--font-sans)",maxWidth:flowiActive?"min(1760px, calc(100vw - 24px))":1040,margin:"0 auto",transition:"max-width .24s ease,padding .24s ease"}}>
    {/* v8.3.3: Home brand logo — shared BrandLogo.jsx, size="home" retains .home-brand-logo marker. */}
    <BrandLogo size="home"/>
    {/* Terminal header */}
    <div style={{background:"#111",borderRadius:12,border:"1px solid #333",overflow:"hidden",marginBottom:28,boxShadow:"0 2px 20px rgba(0,0,0,0.4)"}}>
      <div style={{display:"flex",alignItems:"center",gap:8,padding:"8px 14px",background:"#1a1a1a",borderBottom:"1px solid #333"}}>
        <div style={{display:"flex",gap:6}}><div style={{width:10,height:10,borderRadius:"50%",background:"#ef4444"}}/><div style={{width:10,height:10,borderRadius:"50%",background:"#fbbf24"}}/><div style={{width:10,height:10,borderRadius:"50%",background:"#22c55e"}}/></div>
        <span style={{fontSize:14,color:"#525252",fontFamily:"monospace",marginLeft:6}}>flow-i console</span>
      </div>
      <div style={{display:"flex",gap:flowiActive?16:20,padding:flowiActive?"16px 18px":"20px 24px",alignItems:"flex-start"}}>
        <div style={{display:"flex",flexDirection:"column",alignItems:"center",gap:4,flexShrink:0}}><Holli size={flowiActive?60:72}/><span style={{fontSize:14,color:"#f97316",fontFamily:"monospace",letterSpacing:"0.12em",fontWeight:700}}>flow-i</span></div>
        <div style={{flex:"1 1 auto",minWidth:0,paddingTop:4}}>
          <div style={{marginTop:6,fontFamily:"'JetBrains Mono',monospace",fontSize:14}}><span style={{color:"#f97316"}}>{">"}</span><span style={{color:"#737373"}}> </span><WelcomeType name={user?.username||"user"}/></div>
          <FlowiConsole onNavigate={nav} user={user} onActiveChange={setFlowiActive}/>
        </div>
      </div>
    </div>

    {/* Permission-filtered cards, centered */}
    {visibleCards.length>0?<div className="home-feature-grid">
      {visibleCards.map(c=><Card key={c.key} icon={c.icon} title={c.title} desc={c.desc} tag={c.tag} onClick={()=>nav(c.key)} width="100%"/>)}
    </div>:<div style={{padding:"40px 20px",textAlign:"center",color:"var(--text-secondary)",fontSize:14,marginBottom:32}}>
      사용 가능한 탭이 없습니다. 관리자에게 권한을 요청해주세요.
    </div>}

    <div style={{background:"var(--bg-secondary,#262626)",borderRadius:12,border:"1px solid var(--border,#333)",overflow:"hidden"}}>
      <PageHeader title="사용 방법" subtitle="권한있는 기능 가이드" style={{fontFamily:"'JetBrains Mono',monospace"}} />
      <div style={{padding:"6px 20px 16px"}}>
        {visibleCards.filter(c=>FEATURE_GUIDES[c.key]).map((c,i,arr)=>{const g=FEATURE_GUIDES[c.key];return(<div key={c.key} style={{paddingTop:16,paddingBottom:12,borderBottom:i<arr.length-1?"1px solid var(--border,#333)":"none"}}>
          <div style={{display:"flex",alignItems:"center",gap:10,marginBottom:10,cursor:"pointer"}} onClick={()=>nav(c.key)}>
            <span style={{fontSize:24}}>{g.icon}</span>
            <span style={{fontSize:14,fontWeight:700,color:"var(--text-primary)",fontFamily:"'JetBrains Mono',monospace"}}>{g.title}</span>
            <span style={{fontSize:14,color:"var(--accent)",fontFamily:"monospace",marginLeft:"auto"}}>→ 열기</span>
          </div>
          <ol style={{margin:0,paddingLeft:28,fontSize:14,lineHeight:1.8,color:"var(--text-secondary)"}}>
            {g.steps.map((s,si)=><li key={si} style={{marginBottom:2}}>{s}</li>)}
          </ol>
        </div>);})}
        {visibleCards.filter(c=>FEATURE_GUIDES[c.key]).length===0&&<div style={{padding:"20px 0",textAlign:"center",color:"var(--text-secondary)",fontSize:14}}>권한있는 기능이 없습니다. 아래 관리자 문의 버튼으로 문의해주세요.</div>}
      </div>
    </div>

    {/* v8.3.1: Contact 섹션 — 메시지 탭/팝업 대체.
         v8.4.5: Contact 는 우상단 ✉ 버튼(ContactButton)으로 이관 — 홈 하단 섹션 제거. */}
  </div>);
}

// ─── Contact section (replaces nav Messages tab + unread popup) ────────────────
function fmtT(iso){if(!iso)return"";try{const d=new Date(iso);const mm=String(d.getMonth()+1).padStart(2,"0");const dd=String(d.getDate()).padStart(2,"0");const H=String(d.getHours()).padStart(2,"0");const M=String(d.getMinutes()).padStart(2,"0");return `${mm}-${dd} ${H}:${M}`;}catch{return(iso||"").slice(0,16).replace("T"," ");}}
const SEC_WRAP={marginTop:40,background:"var(--bg-secondary,#262626)",borderRadius:12,border:"1px solid var(--border,#333)",overflow:"hidden"};
const SEC_HEADER={padding:"14px 20px",borderBottom:"1px solid var(--border,#333)",display:"flex",justifyContent:"space-between",alignItems:"center"};
const SEC_TITLE={fontSize:14,fontWeight:700,fontFamily:"'JetBrains Mono',monospace",color:"var(--accent,#f97316)"};

function ContactSection({user}){
  const isAdmin=user?.role==="admin";
  return(<section data-testid="home-contact-section" id="home-contact-section" style={SEC_WRAP}>
    <div style={SEC_HEADER}>
      <span style={SEC_TITLE}>{"> contact"}</span>
      <span style={{fontSize:14,color:"var(--text-secondary)"}}>{isAdmin?"관리자 — 1:1 문의함 + 전체 공지":"관리자에게 문의 보내기"}</span>
    </div>
    {isAdmin?<AdminContact user={user}/>:<UserContact user={user}/>}
  </section>);
}

// ── User side: inline 1:1 inquiry + collapsible history ──
function UserContact({user}){
  const uname=user?.username||"";
  const[thread,setThread]=useState({messages:[]});const[text,setText]=useState("");
  const[sending,setSending]=useState(false);const[showHistory,setShowHistory]=useState(false);
  const[notices,setNotices]=useState([]);
  const load=()=>{
    sf("/api/messages/thread?username="+encodeURIComponent(uname))
      .then(d=>{setThread(d||{messages:[]});postJson("/api/messages/mark_read",{username:uname}).catch(()=>{});})
      .catch(()=>{});
    sf("/api/messages/notices?username="+encodeURIComponent(uname))
      .then(d=>setNotices(d.notices||[])).catch(()=>{});
  };
  useEffect(()=>{if(uname)load();},[uname]);
  const send=()=>{
    const v=(text||"").trim();if(!v||sending)return;
    if(v.length>5000){toast.warn("최대 5000자까지 입력 가능합니다.");return;}
    setSending(true);
    postJson("/api/messages/send",{username:uname,text:v})
      .then(()=>{setText("");load();}).catch(e=>toast.error("전송 실패: "+(e.message||e))).finally(()=>setSending(false));
  };
  const markNoticeRead=(id)=>{
    postJson("/api/messages/notice_read",{username:uname,ids:[id]})
      .then(()=>setNotices(p=>p.map(x=>x.id===id?{...x,read:true}:x))).catch(()=>{});
  };
  const msgs=thread.messages||[];
  const unreadNotices=notices.filter(n=>!n.read);
  return(<div data-testid="contact-user" style={{padding:"16px 20px"}}>
    {/* 최신 공지 pinned to top */}
    {unreadNotices.length>0&&<div style={{marginBottom:16}}>
      <div style={{fontSize:14,color:"var(--accent)",fontFamily:"monospace",marginBottom:6,fontWeight:700}}>📢 새 공지사항 ({unreadNotices.length})</div>
      {unreadNotices.slice(0,3).map(n=>(
        <div key={n.id} onClick={()=>markNoticeRead(n.id)} style={{padding:"10px 12px",borderRadius:6,background:"var(--accent-glow,rgba(249,115,22,0.1))",border:"1px solid var(--border)",marginBottom:6,cursor:"pointer"}}>
          <div style={{fontSize:14,fontWeight:700,color:"var(--text-primary)"}}>{n.title||"(제목 없음)"}</div>
          {n.body&&<div style={{fontSize:14,color:"var(--text-secondary)",marginTop:3,whiteSpace:"pre-wrap",lineHeight:1.5}}>{n.body}</div>}
          <div style={{fontSize:14,color:"var(--text-secondary)",fontFamily:"monospace",marginTop:4}}>{n.author} · {fmtT(n.created_at)}</div>
        </div>))}
    </div>}

    {/* Send-to-admin input */}
    <div style={{fontSize:14,color:"var(--text-secondary)",marginBottom:6,fontFamily:"monospace"}}>💬 관리자에게 문의</div>
    <div style={{display:"flex",gap:8,alignItems:"flex-end"}}>
      <textarea data-testid="contact-user-input" value={text} onChange={e=>setText(e.target.value)} disabled={sending}
        onKeyDown={e=>{if((e.metaKey||e.ctrlKey)&&e.key==="Enter"){if(e.nativeEvent?.isComposing||e.keyCode===229)return;send();}}}
        placeholder="버그 리포트 / 기능 요청 / 권한 요청 등 (Cmd/Ctrl + Enter 전송)" rows={3}
        style={{flex:1,padding:"8px 10px",borderRadius:6,border:"1px solid var(--border)",background:"var(--bg-primary)",color:"var(--text-primary)",fontSize:14,fontFamily:"'Pretendard',sans-serif",resize:"vertical",outline:"none"}}/>
      <button data-testid="contact-user-send" onClick={send} disabled={sending||!text.trim()}
        style={{padding:"8px 18px",borderRadius:6,border:"none",background:sending||!text.trim()?"#94a3b8":"var(--accent)",color:"#fff",fontSize:14,fontWeight:700,cursor:sending||!text.trim()?"default":"pointer",flexShrink:0,alignSelf:"stretch"}}>
        {sending?"…":"보내기"}
      </button>
    </div>
    <div style={{fontSize:14,color:"var(--text-secondary)",marginTop:4,textAlign:"right"}}>{text.length} / 5000</div>

    {/* Collapsible history */}
    <div style={{marginTop:18,borderTop:"1px solid var(--border)",paddingTop:10}}>
      <div onClick={()=>setShowHistory(!showHistory)} style={{cursor:"pointer",fontSize:14,color:"var(--text-secondary)",fontFamily:"monospace",display:"flex",alignItems:"center",gap:6}}>
        <span>{showHistory?"▼":"▶"}</span><span>과거 대화 ({msgs.length})</span>
      </div>
      {showHistory&&<div data-testid="contact-user-history" style={{marginTop:10,maxHeight:300,overflowY:"auto",padding:"4px 2px"}}>
        {msgs.length===0&&<div style={{textAlign:"center",color:"var(--text-secondary)",fontSize:14,padding:20}}>아직 대화가 없습니다.</div>}
        {msgs.map(m=>{const mine=m.from===uname;return(
          <div key={m.id} style={{display:"flex",justifyContent:mine?"flex-end":"flex-start",marginBottom:8}}>
            <div style={{maxWidth:"78%",display:"flex",flexDirection:"column",alignItems:mine?"flex-end":"flex-start"}}>
              <div style={{fontSize:14,color:"var(--text-secondary)",fontFamily:"monospace",marginBottom:2,padding:"0 4px"}}>{mine?"나":m.from} · {fmtT(m.created_at)}</div>
              <div style={{padding:"6px 10px",borderRadius:10,background:mine?"var(--accent)":"var(--bg-card)",color:mine?"#fff":"var(--text-primary)",fontSize:14,lineHeight:1.5,whiteSpace:"pre-wrap",wordBreak:"break-word",border:mine?"none":"1px solid var(--border)"}}>{m.text}</div>
            </div>
          </div>);})}
      </div>}
    </div>
  </div>);
}

// ── Admin side: two tabs only — [📨 1:1 문의함] [📢 전체 공지].
function AdminContact({user}){
  const[sub,setSub]=useState("inbox");
  const tS=(a)=>({padding:"7px 14px",fontSize:14,cursor:"pointer",fontWeight:a?700:500,borderRadius:5,background:a?"var(--accent-glow)":"transparent",color:a?"var(--accent)":"var(--text-secondary)",fontFamily:"'JetBrains Mono',monospace"});
  return(<div data-testid="contact-admin" style={{padding:"14px 20px"}}>
    <div style={{display:"flex",gap:6,marginBottom:14}}>
      <div data-testid="contact-admin-tab-inbox" style={tS(sub==="inbox")} onClick={()=>setSub("inbox")}>📨 1:1 문의함</div>
      <div data-testid="contact-admin-tab-notices" style={tS(sub==="notices")} onClick={()=>setSub("notices")}>📢 전체 공지</div>
    </div>
    {sub==="inbox"&&<AdminContactInbox user={user}/>}
    {sub==="notices"&&<AdminContactNotices user={user}/>}
  </div>);
}

function AdminContactInbox({user}){
  const admin=user?.username||"";
  const[threads,setThreads]=useState([]);const[sel,setSel]=useState("");const[thr,setThr]=useState(null);
  const[reply,setReply]=useState("");const[sending,setSending]=useState(false);
  const loadThreads=()=>sf("/api/messages/admin/threads?admin="+encodeURIComponent(admin)).then(d=>setThreads(d.threads||[])).catch(()=>{});
  const loadThread=(u)=>sf("/api/messages/admin/thread?admin="+encodeURIComponent(admin)+"&user="+encodeURIComponent(u)).then(setThr).catch(()=>{});
  useEffect(()=>{if(admin)loadThreads();},[admin]);
  useEffect(()=>{if(sel)loadThread(sel);else setThr(null);},[sel]);
  const open=(u)=>{setSel(u);postJson("/api/messages/admin/mark_read",{admin,to_user:u}).then(loadThreads).catch(()=>{});};
  const send=()=>{const v=(reply||"").trim();if(!v||!sel||sending)return;if(v.length>5000){toast.warn("최대 5000자");return;}setSending(true);
    postJson("/api/messages/admin/reply",{admin,to_user:sel,text:v})
      .then(()=>{setReply("");loadThread(sel);loadThreads();}).catch(e=>toast.error("실패: "+(e.message||e))).finally(()=>setSending(false));};
  const totalUnread=threads.reduce((s,t)=>s+(t.unread_for_admin||0),0);
  return(<div style={{display:"flex",gap:12,minHeight:340}}>
    <div style={{width:240,background:"var(--bg-primary)",borderRadius:8,border:"1px solid var(--border)",overflow:"hidden",display:"flex",flexDirection:"column",flexShrink:0}}>
      <div style={{padding:"8px 12px",borderBottom:"1px solid var(--border)",display:"flex",alignItems:"center",gap:6}}>
        <span style={{fontSize:14,fontWeight:700,color:"var(--accent)",fontFamily:"monospace"}}>스레드</span>
        <span style={{fontSize:14,color:"var(--text-secondary)"}}>{threads.length}·미확인 {totalUnread}</span>
        <div style={{flex:1}}/>
        <span onClick={loadThreads} style={{fontSize:14,cursor:"pointer",color:"var(--text-secondary)"}} title="새로고침">↻</span>
      </div>
      <div style={{flex:1,overflowY:"auto",maxHeight:340}}>
        {threads.length===0&&<div style={{padding:20,textAlign:"center",color:"var(--text-secondary)",fontSize:14}}>수신 없음</div>}
        {threads.map(t=>(
          <div key={t.user} onClick={()=>open(t.user)} style={{padding:"8px 12px",borderBottom:"1px solid var(--border)",cursor:"pointer",background:sel===t.user?"var(--accent-glow)":(t.unread_for_admin>0?"rgba(249,115,22,0.05)":"transparent")}}>
            <div style={{display:"flex",alignItems:"center",gap:6,marginBottom:2}}>
              {t.unread_for_admin>0&&<span style={{width:6,height:6,borderRadius:"50%",background:"var(--accent)",flexShrink:0}}/>}
              <span style={{fontSize:14,fontWeight:t.unread_for_admin>0?700:500,color:"var(--text-primary)",fontFamily:"monospace",flex:1,overflow:"hidden",textOverflow:"ellipsis",whiteSpace:"nowrap"}}>{t.user}</span>
              {t.unread_for_admin>0&&<span style={{fontSize:14,fontWeight:700,padding:"1px 5px",borderRadius:3,background:"var(--accent)",color:"#fff"}}>{t.unread_for_admin}</span>}
            </div>
            <div style={{fontSize:14,color:"var(--text-secondary)",overflow:"hidden",textOverflow:"ellipsis",whiteSpace:"nowrap",lineHeight:1.4}}>{t.last_from?`[${t.last_from}] `:""}{t.last_preview||"(비어 있음)"}</div>
          </div>))}
      </div>
    </div>
    <div style={{flex:1,background:"var(--bg-primary)",borderRadius:8,border:"1px solid var(--border)",display:"flex",flexDirection:"column",minWidth:0,minHeight:340}}>
      {!sel&&<div style={{flex:1,display:"flex",alignItems:"center",justifyContent:"center",color:"var(--text-secondary)",fontSize:14,padding:20}}>← 스레드를 선택하세요</div>}
      {sel&&thr&&<>
        <div style={{padding:"8px 12px",borderBottom:"1px solid var(--border)",display:"flex",alignItems:"center",gap:8}}>
          <span style={{fontSize:14,fontWeight:700,color:"var(--accent)",fontFamily:"monospace"}}>💬 {sel}</span>
          <span style={{fontSize:14,color:"var(--text-secondary)"}}>{(thr.messages||[]).length} 메시지</span>
        </div>
        <div style={{flex:1,overflowY:"auto",padding:12,maxHeight:280}}>
          {(thr.messages||[]).map(m=>{const mine=m.from===admin;return(
            <div key={m.id} style={{display:"flex",justifyContent:mine?"flex-end":"flex-start",marginBottom:8}}>
              <div style={{maxWidth:"78%",display:"flex",flexDirection:"column",alignItems:mine?"flex-end":"flex-start"}}>
                <div style={{fontSize:14,color:"var(--text-secondary)",fontFamily:"monospace",marginBottom:2,padding:"0 4px"}}>{mine?`나 (${m.from})`:m.from} · {fmtT(m.created_at)}</div>
                <div style={{padding:"6px 10px",borderRadius:10,background:mine?"var(--accent)":"var(--bg-card)",color:mine?"#fff":"var(--text-primary)",fontSize:14,lineHeight:1.5,whiteSpace:"pre-wrap",wordBreak:"break-word",border:mine?"none":"1px solid var(--border)"}}>{m.text}</div>
              </div>
            </div>);})}
        </div>
        <div style={{padding:"8px 12px",borderTop:"1px solid var(--border)"}}>
          <div style={{display:"flex",gap:8,alignItems:"flex-end"}}>
            <textarea value={reply} onChange={e=>setReply(e.target.value)} disabled={sending}
              onKeyDown={e=>{if((e.metaKey||e.ctrlKey)&&e.key==="Enter"){if(e.nativeEvent?.isComposing||e.keyCode===229)return;send();}}}
              placeholder={`${sel} 에게 답장 (Cmd/Ctrl+Enter 전송)`} rows={2}
              style={{flex:1,padding:"7px 10px",borderRadius:6,border:"1px solid var(--border)",background:"var(--bg-secondary)",color:"var(--text-primary)",fontSize:14,fontFamily:"'Pretendard',sans-serif",resize:"vertical",outline:"none"}}/>
            <button onClick={send} disabled={sending||!reply.trim()}
              style={{padding:"7px 16px",borderRadius:6,border:"none",background:sending||!reply.trim()?"#94a3b8":"var(--accent)",color:"#fff",fontSize:14,fontWeight:700,cursor:sending||!reply.trim()?"default":"pointer",flexShrink:0,alignSelf:"stretch"}}>{sending?"…":"답장"}</button>
          </div>
        </div>
      </>}
    </div>
  </div>);
}

function AdminContactNotices({user}){
  const admin=user?.username||"";
  const[notices,setNotices]=useState([]);
  const[title,setTitle]=useState("");const[body,setBody]=useState("");const[sending,setSending]=useState(false);
  const loadNotices=()=>sf("/api/messages/admin/notices?admin="+encodeURIComponent(admin)).then(d=>setNotices(d.notices||[])).catch(()=>{});
  useEffect(()=>{if(admin){loadNotices();}},[admin]);
  const publish=()=>{
    const t=title.trim(),b=body.trim();if(!t&&!b){toast.warn("제목 또는 본문을 입력하세요.");return;}
    if(sending)return;setSending(true);
    postJson("/api/messages/admin/notice_create",{author:admin,title:t,body:b})
      .then(()=>{setTitle("");setBody("");loadNotices();toast.ok("전체 공지가 발행되었습니다.");})
      .catch(e=>toast.error("실패: "+(e.message||e))).finally(()=>setSending(false));
  };
  const del=(id)=>{if(!confirm("공지사항을 삭제하시겠습니까?"))return;
    postJson("/api/messages/admin/notice_delete",{admin,id}).then(loadNotices).catch(e=>toast.error(e.message));};
  const S={width:"100%",padding:"8px 12px",borderRadius:6,border:"1px solid var(--border)",background:"var(--bg-primary)",color:"var(--text-primary)",fontSize:14,outline:"none",fontFamily:"'Pretendard',sans-serif",boxSizing:"border-box"};
  return(<div>
    <div style={{background:"var(--bg-primary)",border:"1px solid var(--accent)",borderRadius:8,padding:14,marginBottom:14}}>
      <div data-testid="contact-admin-mode-all" style={{display:"flex",alignItems:"center",gap:6,fontSize:14,marginBottom:10,color:"var(--accent)",fontFamily:"'JetBrains Mono',monospace",fontWeight:700}}>
        📢 전체 공지 작성 — 모든 사용자에게 발행
      </div>
      <input data-testid="contact-admin-notice-title" value={title} onChange={e=>setTitle(e.target.value)} placeholder="제목 (최대 200자)" maxLength={200} style={{...S,marginBottom:8,fontWeight:600}}/>
      <textarea data-testid="contact-admin-notice-body" value={body} onChange={e=>setBody(e.target.value)} placeholder="공지 본문 (최대 5000자)" rows={4} style={{...S,marginBottom:8,resize:"vertical"}}/>
      <div style={{display:"flex",alignItems:"center"}}>
        <span style={{fontSize:14,color:"var(--text-secondary)"}}>{title.length}/200 · {body.length}/5000</span>
        <div style={{flex:1}}/>
        <button data-testid="contact-admin-notice-publish" onClick={publish} disabled={sending||(!title.trim()&&!body.trim())}
          style={{padding:"7px 18px",borderRadius:5,border:"none",background:sending||(!title.trim()&&!body.trim())?"#94a3b8":"var(--accent)",color:"#fff",fontSize:14,fontWeight:700,cursor:sending?"default":"pointer"}}>
          {sending?"…":"전체 발행"}
        </button>
      </div>
    </div>

    {/* 기존 공지 리스트 */}
    <div style={{fontSize:14,color:"var(--text-secondary)",fontFamily:"monospace",marginBottom:6}}>기존 공지사항 ({notices.length})</div>
    <div style={{background:"var(--bg-primary)",borderRadius:8,border:"1px solid var(--border)",overflow:"hidden",maxHeight:320,overflowY:"auto"}}>
      {notices.length===0&&<div style={{padding:24,textAlign:"center",color:"var(--text-secondary)",fontSize:14}}>등록된 공지사항이 없습니다.</div>}
      {notices.map(n=>(
        <div key={n.id} style={{padding:"10px 14px",borderBottom:"1px solid var(--border)"}}>
          <div style={{display:"flex",alignItems:"center",gap:8,marginBottom:3}}>
            <span style={{fontSize:14,fontWeight:700,color:"var(--text-primary)",flex:1}}>{n.title||"(제목 없음)"}</span>
            <span style={{fontSize:14,color:"var(--text-secondary)",fontFamily:"monospace"}}>{fmtT(n.created_at)}</span>
            <span style={{fontSize:14,color:"var(--accent)",fontFamily:"monospace"}}>👁 {n.read_count||0}/{n.total_recipients||"?"}</span>
            <span onClick={()=>del(n.id)} style={{cursor:"pointer",color:"#ef4444",fontSize:14}}>🗑</span>
          </div>
          {n.body&&<div style={{fontSize:14,color:"var(--text-secondary)",lineHeight:1.5,whiteSpace:"pre-wrap"}}>{n.body}</div>}
          <div style={{fontSize:14,color:"var(--text-secondary)",fontFamily:"monospace",marginTop:3}}>by {n.author}</div>
        </div>))}
    </div>
  </div>);
}
