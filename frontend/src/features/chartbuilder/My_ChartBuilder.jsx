import { useEffect, useMemo, useRef, useState } from "react";
import BoxStatsTable from "../../components/BoxStatsTable";
import { FlowPlotlyChart } from "../../components/PlotlyChart";
import SpreadsheetPasteGrid, { normalizeSpreadsheetRows } from "../../components/SpreadsheetPasteGrid";
import TegValueWaferMap from "../../components/TegValueWaferMap";
import { toast } from "../../components/Toast";
import { postJson, sf } from "../../lib/api";
import { boxBucketsFromPoints, boxStatsAlignment } from "../../lib/boxStats";
import { chartColorMap as buildChartColorMap, chartColorValue, parseChartColorRules } from "../../lib/chartColorRules";
import { chartColorListRules, chartColorListTextFromRules, parseChartColorList, partitionChartColorRules } from "../../lib/chartColorList";
import { canManagePage } from "../../lib/permissions";

const card={border:"1px solid var(--border)",borderRadius:10,background:"var(--bg-secondary)",padding:14};
const input={width:"100%",boxSizing:"border-box",border:"1px solid var(--border)",borderRadius:6,background:"var(--bg-primary)",color:"var(--text-primary)",padding:"7px 9px",fontSize:13};
const btn={border:"1px solid var(--border)",borderRadius:6,background:"var(--bg-tertiary)",color:"var(--text-primary)",padding:"7px 11px",fontWeight:800,cursor:"pointer"};
const CHART_BUILDER_TRANSFER_KEY="flow:chartbuilder:definition-transfer";
const PIE_SLICE_LIMIT=12;
const AGGREGATIONS=["avg","median","p10","p90","min","max"];
// Trend 단위 — shot/wafer 는 점만 찍고(원 측정값의 산포를 그대로 보여준다),
// 일별·주별은 구간마다 값이 하나뿐이라 선으로 이어야 흐름이 읽힌다.
const TREND_GRAINS=[
  {key:"shot",label:"Shot raw",line:false,desc:"같은 시각의 여러 shot 측정값을 선 없이 각각의 점으로 표시합니다."},
  {key:"wafer",label:"Wafer 집계",line:false,desc:"root lot·wafer별 집계값 하나를 선 없이 점으로 표시합니다."},
  {key:"daily",label:"일별 집계",line:true,desc:"날짜별 집계값을 선으로 이어 표시합니다."},
  {key:"weekly",label:"주별 집계",line:true,desc:"주(월요일 시작)별 집계값을 선으로 이어 표시합니다."},
];
const fieldInput={width:"100%",boxSizing:"border-box",border:"1px solid #cbd5e1",borderRadius:6,background:"#fff",color:"#111827",padding:"6px 8px",fontSize:13};
function Field({label,children}){
  return <label style={{display:"flex",flexDirection:"column",gap:4,minWidth:0}}>
    <span style={{fontSize:11,fontWeight:800,color:"#64748b",letterSpacing:.2}}>{label}</span>{children}
  </label>;
}
function pad2(value){return String(value).padStart(2,"0");}
// 일별/주별 구간 키. 파싱이 안 되는 값은 앞 10글자(날짜부)로 떨어뜨린다.
function timeBucket(value,mode){
  const raw=text(value).trim();
  if(!raw)return "";
  const parsed=new Date(raw.replace(" ","T"));
  if(Number.isNaN(parsed.getTime()))return raw.slice(0,10);
  if(mode==="weekly"){
    const monday=new Date(parsed);
    monday.setDate(parsed.getDate()-((parsed.getDay()+6)%7));
    return `${monday.getFullYear()}-${pad2(monday.getMonth()+1)}-${pad2(monday.getDate())}`;
  }
  return `${parsed.getFullYear()}-${pad2(parsed.getMonth()+1)}-${pad2(parsed.getDate())}`;
}
const TRELLIS_HEADERS=[
  {background:"#dbeafe",border:"#3b82f6"},{background:"#dcfce7",border:"#22c55e"},{background:"#fef3c7",border:"#f59e0b"},
  {background:"#fce7f3",border:"#ec4899"},{background:"#ede9fe",border:"#8b5cf6"},{background:"#cffafe",border:"#06b6d4"},
  {background:"#ffedd5",border:"#f97316"},{background:"#e0e7ff",border:"#6366f1"},{background:"#ccfbf1",border:"#14b8a6"},
  {background:"#fee2e2",border:"#ef4444"},{background:"#f3e8ff",border:"#a855f7"},{background:"#ecfccb",border:"#84cc16"},
];

// JOIN 방식 — 이름과 한 줄 요약은 select 에, 자세한 설명은 사용 가이드에 쓴다.
// 기본은 left: 왼쪽(기준) query 의 행을 잃지 않아야 "붙지 않은 행"이 눈에 보인다.
const JOIN_HOWS=[
  {how:"left",short:"왼쪽 전부 유지",desc:"왼쪽 query 의 행을 모두 남기고 오른쪽에서 key 가 맞는 값을 붙입니다. 못 붙은 행은 오른쪽 열이 빈칸이 됩니다. 기준 데이터를 잃지 않으므로 기본값이며, 매칭이 얼마나 됐는지 확인하기 좋습니다."},
  {how:"inner",short:"양쪽 다 있는 행만",desc:"양쪽에 key 가 모두 있는 행만 남깁니다. 빈칸 없는 깨끗한 결과가 되지만, 한쪽에만 있는 행은 조용히 사라지므로 행 수가 줄었는지 꼭 확인하세요."},
  {how:"full",short:"양쪽 전부 유지",desc:"어느 한쪽에만 있는 행까지 모두 남깁니다. 두 DB 사이에 무엇이 빠졌는지 양방향으로 훑어볼 때 씁니다."},
  {how:"semi",short:"오른쪽에 있는 왼쪽 행만",desc:"오른쪽에 key 가 있는 왼쪽 행만 남기고, 오른쪽 열은 붙이지 않습니다. 값은 필요 없고 '오른쪽에 존재하는가'로 거르기만 할 때 씁니다(행이 늘지 않습니다)."},
  {how:"anti",short:"오른쪽에 없는 왼쪽 행만",desc:"semi 의 반대로, 오른쪽에 key 가 없는 왼쪽 행만 남깁니다. 매칭 실패분을 뽑아 원인을 볼 때 씁니다."},
];
const DERIVED_GRID_COLUMNS=["name","columns","separator"];
const FILTER_GRID_COLUMNS=["column","operator","values"];
const DERIVED_GRID_MAX_ROWS=20;
const FILTER_GRID_MAX_ROWS=50;
const AUTO_REPORT_PRESETS=[
  {key:"box",label:"Box",desc:"범주별 분포와 통계표"},
  {key:"trend",label:"Trend",desc:"시간 순서 shot 추이"},
  {key:"wafer_map",label:"WF MAP",desc:"shot 좌표 wafer 분포"},
];
function normalizeDerivedRows(rows){return normalizeSpreadsheetRows(rows,DERIVED_GRID_COLUMNS,{minRows:4,maxRows:DERIVED_GRID_MAX_ROWS});}
function normalizeFilterRows(rows){return normalizeSpreadsheetRows(rows,FILTER_GRID_COLUMNS,{minRows:4,maxRows:FILTER_GRID_MAX_ROWS});}
function cleanDerivedColumns(rows){
  return(rows||[]).map(row=>({
    name:text(row?.name).trim(),
    columns:listValues(row?.columns),
    separator:row?.separator==null||text(row.separator)===""?"_":text(row.separator).slice(0,8),
  })).filter(row=>row.name&&row.columns.length).slice(0,DERIVED_GRID_MAX_ROWS);
}
function cleanRuntimeFilters(rows){
  return(rows||[]).map(row=>({
    column:text(row?.column).trim(),
    operator:text(row?.operator).trim().toLowerCase().replace(/[\s-]+/g,"_")||"in",
    values:listValues(row?.values),
  })).filter(row=>row.column&&(row.values.length||["is_blank","not_blank","blank","is_not_blank"].includes(row.operator))).slice(0,FILTER_GRID_MAX_ROWS);
}
function newSource(index){return{id:`q${index}`,root:"",product:"",sql:"",select_cols:"",apply_reformatter:false,reformatter_items:"",runtime_recent_days:"",runtime_date_column:"",runtime_root_lot_ids:[],runtime_wafer_ids:[],runtime_lot_wafer_pairs:[],derived_columns:normalizeDerivedRows([]),runtime_filters:normalizeFilterRows([])};}
// 시간 창은 저장 차트의 기본값이고, Template Report의 명시적 실행 컨텍스트가 이번 실행에만 덮어쓸 수 있다.
const DEFAULT_DATE_COLUMN="tkout_time";
function recentDaysValue(source){const days=Number(text(source?.runtime_recent_days).trim());return Number.isFinite(days)&&days>0?Math.min(3650,Math.round(days)):0;}
function text(v){return v==null?"":String(v);}
function listValues(value){return text(value).split(/[,\n]+/).map(item=>item.trim()).filter((item,index,all)=>item&&all.findIndex(other=>other.toLowerCase()===item.toLowerCase())===index).slice(0,200);}
function csvCell(v){const s=text(v);return /[",\n\r]/.test(s)?`"${s.replace(/"/g,'""')}"`:s;}
const COLOR_LIST_COLUMNS=["root_lot_id","wafer_id","color"];
const COLOR_LIST_MIN_ROWS=10;
const COLOR_LIST_MAX_ROWS=200;
function blankColorListRow(){return{root_lot_id:"",wafer_id:"",color:""};}
function colorListHeaderIndex(cells){
  const normalized=cells.map(cell=>text(cell).trim().toLowerCase().replace(/[\s-]+/g,"_"));
  const aliases={root_lot_id:"root_lot_id",root_lot:"root_lot_id",rootlotid:"root_lot_id",lot:"root_lot_id",wafer_id:"wafer_id",wafer:"wafer_id",wf:"wafer_id",color:"color",colour:"color",색상:"color",색:"color"};
  const names=normalized.map(name=>aliases[name]||"");
  return COLOR_LIST_COLUMNS.every(name=>names.includes(name))?Object.fromEntries(COLOR_LIST_COLUMNS.map(name=>[name,names.indexOf(name)])):null;
}
function normalizeColorListRows(rows){
  const next=(rows||[]).slice(0,COLOR_LIST_MAX_ROWS).map(row=>Object.fromEntries(COLOR_LIST_COLUMNS.map(name=>[name,text(row?.[name])])));
  let last=next.length-1;
  while(last>=0&&!COLOR_LIST_COLUMNS.some(name=>text(next[last]?.[name]).trim()))last-=1;
  const target=Math.min(COLOR_LIST_MAX_ROWS,Math.max(COLOR_LIST_MIN_ROWS,last+2));
  next.length=Math.min(next.length,target);
  while(next.length<target)next.push(blankColorListRow());
  return next;
}
function colorListRowsFromText(value){
  const lines=text(value).replace(/\r\n?/g,"\n").split("\n").filter(line=>line.trim());
  if(!lines.length)return normalizeColorListRows([]);
  const split=line=>line.includes("\t")?line.split("\t"):line.split(",");
  const first=split(lines[0]),header=colorListHeaderIndex(first),body=lines.slice(header?1:0);
  return normalizeColorListRows(body.map(line=>{
    const cells=split(line),indexes=header||{root_lot_id:0,wafer_id:1,color:2};
    return Object.fromEntries(COLOR_LIST_COLUMNS.map(name=>[name,text(cells[indexes[name]]).trim()]));
  }));
}
function colorListTextFromRows(rows){
  const filled=(rows||[]).filter(row=>COLOR_LIST_COLUMNS.some(name=>text(row?.[name]).trim()));
  return filled.length?[COLOR_LIST_COLUMNS.join("\t"),...filled.map(row=>COLOR_LIST_COLUMNS.map(name=>text(row?.[name]).trim()).join("\t"))].join("\n"):"";
}
const DEFINITION_EXAMPLE=`Q1
TABLE = INLINE
PRODUCT = PRODA
SQL = SELECT root_lot_id, wafer_id, item_id, value
      WHERE item_id = 'CD1'
DERIVE = lot_wafer | columns=root_lot_id,wafer_id | separator=_
FILTER = lot_wafer | operator=in | values=A1234_1,A1234_2

Q2
TABLE = ET
PRODUCT = PRODA
SQL = SELECT root_lot_id, wafer_id, tkout_time, VTH_INDEX
RECENT_DAYS = 30
DATE_COLUMN = tkout_time
REFORMATTER = true
ITEMS = VTH_INDEX

JOIN q1 LEFT q2 ON root_lot_id, wafer_id

CHART
TYPE = scatter
X = tkout_time
Y = VTH_INDEX
COLOR = custom
COLOR_RULE = tkout_time WITHIN 3 DAYS THEN #dc2626
COLOR_RULE = tkout_time WITHIN 7 DAYS THEN #f59e0b
COLOR_ELSE = #cbd5e1
HIGHLIGHT = true
WIDTH = 1200
HEIGHT = 650

MAX_ROWS = 10000`;
const GUIDE_EXAMPLES=[
  {
    title:"예시 1 · 최근 30일 Trend에서 3일/7일 구간 강조",
    interpretation:"tkout_time 기준 최근 30일을 조회합니다. 그중 3일 이내는 빨강, 3일 초과~7일 이내는 주황, 나머지는 회색입니다. COLOR_RULE은 위에서부터 첫 일치만 적용되므로 3일 규칙을 7일 규칙보다 먼저 둡니다.",
    code:`Q1
TABLE = ET
PRODUCT = PRODA
SQL = SELECT root_lot_id, wafer_id, tkout_time, VTH_INDEX
      ORDER BY tkout_time
RECENT_DAYS = 30
DATE_COLUMN = tkout_time
REFORMATTER = true
ITEMS = VTH_INDEX

CHART
TYPE = line
X = tkout_time
Y = VTH_INDEX
COLOR = custom
COLOR_RULE = tkout_time WITHIN 3 DAYS THEN #dc2626
COLOR_RULE = tkout_time WITHIN 7 DAYS THEN #f59e0b
COLOR_ELSE = #cbd5e1
WIDTH = 1200
HEIGHT = 650

MAX_ROWS = 10000`,
  },
  {
    title:"예시 2 · 최근 데이터와 특정 조건이 동시에 맞을 때만 강조",
    interpretation:"최근 14일을 조회하되, 최근 5일이면서 purpose가 EVALUATION인 행만 보라색으로 표시합니다. AND 조건은 모두 만족해야 하며 그 밖의 행은 옅은 회색입니다.",
    code:`Q1
TABLE = SPLITTABLE
PRODUCT = PRODA
SQL = SELECT root_lot_id, wafer_id, tkout_time, purpose, INLINE_CD
      ORDER BY tkout_time
RECENT_DAYS = 14
DATE_COLUMN = tkout_time

CHART
TYPE = scatter
X = tkout_time
Y = INLINE_CD
COLOR = custom
COLOR_RULE = tkout_time WITHIN 5 DAYS AND purpose = 'EVALUATION' THEN #7c3aed
COLOR_ELSE = #d1d5db
HIGHLIGHT = true
WIDTH = 1100
HEIGHT = 600

MAX_ROWS = 5000`,
  },
  {
    title:"예시 3 · JOIN + Template Report 변수 재사용",
    interpretation:"Template Report 실행 시 PRODUCT와 LOT을 바꿉니다. 기본은 저장된 최근 60일 조회 및 최근 7일 강조 규칙이고, 필요하면 Report 공통 실행 컨텍스트에서 기간·색을 모든 차트에 한꺼번에 바꿀 수 있습니다.",
    code:`[split]
TABLE = SPLITTABLE
PRODUCT = {{PRODUCT}}
SQL = SELECT root_lot_id, wafer_id, KNOB_SPLIT
      WHERE root_lot_id = '{{LOT}}'

[et]
TABLE = ET
PRODUCT = {{PRODUCT}}
SQL = SELECT root_lot_id, wafer_id, tkout_time, VTH_INDEX
      WHERE root_lot_id = '{{LOT}}'
      ORDER BY tkout_time
RECENT_DAYS = 60
DATE_COLUMN = tkout_time
REFORMATTER = true
ITEMS = VTH_INDEX

JOIN split LEFT et ON root_lot_id, wafer_id

CHART
TYPE = box
X = KNOB_SPLIT
Y = VTH_INDEX
COLOR = custom
COLOR_RULE = tkout_time WITHIN 7 DAYS THEN #2563eb
COLOR_ELSE = #cbd5e1
WIDTH = 1200
HEIGHT = 650

MAX_ROWS = 10000`,
  },
];
function definitionFromForm(sources,joins,maxRows,chart={}){
  const lines=[];
  (sources||[]).forEach((source,index)=>{
    const id=text(source.id).trim()||`q${index+1}`;
    lines.push(/^q\d+$/i.test(id)?id.toUpperCase():`[${id}]`);
    lines.push(`TABLE = ${text(source.root).trim()}`);
    lines.push(`PRODUCT = ${text(source.product).trim()}`);
    const sqlLines=text(source.sql).trim().split(/\r?\n/);
    lines.push(`SQL = ${sqlLines[0]||""}`);
    sqlLines.slice(1).forEach(line=>lines.push(`  ${line}`));
    if(text(source.select_cols).trim())lines.push(`SELECT_COLS = ${text(source.select_cols).trim()}`);
    const recentDays=recentDaysValue(source);
    if(recentDays){
      lines.push(`RECENT_DAYS = ${recentDays}`);
      lines.push(`DATE_COLUMN = ${text(source.runtime_date_column).trim()||DEFAULT_DATE_COLUMN}`);
    }
    const rootLots=listValues(source.runtime_root_lot_ids||[]),wafers=listValues(source.runtime_wafer_ids||[]);
    const linkedPairs=Array.isArray(source.runtime_lot_wafer_pairs)?source.runtime_lot_wafer_pairs.filter(pair=>text(pair?.root_lot_id).trim()&&text(pair?.wafer_id).trim()):[];
    if(!linkedPairs.length&&rootLots.length)lines.push(`ROOT_LOTS = ${rootLots.join(", ")}`);
    if(!linkedPairs.length&&wafers.length)lines.push(`WAFERS = ${wafers.join(", ")}`);
    if(source.apply_reformatter){
      lines.push("REFORMATTER = true");
      if(text(source.reformatter_items).trim())lines.push(`ITEMS = ${text(source.reformatter_items).trim()}`);
    }
    cleanDerivedColumns(source.derived_columns).forEach(row=>lines.push(`DERIVE = ${row.name} | columns=${row.columns.join(",")} | separator=${row.separator}`));
    cleanRuntimeFilters(source.runtime_filters).forEach(row=>lines.push(`FILTER = ${row.column} | operator=${row.operator} | values=${row.values.join(",")}`));
    lines.push("");
  });
  (joins||[]).forEach(join=>{
    const leftOn=text(join.left_on).split(",").map(v=>v.trim()).filter(Boolean).join(", ");
    const rightOn=text(join.right_on).split(",").map(v=>v.trim()).filter(Boolean).join(", ");
    lines.push(`JOIN ${text(join.left).trim()} ${text(join.how||"left").toUpperCase()} ${text(join.right).trim()} ON ${leftOn===rightOn?leftOn:`${leftOn} = ${rightOn}`}`);
  });
  if((joins||[]).length)lines.push("");
  if(chart&&Object.keys(chart).length){
    lines.push("CHART");
    [["type","TYPE"],["title","TITLE"],["x","X"],["y","Y"],["x_label","X_LABEL"],["y_label","Y_LABEL"],["color","COLOR"],["trellis","TRELLIS"],["trend_grain","TREND_GRAIN"],["aggregation","AGGREGATION"],["map_y","MAP_Y"],["map_scope","MAP_SCOPE"],["map_target","MAP_TARGET"],["pie_basis","PIE_BASIS"],["fit","FIT"],["point_size","POINT_SIZE"],["marker_opacity","MARKER_OPACITY"],["line_width","LINE_WIDTH"],["y_min","Y_MIN"],["y_max","Y_MAX"],["y_scale","Y_SCALE"],["legend_position","LEGEND_POSITION"],["spec_low","SPEC_LOW"],["spec_high","SPEC_HIGH"],["box_points","BOX_POINTS"],["wafer_palette","WAFER_PALETTE"],["wafer_low","WAFER_LOW"],["wafer_center","WAFER_CENTER"],["wafer_high","WAFER_HIGH"],["width","WIDTH"],["height","HEIGHT"]].forEach(([key,label])=>{
      if(text(chart[key]).trim())lines.push(`${label} = ${text(chart[key]).trim()}`);
    });
    (chart.color_rules||[]).forEach(rule=>{if(text(rule).trim())lines.push(`COLOR_RULE = ${text(rule).trim()}`);});
    if(text(chart.color_else).trim())lines.push(`COLOR_ELSE = ${text(chart.color_else).trim()}`);
    if(chart.highlight!=null)lines.push(`HIGHLIGHT = ${chart.highlight?"true":"false"}`);
    if(chart.show_legend!=null)lines.push(`SHOW_LEGEND = ${chart.show_legend?"true":"false"}`);
    if(chart.show_grid!=null)lines.push(`SHOW_GRID = ${chart.show_grid?"true":"false"}`);
    lines.push("");
  }
  lines.push(`MAX_ROWS = ${Math.max(1,Math.min(10000,Number(maxRows)||10000))}`);
  return lines.join("\n").trim()+"\n";
}
function historyTime(value){
  const date=new Date(value);
  return Number.isNaN(date.getTime())?text(value):date.toLocaleString("ko-KR",{hour12:false});
}
function sqlIdentifier(value){
  const name=text(value).trim();
  return /^[A-Za-z_][A-Za-z0-9_]*$/.test(name)?name:`\`${name.replace(/`/g,"``")}\``;
}

function sqlColumnCompletion(value,caret){
  const source=text(value),position=Math.max(0,Math.min(Number(caret)||0,source.length));
  const before=source.slice(0,position);
  const keywords=[...before.matchAll(/\b(SELECT|WHERE|ORDER\s+BY|GROUP\s+BY|LIMIT)\b/gi)];
  const latest=keywords[keywords.length-1];
  if(!latest)return null;
  const clause=latest[1].replace(/\s+/g," ").toUpperCase();
  if(!["SELECT","WHERE","ORDER BY","GROUP BY"].includes(clause))return null;
  const clauseText=before.slice((latest.index||0)+latest[0].length);
  let singleQuoted=false;
  for(let index=0;index<clauseText.length;index+=1){
    if(clauseText[index]!=="'")continue;
    if(singleQuoted&&clauseText[index+1]==="'"){index+=1;continue;}
    singleQuoted=!singleQuoted;
  }
  if(singleQuoted)return null;
  let start=position;
  while(start>0&&/[A-Za-z0-9_$]/.test(source[start-1]))start-=1;
  const token=source.slice(start,position);
  if(token.length<1||!/^[A-Za-z_$]/.test(token))return null;
  let end=position;
  while(end<source.length&&/[A-Za-z0-9_$]/.test(source[end]))end+=1;
  return{clause,token,start,end};
}

function definitionSqlAutocompleteContext(value,caret){
  const source=text(value),position=Math.max(0,Math.min(Number(caret)||0,source.length));
  const before=source.slice(0,position);
  const headers=[...before.matchAll(/(?:^|\n)[ \t]*(?:Q\d+|\[[^\]\n]+\])(?=[ \t]*(?:\n|\||$))/gi)];
  const header=headers[headers.length-1];
  if(!header)return null;
  const blockStart=(header.index||0)+header[0].lastIndexOf("\n")+1;
  const blockBefore=source.slice(blockStart,position);
  const sqlAssignments=[...blockBefore.matchAll(/\bSQL\s*=\s*/gi)];
  const sqlAssignment=sqlAssignments[sqlAssignments.length-1];
  if(!sqlAssignment)return null;
  const sqlStart=blockStart+(sqlAssignment.index||0)+sqlAssignment[0].length;
  const sqlBeforeCaret=source.slice(sqlStart,position);
  if(/\n(?![ \t])(?:[A-Z_][A-Z0-9_]*\s*=|JOIN\b|CHART\b|Q\d+\b|\[)/i.test(sqlBeforeCaret))return null;
  const completion=sqlColumnCompletion(source.slice(sqlStart),position-sqlStart);
  const table=blockBefore.match(/\bTABLE\s*=\s*([^\s|]+)/i)?.[1]||"";
  const product=blockBefore.match(/\bPRODUCT\s*=\s*([^\s|]+)/i)?.[1]||"";
  return completion?{
    completion:{...completion,start:completion.start+sqlStart,end:completion.end+sqlStart},
    root:table,
    product,
  }:null;
}

function definitionQueryAutocompleteContexts(value){
  const source=text(value);
  const headers=[...source.matchAll(/(?:^|\n)[ \t]*(Q\d+|\[([^\]\n]+)\])(?=[ \t]*(?:\n|\||$))/gi)];
  return headers.map((header,index)=>{
    const start=(header.index||0)+header[0].lastIndexOf("\n")+1;
    const end=index+1<(headers.length)?(headers[index+1].index||source.length):source.length;
    const block=source.slice(start,end);
    return{
      id:text(header[2]||header[1]).replace(/^\[|\]$/g,"").trim(),
      root:block.match(/\bTABLE\s*=\s*([^\s|]+)/i)?.[1]||"",
      product:block.match(/\bPRODUCT\s*=\s*([^\s|]+)/i)?.[1]||"",
    };
  });
}

function textareaCaretPoint(textarea,position){
  if(!textarea||typeof document==="undefined")return{left:8,top:30,width:320};
  const computed=window.getComputedStyle(textarea);
  const mirror=document.createElement("div");
  const properties=[
    "boxSizing","width","borderTopWidth","borderRightWidth","borderBottomWidth","borderLeftWidth",
    "paddingTop","paddingRight","paddingBottom","paddingLeft","fontStyle","fontVariant","fontWeight",
    "fontStretch","fontSize","fontFamily","lineHeight","letterSpacing","textTransform","textAlign",
    "textIndent","textDecoration","wordSpacing","tabSize","MozTabSize",
  ];
  properties.forEach(property=>{mirror.style[property]=computed[property];});
  mirror.style.position="absolute";
  mirror.style.visibility="hidden";
  mirror.style.whiteSpace="pre-wrap";
  mirror.style.overflowWrap="break-word";
  mirror.style.top="0";
  mirror.style.left="-9999px";
  mirror.style.height="auto";
  mirror.textContent=text(textarea.value).slice(0,position);
  if(textarea.value[position-1]==="\n")mirror.textContent+="\u200b";
  const marker=document.createElement("span");
  marker.textContent=text(textarea.value).slice(position)||"\u200b";
  mirror.appendChild(marker);
  document.body.appendChild(mirror);
  const lineHeight=Number.parseFloat(computed.lineHeight)||Number.parseFloat(computed.fontSize)*1.4||18;
  const point={
    left:marker.offsetLeft-textarea.scrollLeft,
    top:marker.offsetTop-textarea.scrollTop+lineHeight+3,
    width:textarea.clientWidth,
  };
  mirror.remove();
  return point;
}

function SqlColumnAutocomplete({value,onChange,root,product,resolveContext,ariaLabel,rows=4,placeholder="",id,style={}}){
  const textareaRef=useRef(null);
  const[caret,setCaret]=useState(text(value).length);
  const[focused,setFocused]=useState(false);
  const[suggestions,setSuggestions]=useState([]);
  const[loading,setLoading]=useState(false);
  const[activeIndex,setActiveIndex]=useState(0);
  const[suspendSearch,setSuspendSearch]=useState(false);
  const[caretPoint,setCaretPoint]=useState({left:8,top:30,width:320});
  const resolved=useMemo(()=>resolveContext?resolveContext(value,caret):null,[resolveContext,value,caret]);
  const completion=resolved?.completion||sqlColumnCompletion(value,caret);
  const activeRoot=resolved?.root||root;
  const activeProduct=resolved?.product||product;
  const canSearch=Boolean(focused&&!suspendSearch&&activeRoot&&activeProduct&&completion);
  const listId=`${ariaLabel.replace(/[^A-Za-z0-9_-]+/g,"-").toLowerCase()}-columns`;

  useEffect(()=>{
    if(!canSearch){setSuggestions([]);setLoading(false);return undefined;}
    let alive=true;
    const timer=setTimeout(()=>{
      setLoading(true);
      sf(`/api/filebrowser/columns/search?root=${encodeURIComponent(activeRoot)}&product=${encodeURIComponent(activeProduct)}&q=${encodeURIComponent(completion.token)}&limit=80`)
        .then(data=>{
          if(!alive)return;
          const needle=completion.token.toLocaleLowerCase();
          const virtual=new Set((data.virtual_columns||[]).map(column=>String(column||"")));
          const matches=(data.columns||[]).map(column=>({
            name:String(column||""),dtype:String(data.dtypes?.[column]||""),virtual:virtual.has(String(column||"")),
          })).filter(column=>column.name)
            .sort((left,right)=>{
              const l=left.name.toLocaleLowerCase(),r=right.name.toLocaleLowerCase();
              const lp=l.startsWith(needle)?0:1,rp=r.startsWith(needle)?0:1;
              return lp-rp||l.localeCompare(r);
            });
          setSuggestions(matches);
          setActiveIndex(0);
        })
        .catch(()=>{if(alive)setSuggestions([]);})
        .finally(()=>{if(alive)setLoading(false);});
    },180);
    return()=>{alive=false;clearTimeout(timer);};
  },[canSearch,activeRoot,activeProduct,completion?.token,completion?.clause]);

  const syncCaret=target=>{
    const position=Number(target?.selectionStart)||0;
    setCaret(position);
    setCaretPoint(textareaCaretPoint(target,position));
  };
  const applySuggestion=suggestion=>{
    const column=typeof suggestion==="string"?suggestion:suggestion?.name;
    if(!completion||!column)return;
    const inserted=sqlIdentifier(column);
    const next=text(value).slice(0,completion.start)+inserted+text(value).slice(completion.end);
    const nextCaret=completion.start+inserted.length;
    onChange(next);
    setSuggestions([]);
    setSuspendSearch(true);
    setCaret(nextCaret);
    requestAnimationFrame(()=>{
      textareaRef.current?.focus();
      textareaRef.current?.setSelectionRange(nextCaret,nextCaret);
    });
  };
  const handleKeyDown=event=>{
    if(!canSearch||!suggestions.length)return;
    if(event.key==="Tab"){
      event.preventDefault();
      applySuggestion(suggestions[activeIndex]||suggestions[0]);
    }else if(event.key==="ArrowDown"){
      event.preventDefault();
      setActiveIndex(index=>(index+1)%suggestions.length);
    }else if(event.key==="ArrowUp"){
      event.preventDefault();
      setActiveIndex(index=>(index-1+suggestions.length)%suggestions.length);
    }else if(event.key==="Escape"){
      event.preventDefault();
      setSuggestions([]);
    }
  };
  const showPanel=canSearch&&(loading||suggestions.length>0);
  const popupLeft=Math.max(4,Math.min(caretPoint.left,Math.max(4,caretPoint.width-250)));
  const popupWidth=Math.max(230,Math.min(420,caretPoint.width-popupLeft-4));
  return <div style={{position:"relative"}}>
    <textarea
      ref={textareaRef}
      id={id}
      aria-label={ariaLabel}
      aria-autocomplete="list"
      aria-controls={showPanel?listId:undefined}
      aria-expanded={showPanel}
      value={value}
      onChange={event=>{setSuspendSearch(false);onChange(event.target.value);syncCaret(event.target);}}
      onFocus={event=>{setFocused(true);syncCaret(event.target);}}
      onBlur={()=>setFocused(false)}
      onClick={event=>syncCaret(event.target)}
      onSelect={event=>syncCaret(event.target)}
      onScroll={event=>syncCaret(event.target)}
      onKeyUp={event=>{if(!["Tab","ArrowDown","ArrowUp","Escape"].includes(event.key))syncCaret(event.target);}}
      onKeyDown={handleKeyDown}
      rows={rows}
      placeholder={placeholder}
      spellCheck={false}
      style={style}
    />
    {showPanel&&<div id={listId} role="listbox" aria-label={`${ariaLabel} 열 자동완성`} style={{position:"absolute",zIndex:40,left:popupLeft,top:caretPoint.top,width:popupWidth,border:"1px solid var(--accent)",borderRadius:6,background:"var(--bg-primary)",boxShadow:"0 10px 26px rgba(15,23,42,.24)",maxHeight:170,overflow:"auto"}}>
      <div style={{position:"sticky",top:0,zIndex:1,display:"flex",alignItems:"center",gap:6,padding:"6px 9px",background:"var(--bg-tertiary)",borderBottom:"1px solid var(--border)",fontSize:11,color:"var(--text-secondary)"}}>
        <b style={{color:"var(--accent)"}}>{completion?.clause}</b>
        <span>열 검색 · Tab 자동완성</span>
        {loading&&<span style={{marginLeft:"auto"}}>조회 중…</span>}
      </div>
      {suggestions.map((column,index)=><button key={column.name} type="button" role="option" aria-selected={index===activeIndex} onMouseDown={event=>{event.preventDefault();applySuggestion(column);}} style={{display:"flex",alignItems:"center",gap:8,width:"100%",padding:"7px 9px",border:0,borderBottom:index<suggestions.length-1?"1px solid var(--border)":0,background:index===activeIndex?"var(--accent-glow)":"transparent",color:"var(--text-primary)",textAlign:"left",fontSize:12,cursor:"pointer"}}>
        <code style={{fontFamily:"'JetBrains Mono',monospace",fontWeight:800}}>{column.name}</code>
        {column.virtual&&<span style={{padding:"1px 5px",borderRadius:10,background:"#dcfce7",color:"#166534",fontSize:10,fontWeight:900}}>TEG map</span>}
        {column.dtype&&<span style={{marginLeft:"auto",color:"var(--text-secondary)",fontSize:10}}>{column.dtype}</span>}
      </button>)}
    </div>}
  </div>;
}

function shotCoordinatePairs(columns){
  const lower=new Map(columns.map(c=>[c.toLowerCase(),c]));
  const bases=[["chip_x_pos","chip_y_pos"],["shot_x","shot_y"],["x_pos","y_pos"]];
  const pairs=[];
  for(const[xBase,yBase]of bases){
    for(const column of columns){
      const key=column.toLowerCase();
      if(key!==xBase&&!key.endsWith(`__${xBase}`))continue;
      const prefix=key.slice(0,key.length-xBase.length),y=lower.get(`${prefix}${yBase}`);
      if(y)pairs.push({x:column,y,label:`${column} + ${y}`});
    }
  }
  return pairs;
}

function radiusCoordinateMatcher(dataRows,xColumn,yColumn,layoutRows){
  const coordKey=(x,y)=>`${Number(x).toFixed(6)},${Number(y).toFixed(6)}`;
  const sourceMap=new Map();
  dataRows.slice(0,10000).forEach(row=>{
    const x=Number(row[xColumn]),y=Number(row[yColumn]);
    if(Number.isFinite(x)&&Number.isFinite(y))sourceMap.set(coordKey(x,y),{x,y});
  });
  const source=[...sourceMap.values()];
  const layout=(layoutRows||[]).map(row=>({x:Number(row.shot_x),y:Number(row.shot_y),radius:Number(row.radius)})).filter(row=>Number.isFinite(row.x)&&Number.isFinite(row.y)&&Number.isFinite(row.radius));
  const layoutMap=new Map(layout.map(row=>[coordKey(row.x,row.y),row]));
  const transforms=[
    {name:"identity",fn:(x,y)=>[x,y]}, {name:"rotate 90°",fn:(x,y)=>[-y,x]},
    {name:"rotate 180°",fn:(x,y)=>[-x,-y]}, {name:"rotate 270°",fn:(x,y)=>[y,-x]},
    {name:"mirror X",fn:(x,y)=>[-x,y]}, {name:"mirror Y",fn:(x,y)=>[x,-y]},
    {name:"swap XY",fn:(x,y)=>[y,x]}, {name:"swap XY mirror",fn:(x,y)=>[-y,-x]},
  ];
  let best={matched:-1,transform:transforms[0],dx:0,dy:0,rank:Number.MAX_SAFE_INTEGER};
  transforms.forEach((transform,transformIndex)=>{
    const offsets=new Map([["0.000000,0.000000",[0,0]]]);
    source.slice(0,10).forEach(point=>{
      const[tx,ty]=transform.fn(point.x,point.y);
      layout.forEach(target=>{
        const dx=target.x-tx,dy=target.y-ty;
        offsets.set(coordKey(dx,dy),[dx,dy]);
      });
    });
    offsets.forEach(([dx,dy])=>{
      let matched=0;
      source.forEach(point=>{
        const[tx,ty]=transform.fn(point.x,point.y);
        if(layoutMap.has(coordKey(tx+dx,ty+dy)))matched++;
      });
      const rank=transformIndex*1000000+Math.abs(dx)+Math.abs(dy);
      if(matched>best.matched||(matched===best.matched&&rank<best.rank))best={matched,transform,dx,dy,rank};
    });
  });
  return{
    sourceCount:source.length,
    matchedCount:Math.max(0,best.matched),
    description:`${best.transform.name} · offset (${Number(best.dx).toFixed(3)}, ${Number(best.dy).toFixed(3)})`,
    match(x,y){
      const[tx,ty]=best.transform.fn(Number(x),Number(y));
      return layoutMap.get(coordKey(tx+best.dx,ty+best.dy))||null;
    },
  };
}

function aggregateShot(values,method){
  const sorted=[...values].sort((a,b)=>a-b),n=sorted.length;
  if(!n)return NaN;
  if(method==="avg")return sorted.reduce((a,v)=>a+v,0)/n;
  if(method==="min")return sorted[0];
  if(method==="max")return sorted[n-1];
  const q=method==="p10"?0.1:method==="p90"?0.9:0.5,pos=(n-1)*q,lo=Math.floor(pos),hi=Math.ceil(pos);
  return sorted[lo]+(sorted[hi]-sorted[lo])*(pos-lo);
}

function linearFit(points){
  if(points.length<2)return null;
  const n=points.length,sx=points.reduce((a,p)=>a+p.x,0),sy=points.reduce((a,p)=>a+p.y,0);
  const sxx=points.reduce((a,p)=>a+p.x*p.x,0),syy=points.reduce((a,p)=>a+p.y*p.y,0),sxy=points.reduce((a,p)=>a+p.x*p.y,0);
  const den=n*sxx-sx*sx,corDen=Math.sqrt(Math.max(0,(n*sxx-sx*sx)*(n*syy-sy*sy)));
  if(!den||!corDen)return null;
  const slope=(n*sxy-sx*sy)/den,intercept=(sy-slope*sx)/n,corr=(n*sxy-sx*sy)/corDen;
  return{slope,intercept,r2:corr*corr,corr,equation:`y = ${slope.toFixed(4)}x ${intercept<0?"-":"+"} ${Math.abs(intercept).toFixed(4)}`};
}

function QueryCard({source,index,roots,autocompleteSource,onChange,onRemove,onClone}){
  const[products,setProducts]=useState([]);
  const[reformatterItems,setReformatterItems]=useState([]);
  const[reformatterBusy,setReformatterBusy]=useState(false);
  const[columnSearch,setColumnSearch]=useState("KNOB_");
  const[schemaColumns,setSchemaColumns]=useState([]);
  const[schemaDtypes,setSchemaDtypes]=useState({});
  const[schemaAssist,setSchemaAssist]=useState({});
  const[selectedColumns,setSelectedColumns]=useState([]);
  const[columnBusy,setColumnBusy]=useState(false);
  const isSplitTable=String(source.root||"").toUpperCase()==="SPLITTABLE";
  const isYieldShot=String(source.root||"").toUpperCase()==="YIELD_SHOT";
  const isEt=String(source.root||"").toUpperCase().includes("ET");
  const isInline=String(source.root||"").toUpperCase().includes("INLINE")&&!isSplitTable;
  useEffect(()=>{
    if(!source.root){setProducts([]);return;}
    let alive=true;
    sf(`/api/filebrowser/products?root=${encodeURIComponent(source.root)}&fast=true`).then(data=>{if(alive)setProducts(data.products||[]);}).catch(()=>{if(alive)setProducts([]);});
    return()=>{alive=false;};
  },[source.root]);
  useEffect(()=>{
    setSelectedColumns([]);
    setSchemaColumns([]);
    setSchemaDtypes({});setSchemaAssist({});
    setColumnSearch(isSplitTable?"KNOB_":"");
  },[source.root,source.product,isSplitTable]);
  useEffect(()=>{
    if(!source.root||!source.product){setSchemaColumns([]);setSchemaDtypes({});setSchemaAssist({});return undefined;}
    let alive=true;
    const timer=setTimeout(()=>{
      setColumnBusy(true);
      sf(`/api/filebrowser/columns/search?root=${encodeURIComponent(source.root)}&product=${encodeURIComponent(source.product)}&q=${encodeURIComponent(columnSearch)}&limit=500`)
        .then(data=>{if(alive){setSchemaColumns(data.columns||[]);setSchemaDtypes(data.dtypes||{});setSchemaAssist(data.assist||{});}})
        .catch(()=>{if(alive){setSchemaColumns([]);setSchemaDtypes({});setSchemaAssist({});}})
        .finally(()=>{if(alive)setColumnBusy(false);});
    },180);
    return()=>{alive=false;clearTimeout(timer);};
  },[source.root,source.product,columnSearch]);
  useEffect(()=>{
    if(!isEt||!source.product||!source.apply_reformatter){setReformatterItems([]);return undefined;}
    let alive=true;setReformatterBusy(true);
    sf(`/api/reformatize/items?product=${encodeURIComponent(source.product)}`)
      .then(data=>{if(alive)setReformatterItems(data.items||[]);})
      .catch(()=>{if(alive)setReformatterItems([]);})
      .finally(()=>{if(alive)setReformatterBusy(false);});
    return()=>{alive=false;};
  },[isEt,source.product,source.apply_reformatter]);
  const set=(key,value)=>onChange({...source,[key]:value});
  const toggleColumn=(column)=>setSelectedColumns(old=>old.includes(column)?old.filter(value=>value!==column):[...old,column]);
  const actualColumn=name=>schemaColumns.find(column=>column.toLowerCase()===name.toLowerCase())||((schemaAssist.virtual_columns||[]).find(column=>column.toLowerCase()===name.toLowerCase()))||"";
  const applyColumns=(requested=selectedColumns)=>{
    const keys=[actualColumn("root_lot_id"),actualColumn("wafer_id")].filter(Boolean);
    const columns=[...keys,...requested.filter(c=>!keys.some(key=>key.toLowerCase()===c.toLowerCase()))];
    set("sql",`SELECT ${columns.map(sqlIdentifier).join(", ")}`);
  };
  const applyRecipe=kind=>{
    const preferred=kind==="inline_map"
      ?["root_lot_id","wafer_id","step_id","process_id","item_id","subitem_id","value","shot_x","shot_y"]
      :kind==="trend"
        ?["root_lot_id","wafer_id","tkout_time","item_id","value"]
        :["root_lot_id","wafer_id","shot_x","shot_y","value"];
    const picked=[];
    preferred.forEach(name=>{const column=actualColumn(name);if(column&&!picked.includes(column))picked.push(column);});
    if(picked.length)applyColumns(picked);
  };
  return <div style={card}>
    <div style={{display:"flex",alignItems:"center",gap:8,marginBottom:10}}>
      <strong style={{fontSize:14,color:"var(--text-primary)"}}>Query {index+1}</strong>
      <input value={source.id} onChange={e=>set("id",e.target.value)} title="JOIN에서 사용할 query id" style={{...input,width:100,fontFamily:"monospace"}}/>
      <button type="button" onClick={onClone} style={{...btn,marginLeft:"auto"}}>복제</button>
      {onRemove&&<button type="button" onClick={onRemove} style={{...btn,color:"var(--danger)"}}>삭제</button>}
    </div>
    <div style={{display:"grid",gridTemplateColumns:"minmax(180px,1fr) minmax(180px,1fr)",gap:9}}>
      <label style={{fontSize:12,color:"var(--text-secondary)"}}>DB
        <select value={source.root} onChange={e=>set("root",e.target.value)} style={{...input,marginTop:4}}><option value="">선택</option>{roots.map(r=><option key={r.name} value={r.name}>{r.display_name||r.name}</option>)}</select>
      </label>
      <label style={{fontSize:12,color:"var(--text-secondary)"}}>Product
        <select value={source.product} onChange={e=>set("product",e.target.value)} style={{...input,marginTop:4}}><option value="">선택</option>{products.map(p=><option key={p.name} value={p.name}>{p.name}</option>)}</select>
      </label>
    </div>
    <div style={{display:"block",fontSize:12,color:"var(--text-secondary)",marginTop:9}}>SQL <span style={{fontWeight:400}}>— FROM은 선택한 DB/Product로 자동 지정됩니다.</span>
      <SqlColumnAutocomplete value={source.sql} onChange={value=>set("sql",value)} root={source.root||autocompleteSource?.root||""} product={source.product||autocompleteSource?.product||""} ariaLabel={`Query ${index+1} SQL`} rows={4} placeholder="SELECT root_lot_id, wafer_id, tkout_time, value WHERE item_id = 'CD1' ORDER BY tkout_time" style={{...input,marginTop:4,fontFamily:"'JetBrains Mono',monospace",resize:"vertical"}}/>
    </div>
    <label style={{display:"block",fontSize:12,color:"var(--text-secondary)",marginTop:9}}>선택 열(선택 사항)
      <input value={source.select_cols} onChange={e=>set("select_cols",e.target.value)} placeholder="root_lot_id, wafer_id, tkout_time, value" style={{...input,marginTop:4,fontFamily:"monospace"}}/>
    </label>
    {isYieldShot&&<div style={{marginTop:10,padding:10,border:"1px solid #86efac",borderRadius:7,background:"#f0fdf4",color:"#166534",fontSize:12,lineHeight:1.55}}>
      <b>Full Shot 수율 가상 DB</b> · WF MAP의 제품별 X/Y Scan 설정에서 완전한 shot만 가져옵니다.<br/>
      Corr/JOIN 권장 열: <code>root_lot_id, wafer_id, shot_x, shot_y, shot_yield</code><br/>
      예: <code>SELECT root_lot_id, wafer_id, shot_x, shot_y, shot_yield</code>
    </div>}
    {isInline&&<div style={{marginTop:10,padding:10,border:"1px solid #86efac",borderRadius:7,background:"#f0fdf4",color:"#166534",fontSize:12,lineHeight:1.55}}>
      <b>TEG Inline map 좌표 자동 보강</b> · 원본 <code>step_id + item_id + subitem_id</code>를 TEG 위치조회의 Inline map setting과 연결해 결과에 <code>shot_x/shot_y</code>를 붙입니다.<br/>
      {schemaAssist.inline_maps?.length
        ?`${schemaAssist.inline_maps.filter(row=>row.available).length}/${schemaAssist.inline_maps.length}개 연결 사용 가능 · ${schemaAssist.inline_maps.map(row=>`${row.item_id} → ${row.map_name}${row.available?"":" (map 없음)"}`).join(" · ")}`
        :"현재 제품에 연결 규칙이 없습니다. inline_shot_matching.csv 설정 후 좌표가 자동으로 활성화됩니다."}
      <div style={{display:"flex",gap:6,flexWrap:"wrap",marginTop:7}}>
        <button type="button" onClick={()=>applyRecipe("inline_map")} style={{...btn,padding:"5px 8px",background:"#fff",color:"#166534",borderColor:"#86efac"}}>Inline WF MAP 기본열 넣기</button>
        <button type="button" onClick={()=>applyRecipe("join")} style={{...btn,padding:"5px 8px",background:"#fff",color:"#166534",borderColor:"#86efac"}}>Shot Corr/JOIN 기본열 넣기</button>
      </div>
    </div>}
    {/* 시간 창 — 저장 코드(RECENT_DAYS)의 기본값이며 Report 실행 컨텍스트에서 일괄 변경할 수 있다. */}
    <div style={{display:"grid",gridTemplateColumns:"minmax(120px,1fr) minmax(160px,1.4fr)",gap:9,marginTop:9}}>
      <label style={{fontSize:12,color:"var(--text-secondary)"}}>최근 일수 <span style={{fontWeight:400}}>· 비우면 전체</span>
        <input aria-label={`Query ${index+1} 최근 일수`} type="number" min="1" max="3650" value={source.runtime_recent_days??""} onChange={e=>set("runtime_recent_days",e.target.value)} placeholder="전체 기간" style={{...input,marginTop:4}}/>
      </label>
      <label style={{fontSize:12,color:"var(--text-secondary)"}}>시간 열
        <input aria-label={`Query ${index+1} 시간 열`} value={source.runtime_date_column??""} onChange={e=>set("runtime_date_column",e.target.value)} placeholder={DEFAULT_DATE_COLUMN} disabled={!recentDaysValue(source)} style={{...input,marginTop:4,fontFamily:"monospace",opacity:recentDaysValue(source)?1:.55}}/>
      </label>
      <div style={{gridColumn:"1 / -1",fontSize:11,color:"var(--text-secondary)"}}>
        {recentDaysValue(source)
          ?`${text(source.runtime_date_column).trim()||DEFAULT_DATE_COLUMN} 기준 최근 ${recentDaysValue(source)}일만 조회합니다. 저장 코드의 기본 기간으로 사용됩니다.`
          :"기간 제한 없이 조회합니다. 필요하면 Template Report 실행 시 모든 차트 기간을 한꺼번에 지정할 수 있습니다."}
      </div>
    </div>
    <details style={{marginTop:10,border:"1px solid var(--border)",borderRadius:7,background:"var(--bg-primary)",overflow:"hidden"}}>
      <summary style={{cursor:"pointer",padding:"9px 10px",fontSize:12,fontWeight:900,userSelect:"none"}}>특정 값 필터 · 여러 열 합치기</summary>
      <div style={{padding:"10px",borderTop:"1px solid var(--border)",display:"grid",gap:9}}>
        <div style={{fontSize:11,lineHeight:1.55,color:"var(--text-secondary)"}}>엑셀에서 여러 셀을 그대로 붙여 넣을 수 있습니다. 파생열을 먼저 만든 뒤 필터하므로 <code>root_lot_id + wafer_id → lot_wafer</code>처럼 합친 값도 바로 거를 수 있습니다.</div>
        <strong style={{fontSize:12}}>여러 열 합치기</strong>
        <SpreadsheetPasteGrid
          ariaLabel={`Query ${index+1} 파생열`}
          columns={DERIVED_GRID_COLUMNS}
          rows={source.derived_columns||normalizeDerivedRows([])}
          onChange={rows=>set("derived_columns",rows)}
          columnLabels={{name:"새 열 이름",columns:"합칠 열 · 쉼표",separator:"구분자"}}
          placeholders={{name:"lot_wafer",columns:"root_lot_id, wafer_id",separator:"_"}}
          minRows={4}
          maxRows={DERIVED_GRID_MAX_ROWS}
          maxHeight={215}
          minTableWidth={520}
        />
        <strong style={{fontSize:12}}>값 필터</strong>
        <SpreadsheetPasteGrid
          ariaLabel={`Query ${index+1} 값 필터`}
          columns={FILTER_GRID_COLUMNS}
          rows={source.runtime_filters||normalizeFilterRows([])}
          onChange={rows=>set("runtime_filters",rows)}
          columnLabels={{column:"필터 열",operator:"조건",values:"값 · 쉼표"}}
          placeholders={{column:"lot_wafer",operator:"in",values:"A1234_1, A1234_2"}}
          minRows={4}
          maxRows={FILTER_GRID_MAX_ROWS}
          maxHeight={215}
          minTableWidth={560}
        />
        <div style={{fontSize:10,lineHeight:1.55,color:"var(--text-secondary)"}}>조건: <code>in</code>, <code>not_in</code>, <code>equals</code>, <code>not_equals</code>, <code>contains</code>, <code>not_contains</code>, <code>is_blank</code>, <code>not_blank</code>. 여러 필터 행은 모두 만족해야 합니다.</div>
      </div>
    </details>
    {isEt&&<div style={{marginTop:10,padding:10,border:"1px solid var(--border)",borderRadius:7,background:"var(--bg-primary)"}}>
      <label style={{display:"flex",alignItems:"center",gap:7,fontSize:12,fontWeight:800,cursor:"pointer"}}>
        <input type="checkbox" checked={Boolean(source.apply_reformatter)} onChange={e=>set("apply_reformatter",e.target.checked)}/>
        ET 다운로드 Reformatter 적용 (REAL / ADDP)
      </label>
      {source.apply_reformatter&&<>
        <label style={{display:"block",fontSize:12,color:"var(--text-secondary)",marginTop:8}}>계산 Item alias
          <input list={`chart-reformatter-items-${index}`} value={source.reformatter_items||""} onChange={e=>set("reformatter_items",e.target.value)} placeholder="VTH_INDEX, ET_VALUE_2" style={{...input,marginTop:4,fontFamily:"monospace"}}/>
          <datalist id={`chart-reformatter-items-${index}`}>{reformatterItems.map(item=><option key={item.alias} value={item.alias}>{item.category?.toUpperCase()}</option>)}</datalist>
        </label>
        <div style={{fontSize:11,color:"var(--text-secondary)",marginTop:6}}>{reformatterBusy?"Reformatter item 조회 중…":`${reformatterItems.length}개 alias · 여러 개는 쉼표로 구분합니다. 비우면 SQL SELECT의 alias를 자동 감지합니다.`}</div>
        {!!reformatterItems.length&&<div style={{display:"flex",gap:5,flexWrap:"wrap",marginTop:7,maxHeight:82,overflow:"auto"}}>{reformatterItems.slice(0,80).map(item=><button type="button" key={item.alias} onClick={()=>{const current=text(source.reformatter_items).split(",").map(v=>v.trim()).filter(Boolean);if(!current.includes(item.alias))set("reformatter_items",[...current,item.alias].join(", "));}} style={{...btn,padding:"3px 6px",fontSize:10}}>{item.category?.toUpperCase()} · {item.alias}</button>)}</div>}
      </>}
    </div>}
    {source.product&&<div style={{marginTop:10,padding:10,border:"1px solid var(--border)",borderRadius:7,background:"var(--bg-primary)"}}>
      <div style={{display:"flex",alignItems:"center",gap:7,flexWrap:"wrap"}}>
        <strong style={{fontSize:12}}>열 도우미</strong>
        {isSplitTable&&["KNOB_","FAB_","MASK_","INLINE_","VM_",""] .map(prefix=><button type="button" key={prefix||"all"} onClick={()=>setColumnSearch(prefix)} style={{...btn,padding:"4px 7px",fontSize:11,background:columnSearch===prefix?"var(--accent)":"var(--bg-tertiary)",color:columnSearch===prefix?"#fff":"var(--text-primary)"}}>{prefix||"전체"}</button>)}
        <button type="button" onClick={()=>applyRecipe("trend")} style={{...btn,padding:"4px 7px",fontSize:11}}>Trend 기본열</button>
        <button type="button" onClick={()=>applyRecipe("join")} style={{...btn,padding:"4px 7px",fontSize:11}}>Corr/JOIN 기본열</button>
        <input aria-label={`Query ${index+1} 열 검색`} value={columnSearch} onChange={e=>setColumnSearch(e.target.value)} placeholder="열 이름 검색" style={{...input,width:210,marginLeft:"auto"}}/>
      </div>
      <div style={{display:"flex",gap:5,flexWrap:"wrap",marginTop:8,maxHeight:120,overflow:"auto"}}>
        {columnBusy?<span style={{fontSize:12,color:"var(--text-secondary)"}}>열 조회 중…</span>:schemaColumns.length?schemaColumns.map(column=><button type="button" key={column} title={`${column}${schemaDtypes[column]?` · ${schemaDtypes[column]}`:""}`} onClick={()=>toggleColumn(column)} style={{...btn,padding:"4px 7px",fontSize:11,maxWidth:300,overflow:"hidden",textOverflow:"ellipsis",whiteSpace:"nowrap",background:selectedColumns.includes(column)?"var(--accent-glow)":"var(--bg-tertiary)",borderColor:selectedColumns.includes(column)?"var(--accent)":"var(--border)"}}>{selectedColumns.includes(column)?"✓ ":"＋ "}{column}{schemaAssist.virtual_columns?.includes(column)?" · TEG map":""}</button>):<span style={{fontSize:12,color:"var(--text-secondary)"}}>일치하는 열이 없습니다.</span>}
      </div>
      <div style={{display:"flex",alignItems:"center",gap:8,marginTop:8}}>
        <span style={{fontSize:11,color:"var(--text-secondary)",flex:1}}>선택 {selectedColumns.length}개 · Root Lot/Wafer 열이 있으면 자동 포함됩니다. SQL에서 열 이름 한 글자를 입력하고 <b>Tab</b>을 누르면 자동완성됩니다.</span>
        <button type="button" disabled={!selectedColumns.length} onClick={()=>applyColumns()} style={{...btn,padding:"5px 9px"}}>선택 열 SQL 반영</button>
      </div>
    </div>}
  </div>;
}

function DataTable({columns,rows,title}){
  if(!columns.length)return null;
  return <div style={{...card,padding:0,overflow:"hidden"}}>
    <div style={{padding:"10px 12px",fontSize:14,fontWeight:900,borderBottom:"1px solid var(--border)"}}>{title}</div>
    <div style={{overflow:"auto",maxHeight:390}}><table style={{borderCollapse:"collapse",width:"100%",fontSize:12,whiteSpace:"nowrap"}}>
      <thead style={{position:"sticky",top:0,background:"var(--bg-tertiary)",zIndex:1}}><tr>{columns.map(c=><th key={c} style={{padding:"7px 9px",textAlign:"left",borderBottom:"1px solid var(--border)"}}>{c}</th>)}</tr></thead>
      <tbody>{rows.map((row,i)=><tr key={i}>{columns.map(c=><td key={c} style={{padding:"6px 9px",borderBottom:"1px solid var(--border)",color:"var(--text-secondary)"}}>{text(row[c])}</td>)}</tr>)}</tbody>
    </table></div>
  </div>;
}

function TrellisPlot({chart,column,enableHighlight=false}){
  const grouped=useMemo(()=>{
    const buckets=new Map();
    (chart?.points||[]).forEach(point=>{
      const value=text(point?.trellis_value).trim()||"missing";
      if(!buckets.has(value))buckets.set(value,[]);
      buckets.get(value).push(point);
    });
    return[...buckets.entries()].sort(([a],[b])=>a.localeCompare(b,undefined,{numeric:true}));
  },[chart]);
  const shown=grouped.slice(0,12);
  // Trend 패널은 시간축이라 가로로 길어야 하고, Corr 패널은 정사각에 가까워야 한다.
  const panelKind=chart?.trend_grain?"panel_wide":"panel";
  return <div>
    <div style={{fontSize:12,color:"#475569",margin:"0 0 9px"}}>Trellis: {column} · {grouped.length}개 패널{grouped.length>12?" (앞 12개 표시)":""}</div>
    <div style={{display:"grid",gridTemplateColumns:chart?.width?"1fr":"repeat(auto-fit,minmax(420px,1fr))",gap:12}}>
      {shown.map(([value,points],index)=>{
        const panelFit=chart?.fit&&!chart?.trend_grain?linearFit(points.map(point=>({x:Number(point.x),y:Number(point.y)})).filter(point=>Number.isFinite(point.x)&&Number.isFinite(point.y))):null;
        const panel={...chart,title:`${column}: ${value}`,points,fit:panelFit,corr:panelFit?.corr,use_svg:true,compact:true,hide_title:true,emphasize_axes:true};
        const header=TRELLIS_HEADERS[index%TRELLIS_HEADERS.length];
        // 머리글은 "무엇으로 나뉘었나"만 말한다 — 패널마다 붙던 point 수는 비교에 방해가 됐다.
        return <div key={value} style={{width:chart?.width?`min(100%, ${chart.width}px)`:"auto",margin:chart?.width?"0 auto":0,border:"1px solid #cbd5e1",borderRadius:8,overflow:"hidden"}}><div style={{padding:"9px 12px",fontSize:14,fontWeight:900,textAlign:"center",color:"#0f172a",background:header.background,borderBottom:`3px solid ${header.border}`}}>{column}: {value}</div><FlowPlotlyChart chart={panel} cfg={panel} layoutKind={panelKind} dark={false} enableHighlight={enableHighlight}/></div>;
      })}
    </div>
  </div>;
}

export default function My_ChartBuilder({user}){
  const[roots,setRoots]=useState([]);
  const[sources,setSources]=useState([newSource(1)]);
  const[joins,setJoins]=useState([]);
  const[maxRows,setMaxRows]=useState(10000);
  const[definitionCode,setDefinitionCode]=useState(DEFINITION_EXAMPLE);
  const[chartName,setChartName]=useState("");
  const[codeBusy,setCodeBusy]=useState(false);
  const[history,setHistory]=useState([]);
  const[historySearch,setHistorySearch]=useState("");
  const[historyBusy,setHistoryBusy]=useState(false);
  const[pinBusy,setPinBusy]=useState("");
  const[result,setResult]=useState(null);
  const[busy,setBusy]=useState(false);
  const[chartType,setChartType]=useState("scatter");
  const[xCol,setXCol]=useState("");
  const[yCol,setYCol]=useState("");
  const[mapYCol,setMapYCol]=useState("");
  const[mapScope,setMapScope]=useState("root_wafer");
  const[mapTarget,setMapTarget]=useState("");
  const[mapAggregation,setMapAggregation]=useState("median");
  const[trendGrain,setTrendGrain]=useState("shot");
  const[trendAggregation,setTrendAggregation]=useState("median");
  const[barAggregation,setBarAggregation]=useState("median");
  const[radiusAggregation,setRadiusAggregation]=useState("raw");
  const[radiusFitMode,setRadiusFitMode]=useState("cubic");
  const[corrFitMode,setCorrFitMode]=useState("linear");
  const[pieBasis,setPieBasis]=useState("count");
  const[showBoxStats,setShowBoxStats]=useState(true);
  // 통계표 열을 상자 바로 아래에 맞추려면 plotly 가 최종적으로 잡은 그림 영역이 필요하다.
  const[boxGeometry,setBoxGeometry]=useState(null);
  const[radiusLayout,setRadiusLayout]=useState(null);
  const[radiusError,setRadiusError]=useState("");
  const[radiusBusy,setRadiusBusy]=useState(false);
  const[colorCol,setColorCol]=useState("");
  const[trellisCol,setTrellisCol]=useState("");
  const[customColorRules,setCustomColorRules]=useState("");
  const[customColorElse,setCustomColorElse]=useState("gray");
  const[colorListRows,setColorListRows]=useState(()=>normalizeColorListRows([]));
  const[highlightEnabled,setHighlightEnabled]=useState(true);
  const[showLegend,setShowLegend]=useState(true);
  const[chartWidth,setChartWidth]=useState("");
  const[chartHeight,setChartHeight]=useState("");
  const[chartTitle,setChartTitle]=useState("");
  const[xAxisLabel,setXAxisLabel]=useState("");
  const[yAxisLabel,setYAxisLabel]=useState("");
  const[pointSize,setPointSize]=useState("9");
  const[markerOpacity,setMarkerOpacity]=useState("0.82");
  const[lineWidth,setLineWidth]=useState("2.3");
  const[yMin,setYMin]=useState("");
  const[yMax,setYMax]=useState("");
  const[yScale,setYScale]=useState("linear");
  const[showGrid,setShowGrid]=useState(true);
  const[legendPosition,setLegendPosition]=useState("bottom");
  const[specLowCol,setSpecLowCol]=useState("");
  const[specHighCol,setSpecHighCol]=useState("");
  const[boxPoints,setBoxPoints]=useState("outliers");
  const[waferPalette,setWaferPalette]=useState("blue_gray_red");
  const[waferLow,setWaferLow]=useState("");
  const[waferCenter,setWaferCenter]=useState("");
  const[waferHigh,setWaferHigh]=useState("");
  const[assistantPrompt,setAssistantPrompt]=useState("");
  const[assistantBusy,setAssistantBusy]=useState(false);
  const[assistantReply,setAssistantReply]=useState(null);
  useEffect(()=>{sf("/api/filebrowser/roots?fast=true").then(d=>setRoots([
    ...(d.roots||[]),
    {name:"YIELD_SHOT",display_name:"WF MAP · Full Shot",granularity:"shot",structure:"virtual"},
  ])).catch(e=>toast.error(e.message));},[]);
  const loadHistory=(query=historySearch)=>{
    setHistoryBusy(true);
    return sf(`/api/filebrowser/chart-builder/history?limit=500&q=${encodeURIComponent(text(query).trim())}`)
      .then(data=>setHistory(data.history||[]))
      .catch(error=>toast.error(`코드 히스토리 조회 실패: ${error.message||error}`))
      .finally(()=>setHistoryBusy(false));
  };
  useEffect(()=>{const timer=window.setTimeout(()=>loadHistory(historySearch),250);return()=>window.clearTimeout(timer);},[historySearch]);
  const ids=sources.map(s=>s.id).filter(Boolean);
  const joined=result?.joined||{};
  const columns=Array.isArray(joined.columns)?joined.columns:[];
  const rows=Array.isArray(joined.rows)?joined.rows:[];
  const colorListText=useMemo(()=>colorListTextFromRows(colorListRows),[colorListRows]);
  const colorListPreview=useMemo(()=>parseChartColorList(colorListText),[colorListText]);
  const linkedColorRuleLines=useMemo(()=>colorListPreview.errors.length?[]:chartColorListRules(colorListPreview.rows),[colorListPreview]);
  const formulaColorRuleLines=useMemo(()=>text(customColorRules).split(/\r?\n/).map(rule=>rule.trim()).filter(Boolean),[customColorRules]);
  const combinedColorRuleLines=useMemo(()=>[...linkedColorRuleLines,...formulaColorRuleLines],[linkedColorRuleLines,formulaColorRuleLines]);
  const numericCols=useMemo(()=>columns.filter(c=>rows.slice(0,80).some(r=>text(r[c]).trim()!==""&&Number.isFinite(Number(r[c])))),[columns,rows]);
  const shotPairs=useMemo(()=>shotCoordinatePairs(columns),[columns]);
  const radiusSource=useMemo(()=>{
    const sourcesOut=result?.sources||[];
    return sourcesOut.find(source=>Array.isArray(source.columns)&&source.columns.includes(xCol)&&source.columns.includes(mapYCol))
      ||sourcesOut.find(source=>Array.isArray(source.columns)&&shotPairs.some(pair=>source.columns.includes(pair.x)&&source.columns.includes(pair.y)))
      ||sourcesOut.find(source=>source.product)||null;
  },[result,xCol,mapYCol,shotPairs]);
  const radiusProduct=radiusSource?.inline_coordinate_mapping?.vehicles?.[0]||radiusSource?.product||"";
  useEffect(()=>{
    if(!["radius","wafer_map"].includes(chartType)||!radiusProduct){setRadiusLayout(null);setRadiusError("");setRadiusBusy(false);return undefined;}
    let alive=true;setRadiusBusy(true);setRadiusError("");
    sf(`/api/filebrowser/chart-builder/radius-layout?product=${encodeURIComponent(radiusProduct)}`)
      .then(data=>{if(alive)setRadiusLayout(data);})
      .catch(error=>{if(alive){setRadiusLayout(null);setRadiusError(error.message||String(error));}})
      .finally(()=>{if(alive)setRadiusBusy(false);});
    return()=>{alive=false;};
  },[chartType,radiusProduct]);
  const radiusMatcher=useMemo(()=>{
    if(!["radius","wafer_map"].includes(chartType)||!xCol||!mapYCol||!radiusLayout?.rows?.length)return null;
    return radiusCoordinateMatcher(rows,xCol,mapYCol,radiusLayout.rows);
  },[chartType,rows,xCol,mapYCol,radiusLayout]);
  const rootLotCol=useMemo(()=>columns.find(c=>c.toLowerCase()==="root_lot_id")||columns.find(c=>c.toLowerCase().endsWith("__root_lot_id"))||"",[columns]);
  const waferCol=useMemo(()=>columns.find(c=>c.toLowerCase()==="wafer_id")||columns.find(c=>c.toLowerCase().endsWith("__wafer_id"))||"",[columns]);
  const mapGroups=useMemo(()=>{
    const waferMode=["root_wafer","trellis_wafer","trellis_root_wafer"].includes(mapScope);
    if((mapScope!=="trellis_wafer"&&!rootLotCol)||(waferMode&&!waferCol))return[];
    const found=new Map();
    rows.forEach(r=>{
      const lot=rootLotCol?text(r[rootLotCol]):"",wafer=waferMode?text(r[waferCol]):"";
      if((mapScope!=="trellis_wafer"&&!lot)||(waferMode&&!wafer))return;
      const key=mapScope==="trellis_wafer"?wafer:`${lot}|${wafer}`;
      const label=mapScope==="root_lot"?lot:mapScope==="trellis_wafer"?`W${wafer}`:`${lot} | W${wafer}`;
      if(!found.has(key))found.set(key,{key,lot,wafer,label});
    });
    return[...found.values()].sort((a,b)=>a.label.localeCompare(b.label,undefined,{numeric:true}));
  },[rows,mapScope,rootLotCol,waferCol]);
  useEffect(()=>{
    if(columns.length&&!xCol)setXCol(columns.find(c=>c==="tkout_time")||columns[0]);
    if(numericCols.length&&!yCol)setYCol(numericCols.find(c=>c==="value"||c==="y")||numericCols[0]);
    if(shotPairs.length&&!mapYCol)setMapYCol(shotPairs[0].y);
  },[columns,numericCols,shotPairs,xCol,yCol,mapYCol]);
  useEffect(()=>{
    if(!["wafer_map","radius"].includes(chartType)||!columns.length)return;
    const shotX=shotPairs[0]?.x;
    const shotY=shotPairs[0]?.y;
    const value=columns.find(c=>["value","item_value","measurement_value"].includes(c.toLowerCase()));
    if(shotX)setXCol(shotX);
    if(shotY)setMapYCol(shotY);
    if(value)setYCol(value);
  },[chartType,columns,shotPairs]);
  useEffect(()=>{if(mapGroups.length&&!mapGroups.some(g=>g.key===mapTarget))setMapTarget(mapGroups[0].key);},[mapGroups,mapTarget]);
  const resolveSourceRoot=source=>{
    const requested=text(source?.root).trim().toLowerCase();
    const match=roots.find(root=>[root.name,root.display_name,root.canonical].some(value=>text(value).trim().toLowerCase()===requested));
    return match?{...source,root:match.name}:source;
  };
  const resolveDefinitionAutocomplete=(value,caret)=>{
    const context=definitionSqlAutocompleteContext(value,caret);
    if(!context)return null;
    return{...context,root:resolveSourceRoot({root:context.root}).root};
  };
  const definitionAutocompleteSources=useMemo(()=>definitionQueryAutocompleteContexts(definitionCode).map(context=>{
    const requested=text(context.root).trim().toLowerCase();
    const match=roots.find(root=>[root.name,root.display_name,root.canonical].some(value=>text(value).trim().toLowerCase()===requested));
    return{...context,root:match?.name||context.root};
  }),[definitionCode,roots]);
  const queryAutocompleteSource=(source,index)=>{
    const id=text(source?.id).trim().toLowerCase();
    return definitionAutocompleteSources.find(context=>text(context.id).trim().toLowerCase()===id)
      ||definitionAutocompleteSources[index]
      ||null;
  };
  // 시간 창은 폼에서는 빈칸(=전체 기간)이고 요청·코드에서는 숫자다. 형태만 여기서 맞춘다.
  const formSource=source=>({...source,runtime_recent_days:recentDaysValue(source)||"",runtime_date_column:text(source?.runtime_date_column),derived_columns:normalizeDerivedRows(source?.derived_columns),runtime_filters:normalizeFilterRows(source?.runtime_filters)});
  const requestSource=(source,linkedRows=[])=>{
    const resolved=resolveSourceRoot(source),days=recentDaysValue(resolved);
    const pairs=(linkedRows||[]).map(row=>({root_lot_id:text(row.root_lot_id).trim(),wafer_id:text(row.wafer_id).trim()})).filter(row=>row.root_lot_id&&row.wafer_id);
    const roots=pairs.length?listValues(pairs.map(row=>row.root_lot_id)):listValues(resolved.runtime_root_lot_ids||[]);
    const wafers=pairs.length?listValues(pairs.map(row=>row.wafer_id)):listValues(resolved.runtime_wafer_ids||[]);
    return{...resolved,runtime_recent_days:days,runtime_date_column:days?(text(resolved.runtime_date_column).trim()||DEFAULT_DATE_COLUMN):"",runtime_root_lot_ids:roots,runtime_wafer_ids:wafers,runtime_lot_wafer_pairs:pairs,derived_columns:cleanDerivedColumns(resolved.derived_columns),runtime_filters:cleanRuntimeFilters(resolved.runtime_filters)};
  };
  const currentChartConfig=()=>({
    type:chartType,
    title:text(chartTitle).trim(),
    x:xCol,
    y:yCol,
    x_label:text(xAxisLabel).trim(),
    y_label:text(yAxisLabel).trim(),
    color:colorCol==="__custom__"?"custom":colorCol,
    trellis:trellisCol,
    trend_grain:chartType==="line"?trendGrain:"",
    aggregation:chartType==="wafer_map"?mapAggregation:chartType==="line"?trendAggregation:chartType.startsWith("bar")?barAggregation:chartType==="radius"?radiusAggregation:"",
    map_y:chartType==="wafer_map"?mapYCol:"",
    map_scope:chartType==="wafer_map"?mapScope:"",
    map_target:chartType==="wafer_map"?mapTarget:"",
    pie_basis:isPie?pieBasis:"",
    fit:chartType==="scatter"?corrFitMode:chartType==="radius"?radiusFitMode:"",
    point_size:Number(pointSize)||9,
    marker_opacity:Number(markerOpacity)||0.82,
    line_width:Number(lineWidth)||2.3,
    y_min:text(yMin).trim(),
    y_max:text(yMax).trim(),
    y_scale:yScale,
    show_grid:showGrid,
    legend_position:legendPosition,
    spec_low:specLowCol,
    spec_high:specHighCol,
    box_points:boxPoints,
    wafer_palette:waferPalette,
    wafer_low:text(waferLow).trim(),
    wafer_center:text(waferCenter).trim(),
    wafer_high:text(waferHigh).trim(),
    color_rules:combinedColorRuleLines,
    color_else:customColorElse,
    highlight:highlightEnabled,
    show_legend:showLegend,
    width:text(chartWidth).trim()?Number(chartWidth):"",
    height:text(chartHeight).trim()?Number(chartHeight):"",
  });
  const applyChartConfig=(config={})=>{
    const hasConfig=config&&Object.keys(config).length>0;
    const separated=partitionChartColorRules(hasConfig?(config.color_rules||[]):[]);
    setChartType(hasConfig&&config.type?text(config.type).toLowerCase():"scatter");
    setChartTitle(hasConfig?text(config.title):"");
    setXCol(hasConfig?text(config.x):"");
    setYCol(hasConfig?text(config.y):"");
    setXAxisLabel(hasConfig?text(config.x_label):"");
    setYAxisLabel(hasConfig?text(config.y_label):"");
    setColorCol(hasConfig&&["custom","__custom__"].includes(text(config.color).toLowerCase())?"__custom__":hasConfig?text(config.color):"");
    setTrellisCol(hasConfig?text(config.trellis):"");
    setCustomColorRules(separated.formulaRules.join("\n"));
    setColorListRows(colorListRowsFromText(hasConfig?chartColorListTextFromRules(config.color_rules||[]):""));
    setCustomColorElse(hasConfig?text(config.color_else||"gray"):"gray");
    setHighlightEnabled(hasConfig?config.highlight!==false:true);
    setShowLegend(hasConfig?config.show_legend!==false:true);
    setTrendGrain(hasConfig&&config.trend_grain?text(config.trend_grain):"shot");
    const aggregation=hasConfig&&config.aggregation?text(config.aggregation):"median";
    setTrendAggregation(aggregation);setBarAggregation(aggregation);setMapAggregation(aggregation);setRadiusAggregation(aggregation==="raw"?"raw":"median");
    setMapYCol(hasConfig?text(config.map_y):"");setMapScope(hasConfig&&config.map_scope?text(config.map_scope):"root_wafer");setMapTarget(hasConfig?text(config.map_target):"");
    setPieBasis(hasConfig&&config.pie_basis?text(config.pie_basis):"count");
    setCorrFitMode(hasConfig&&config.fit?text(config.fit):"linear");setRadiusFitMode(hasConfig&&config.fit?text(config.fit):"cubic");
    setPointSize(hasConfig&&config.point_size?text(config.point_size):"9");setMarkerOpacity(hasConfig&&config.marker_opacity!=null?text(config.marker_opacity):"0.82");setLineWidth(hasConfig&&config.line_width?text(config.line_width):"2.3");
    setYMin(hasConfig&&config.y_min!=null?text(config.y_min):"");setYMax(hasConfig&&config.y_max!=null?text(config.y_max):"");setYScale(hasConfig&&config.y_scale?text(config.y_scale):"linear");
    setShowGrid(hasConfig?config.show_grid!==false:true);setLegendPosition(hasConfig&&config.legend_position?text(config.legend_position):"bottom");
    setSpecLowCol(hasConfig?text(config.spec_low):"");setSpecHighCol(hasConfig?text(config.spec_high):"");setBoxPoints(hasConfig&&config.box_points?text(config.box_points):"outliers");
    setWaferPalette(hasConfig&&config.wafer_palette?text(config.wafer_palette):"blue_gray_red");setWaferLow(hasConfig&&config.wafer_low!=null?text(config.wafer_low):"");setWaferCenter(hasConfig&&config.wafer_center!=null?text(config.wafer_center):"");setWaferHigh(hasConfig&&config.wafer_high!=null?text(config.wafer_high):"");
    setChartWidth(hasConfig&&config.width?text(config.width):"");
    setChartHeight(hasConfig&&config.height?text(config.height):"");
  };
  const run=async(config=null,options={})=>{
    const activeChart=config?.chart??currentChartConfig();
    const linkedRows=partitionChartColorRules(activeChart?.color_rules||[]).rows;
    const activeSources=(config?.sources||sources).map(source=>requestSource(source,linkedRows));
    const activeJoins=activeSources.length>1?(config?.joins||joins):[];
    const activeMaxRows=config?.max_rows??maxRows;
    if(!config&&text(colorListText).trim()&&colorListPreview.errors.length){toast.error(colorListPreview.errors[0]);return;}
    if(activeSources.some(s=>!s.root||!s.product)){toast.error("각 Query의 DB와 Product를 선택해 주세요.");return;}
    const requestedWidth=Number(activeChart?.width||0),requestedHeight=Number(activeChart?.height||0);
    if(requestedWidth&&(requestedWidth<320||requestedWidth>2400)){toast.error("차트 Width는 320~2400px 사이로 입력해 주세요.");return;}
    if(requestedHeight&&(requestedHeight<240||requestedHeight>1600)){toast.error("차트 Height는 240~1600px 사이로 입력해 주세요.");return;}
    const requestedPointSize=Number(activeChart?.point_size||0),requestedOpacity=Number(activeChart?.marker_opacity||0),requestedLineWidth=Number(activeChart?.line_width||0);
    if(requestedPointSize<2||requestedPointSize>30){toast.error("Point 크기는 2~30 사이로 입력해 주세요.");return;}
    if(requestedOpacity<0.05||requestedOpacity>1){toast.error("Marker 투명도는 0.05~1 사이로 입력해 주세요.");return;}
    if(requestedLineWidth<0.5||requestedLineWidth>8){toast.error("Line 굵기는 0.5~8 사이로 입력해 주세요.");return;}
    if(text(activeChart?.y_min).trim()&&text(activeChart?.y_max).trim()&&Number(activeChart.y_min)>=Number(activeChart.y_max)){toast.error("Y축 최소값은 최대값보다 작아야 합니다.");return;}
    if(activeChart?.y_scale==="log"&&[activeChart?.y_min,activeChart?.y_max].some(value=>text(value).trim()&&Number(value)<=0)){toast.error("Log scale의 Y축 최소·최대는 0보다 커야 합니다.");return;}
    const colorErrors=text(activeChart?.color).toLowerCase()==="custom"?parseChartColorRules(activeChart.color_rules||[]).filter(rule=>rule.error):[];
    if(colorErrors.length){toast.error(`Custom Color ${colorErrors[0].error}`);return;}
    const canonical=config?.canonical_code||definitionFromForm(activeSources,activeJoins,activeMaxRows,activeChart);
    setDefinitionCode(canonical);
    setBusy(true);
    try{
      const out=await postJson("/api/filebrowser/chart-builder/run",{sources:activeSources,joins:activeJoins,max_rows:Number(activeMaxRows)||10000,chart:activeChart,chart_name:text(chartName).trim(),save_history:options.saveHistory!==false});
      setResult(out);applyChartConfig(activeChart);
      const mapSource=(out.sources||[]).find(s=>String(s.root||"").toUpperCase().includes("INLINE"));
      setMapAggregation(mapSource?"avg":"median");
      setTrendAggregation(mapSource?"avg":"median");
      setBarAggregation(mapSource?"avg":"median");
      setRadiusAggregation("raw");
      const saved=out.saved_chart;
      toast.ok(saved?`'${saved.name}' 저장 완료 · ${saved.id}`:`JOIN 결과 ${Number(out.joined?.row_count||0).toLocaleString()}행`);
      loadHistory(historySearch);
    }catch(e){toast.error(e.message||String(e));}
    finally{setBusy(false);}
  };
  const applyDefinition=async(execute=false,code=definitionCode)=>{
    if(!text(code).trim()){toast.error("전체 코드를 입력해 주세요.");return;}
    setCodeBusy(true);
    try{
      const parsed=await postJson("/api/filebrowser/chart-builder/parse",{code});
      setSources((parsed.sources||[newSource(1)]).map(source=>formSource(resolveSourceRoot(source))));
      setJoins(parsed.joins||[]);
      setMaxRows(parsed.max_rows||10000);
      setDefinitionCode(parsed.canonical_code||code);
      setResult(null);
      applyChartConfig(parsed.chart||{});
      if(execute)await run(parsed);
      else toast.ok("전체 코드를 아래 Query/JOIN 폼에 적용했습니다.");
    }catch(error){toast.error(error.message||String(error));}
    finally{setCodeBusy(false);}
  };
  // Template Report 슬롯에서 "차트생성에서 수정"을 누르면 해당 슬롯의 실제 실행
  // 코드를 그대로 받아 폼으로 연다. sessionStorage는 탭 전환 중 컴포넌트가 아직
  // 마운트되지 않은 경우를, custom event는 이미 열린 화면을 각각 처리한다.
  useEffect(()=>{
    const consume=event=>{
      let payload=event?.detail||null;
      if(!payload){
        try{payload=JSON.parse(window.sessionStorage.getItem(CHART_BUILDER_TRANSFER_KEY)||"null");}catch(_error){payload=null;}
      }
      const code=text(payload?.definition_code);
      if(!code.trim())return;
      try{window.sessionStorage.removeItem(CHART_BUILDER_TRANSFER_KEY);}catch(_error){}
      setChartName(text(payload?.chart_name));
      setDefinitionCode(code);
      applyDefinition(false,code);
      window.setTimeout(()=>document.getElementById("chart-builder-code")?.scrollIntoView({behavior:"smooth",block:"start"}),80);
      toast.ok("Template의 차트 생성식을 불러왔습니다. 수정 후 실행·저장하면 다시 사용할 수 있습니다.");
    };
    consume();
    window.addEventListener("flow:chartbuilder-load-code",consume);
    return()=>window.removeEventListener("flow:chartbuilder-load-code",consume);
  },[]);
  const formToCode=()=>{
    if(text(colorListText).trim()&&colorListPreview.errors.length){toast.error(colorListPreview.errors[0]);return;}
    const activeSources=sources.map(source=>requestSource(source,colorListPreview.rows));
    const code=definitionFromForm(activeSources,activeSources.length>1?joins:[],maxRows,currentChartConfig());
    setDefinitionCode(code);
    toast.ok("현재 Query/JOIN/차트 폼을 전체 코드로 만들었습니다.");
  };
  const askChartAssistant=async(promptOverride="")=>{
    const instruction=text(promptOverride||assistantPrompt).trim();
    if(!instruction){toast.warn("Assistant에게 바꿀 내용을 입력해 주세요.");return;}
    if(text(colorListText).trim()&&colorListPreview.errors.length){toast.error(colorListPreview.errors[0]);return;}
    const activeSources=sources.map(source=>requestSource(source,colorListPreview.rows));
    const code=definitionFromForm(activeSources,activeSources.length>1?joins:[],maxRows,currentChartConfig());
    setAssistantBusy(true);
    try{
      const plan=await postJson("/api/filebrowser/chart-builder/assistant",{instruction,definition_code:code,columns});
      setAssistantReply(plan);
      if(!plan.changed){toast.info(plan.message||"반영할 변경을 찾지 못했습니다.");return;}
      const parsed={sources:plan.sources||sources,joins:plan.joins||[],max_rows:plan.max_rows||maxRows,chart:plan.chart||{},canonical_code:plan.canonical_code||code};
      setSources((parsed.sources||[newSource(1)]).map(source=>formSource(resolveSourceRoot(source))));
      setJoins(parsed.joins||[]);
      setMaxRows(parsed.max_rows||10000);
      setDefinitionCode(parsed.canonical_code);
      applyChartConfig(parsed.chart);
      setAssistantPrompt("");
      if(plan.requires_rerun){
        await run(parsed,{saveHistory:false});
        toast.ok(`${plan.message} JOIN 결과도 다시 조회했습니다.`);
      }else{
        toast.ok(plan.message||"차트 설정을 바꿨습니다.");
      }
    }catch(error){
      const message=error.message||String(error);
      setAssistantReply({ok:false,message,warnings:[]});
      toast.error(message);
    }finally{setAssistantBusy(false);}
  };
  const copyDefinition=async()=>{
    const code=text(definitionCode).trim()?definitionCode:definitionFromForm(sources,joins,maxRows,currentChartConfig());
    try{
      await navigator.clipboard.writeText(code);
      toast.ok("전체 코드를 복사했습니다.");
    }catch(_error){toast.error("브라우저에서 복사하지 못했습니다. 코드 영역을 직접 선택해 주세요.");}
  };
  const loadHistoryEntry=(entry)=>{
    const code=text(entry?.definition_code);
    setChartName(text(entry?.name));
    setDefinitionCode(code);
    applyDefinition(false,code);
    document.getElementById("chart-builder-code")?.scrollIntoView({behavior:"smooth",block:"start"});
  };
  const canManageHistory=canManagePage(user,"chartbuilder");
  const toggleHistoryPin=async entry=>{
    const historyId=text(entry?.history_id).trim();
    if(!historyId||!canManageHistory)return;
    setPinBusy(historyId);
    try{
      await postJson(`/api/filebrowser/chart-builder/history/${encodeURIComponent(historyId)}/pin`,{pinned:!entry.pinned});
      toast.ok(entry.pinned?"차트 고정을 해제했습니다.":"차트를 공용 히스토리 위에 고정했습니다.");
      await loadHistory(historySearch);
    }catch(error){toast.error(error.message||String(error));}
    finally{setPinBusy("");}
  };
  const download=()=>{
    if(!rows.length)return;
    const csv="\ufeff"+[columns.join(","),...rows.map(r=>columns.map(c=>csvCell(r[c])).join(","))].join("\r\n");
    const url=URL.createObjectURL(new Blob([csv],{type:"text/csv;charset=utf-8"}));
    const a=document.createElement("a");a.href=url;a.download=`flow_chart_join_${new Date().toISOString().slice(0,10)}.csv`;a.click();setTimeout(()=>URL.revokeObjectURL(url),1000);
  };
  const addQuery=(template=null)=>{
    if(sources.length>=10)return;
    const used=new Set(sources.map(s=>s.id));let n=sources.length+1;while(used.has(`q${n}`))n++;
    const next=template?{...template,id:`q${n}`} : newSource(n);
    const anchor=sources[0]?.id||"q1";
    setSources(v=>[...v,next]);
    setJoins(v=>[...v,{left:anchor,right:next.id,left_on:"root_lot_id, wafer_id",right_on:"root_lot_id, wafer_id",how:"left"}]);
  };
  const updateQuery=(index,next)=>{
    const oldId=sources[index]?.id;
    setSources(old=>old.map((value,i)=>i===index?next:value));
    if(oldId&&next.id&&oldId!==next.id)setJoins(old=>old.map(join=>({...join,left:join.left===oldId?next.id:join.left,right:join.right===oldId?next.id:join.right})));
  };
  const removeQuery=(index)=>{
    const id=sources[index]?.id;
    setSources(old=>old.filter((_,i)=>i!==index));
    if(id)setJoins(old=>old.filter(join=>join.left!==id&&join.right!==id));
  };
  const replaceColorListRows=nextRows=>{
    const normalized=normalizeColorListRows(nextRows);
    setColorListRows(normalized);
    if(normalized.some(row=>COLOR_LIST_COLUMNS.some(name=>text(row[name]).trim())))setColorCol("__custom__");
    else setSources(old=>old.map(source=>({...source,runtime_root_lot_ids:[],runtime_wafer_ids:[],runtime_lot_wafer_pairs:[]})));
  };
  const updateColorListCell=(rowIndex,column,value)=>{
    replaceColorListRows(colorListRows.map((row,index)=>index===rowIndex?{...row,[column]:value}:row));
  };
  const pasteColorList=(event,rowIndex,columnIndex)=>{
    const raw=event.clipboardData?.getData("text/plain")||"";
    if(!raw)return;
    event.preventDefault();
    const lines=raw.replace(/\r\n?/g,"\n").split("\n");
    while(lines.length&&!lines[lines.length-1].trim())lines.pop();
    if(!lines.length)return;
    const split=line=>line.includes("\t")?line.split("\t"):line.split(",");
    const first=split(lines[0]),header=colorListHeaderIndex(first);
    const matrix=(header?lines.slice(1):lines).map(line=>{
      const cells=split(line);
      return header?COLOR_LIST_COLUMNS.map(name=>cells[header[name]]??""):cells;
    });
    const startColumn=header?0:columnIndex;
    const next=colorListRows.map(row=>({...row}));
    matrix.slice(0,COLOR_LIST_MAX_ROWS-rowIndex).forEach((cells,rowOffset)=>{
      const targetIndex=rowIndex+rowOffset;
      while(next.length<=targetIndex&&next.length<COLOR_LIST_MAX_ROWS)next.push(blankColorListRow());
      cells.slice(0,COLOR_LIST_COLUMNS.length-startColumn).forEach((value,cellOffset)=>{
        next[targetIndex][COLOR_LIST_COLUMNS[startColumn+cellOffset]]=text(value).trim();
      });
    });
    replaceColorListRows(next);
  };
  const applyColorList=()=>{
    if(colorListPreview.errors.length){toast.error(colorListPreview.errors[0]);return;}
    if(!colorListPreview.rows.length){toast.warn("root_lot_id, wafer_id, color 목록을 입력해 주세요.");return;}
    setColorCol("__custom__");
    toast.ok(`${colorListPreview.rows.length}개 조합을 컬러링 목록으로 적용했습니다.`);
  };
  const parsedColorRules=useMemo(()=>parseChartColorRules(combinedColorRuleLines),[combinedColorRuleLines]);
  const colorRuleError=parsedColorRules.find(rule=>rule.error)?.error||"";
  const customColorMap=useMemo(()=>buildChartColorMap(parsedColorRules,customColorElse),[parsedColorRules,customColorElse]);
  const colorLabel=colorCol==="__custom__"?"Custom Color":colorCol;
  const rowColorValue=row=>{
    if(colorCol!=="__custom__")return colorCol?row[colorCol]:"";
    return chartColorValue(row,parsedColorRules);
  };
  const chartColorMap=colorCol==="__custom__"?customColorMap:{};
  const chart=useMemo(()=>{
    // pie 는 "행 수" 기준이면 Y 없이도 그린다 — 나머지는 X·Y 가 모두 있어야 한다.
    if(!rows.length||!xCol||(!yCol&&!["pie","donut"].includes(chartType)))return null;
    if(chartType==="wafer_map"){
      const validPair=shotPairs.find(pair=>pair.x===xCol&&pair.y===mapYCol);
      if(!validPair)return{chart_type:"wafer_map",error:"WF MAP은 chip_x_pos+chip_y_pos 또는 shot_x+shot_y 좌표 열이 모두 있어야 합니다."};
      if(radiusBusy)return{chart_type:"wafer_map",error:"제품별 Chip_Radius.csv shot 좌표를 불러오는 중입니다."};
      if(radiusError)return{chart_type:"wafer_map",error:radiusError};
      if(!radiusMatcher||!radiusLayout)return{chart_type:"wafer_map",error:"제품별 Chip_Radius.csv shot 좌표를 불러오지 못했습니다."};
      if(radiusMatcher.matchedCount===0)return{chart_type:"wafer_map",error:"SQL shot 좌표와 제품 WF MAP 좌표를 매칭하지 못했습니다."};
      const trellisMode=mapScope.startsWith("trellis_");
      const waferMode=["root_wafer","trellis_wafer","trellis_root_wafer"].includes(mapScope);
      if(mapScope!=="trellis_wafer"&&!rootLotCol)return{chart_type:"wafer_map",error:"선택한 WF MAP 단위에는 SQL 결과의 root_lot_id가 필요합니다."};
      if(waferMode&&!waferCol)return{chart_type:"wafer_map",error:"선택한 WF MAP 단위에는 SQL 결과의 wafer_id가 필요합니다."};
      const source=(result?.sources||[]).find(s=>s.product&&Array.isArray(s.columns)&&s.columns.includes(xCol)&&s.columns.includes(mapYCol))||(result?.sources||[]).find(s=>s.product);
      const addShot=(groups,r)=>{
        const target=radiusMatcher.match(r[xCol],r[mapYCol]),value=Number(r[yCol]);
        if(!target||!Number.isFinite(value))return;
        const x=Number(target.x),y=Number(target.y);
        const key=`${x},${y}`,group=groups.get(key)||{x,y,values:[]};group.values.push(value);groups.set(key,group);
      };
      const shotPoints=groups=>[...groups.values()].map(group=>{
        const n=group.values.length,value=aggregateShot(group.values,mapAggregation);
        return{x:group.x,y:group.y,value,n};
      });
      if(trellisMode){
        const panelShots=new Map(mapGroups.map(group=>[group.key,new Map()]));
        rows.slice(0,10000).forEach(r=>{
          const lot=rootLotCol?text(r[rootLotCol]):"",wafer=text(r[waferCol]);
          const key=mapScope==="trellis_wafer"?wafer:`${lot}\u001f${wafer}`;
          const groups=panelShots.get(key);if(groups)addShot(groups,r);
        });
        const panels=mapGroups.map(group=>({key:group.key,label:group.label,points:shotPoints(panelShots.get(group.key)||new Map())})).filter(panel=>panel.points.length);
        const mapVehicle=source?.inline_coordinate_mapping?.vehicles?.[0]||source?.product||"";
        return{chart_type:"wafer_map",title:`${yCol} WF MAP Trellis (${mapAggregation})`,x_label:xCol,map_y_label:mapYCol,y_label:`${yCol} ${mapAggregation}`,product:mapVehicle,points:[],panels,aggregation:mapAggregation,map_scope:mapScope};
      }
      const selected=mapGroups.find(group=>group.key===mapTarget);
      if(!selected)return{chart_type:"wafer_map",error:"표시할 root lot 또는 wafer를 선택해 주세요."};
      const groups=new Map();
      rows.slice(0,10000).forEach(r=>{
        if(text(r[rootLotCol])!==selected.lot||(mapScope==="root_wafer"&&text(r[waferCol])!==selected.wafer))return;
        addShot(groups,r);
      });
      const points=shotPoints(groups);
      const mapVehicle=source?.inline_coordinate_mapping?.vehicles?.[0]||source?.product||"";
      return{chart_type:"wafer_map",title:`${selected.label} · ${yCol} WF MAP (${mapAggregation})`,x_label:xCol,map_y_label:mapYCol,y_label:`${yCol} ${mapAggregation}`,product:mapVehicle,points,aggregation:mapAggregation,map_scope:mapScope,map_target:selected};
    }
    if(chartType==="radius"){
      const validPair=shotPairs.find(pair=>pair.x===xCol&&pair.y===mapYCol);
      if(!validPair)return{chart_type:"scatter",error:"Radius Plot에는 chip_x_pos+chip_y_pos 또는 shot_x+shot_y 좌표 열이 모두 있어야 합니다."};
      if(radiusBusy)return{chart_type:"scatter",error:"Chip_Radius.csv의 제품별 shot radius를 불러오는 중입니다."};
      if(radiusError)return{chart_type:"scatter",error:radiusError};
      if(!radiusMatcher||!radiusLayout)return{chart_type:"scatter",error:"제품별 Chip_Radius shot 정보를 불러오지 못했습니다."};
      if(radiusMatcher.matchedCount===0)return{chart_type:"scatter",error:"SQL shot 좌표와 Chip_Radius.csv 좌표를 매칭하지 못했습니다."};
      const mapped=rows.slice(0,10000).map(row=>{
        const target=radiusMatcher.match(row[xCol],row[mapYCol]),value=Number(row[yCol]);
        if(!target||!Number.isFinite(value))return null;
        return{row,target,value};
      }).filter(Boolean);
      let points;
      if(radiusAggregation==="raw"){
        points=mapped.map(({row,target,value})=>({...row,x:target.radius,x_label:target.radius,y:value,radius:target.radius,radius_shot:`${target.x},${target.y}`,source_shot:`${row[xCol]},${row[mapYCol]}`,color_value:rowColorValue(row),trellis_value:trellisCol?row[trellisCol]:""}));
      }else{
        const buckets=new Map();
        mapped.forEach(({row,target,value})=>{
          const key=[target.x,target.y,text(rowColorValue(row)),trellisCol?text(row[trellisCol]):""].join("\u001f"),bucket=buckets.get(key)||{row,target,values:[]};
          bucket.values.push(value);buckets.set(key,bucket);
        });
        points=[...buckets.values()].map(({row,target,values})=>({...row,x:target.radius,x_label:target.radius,y:aggregateShot(values,radiusAggregation),radius:target.radius,radius_shot:`${target.x},${target.y}`,source_shot:`${row[xCol]},${row[mapYCol]}`,n:values.length,color_value:rowColorValue(row),trellis_value:trellisCol?row[trellisCol]:""}));
      }
      return{chart_type:"scatter",title:"",x_label:"Chip Radius (mm)",y_label:radiusAggregation==="raw"?yCol:`${yCol} ${radiusAggregation}`,color_by:colorLabel,color_map:chartColorMap,points,point_size:7,trend_grain:"radius",cubic_fit:radiusFitMode==="cubic",radius_mapping:radiusMatcher.description,radius_mask:radiusLayout.mask,radius_matched:radiusMatcher.matchedCount,radius_source_count:radiusMatcher.sourceCount,aggregation:radiusAggregation};
    }
    if(chartType==="pie"||chartType==="donut"){
      // 조각은 "전체 대비 몫"이라 합계가 성립하는 값만 쓴다 — 행 수 또는 Y 합계.
      const buckets=new Map();
      rows.slice(0,10000).forEach(row=>{
        const label=text(row[xCol]).trim()||"(빈값)";
        const value=pieBasis==="sum"?Number(row[yCol]):1;
        if(pieBasis==="sum"&&!Number.isFinite(value))return;
        buckets.set(label,(buckets.get(label)||0)+value);
      });
      const all=[...buckets.entries()].map(([label,value])=>({label,value})).sort((a,b)=>b.value-a.value);
      // 조각이 스물이면 어느 것도 안 읽힌다 — 상위 12개만 두고 나머지는 묶는다.
      const top=all.slice(0,PIE_SLICE_LIMIT);
      const rest=all.slice(PIE_SLICE_LIMIT);
      const slices=rest.length?[...top,{label:`기타 ${rest.length}개`,value:rest.reduce((sum,item)=>sum+item.value,0)}]:top;
      const total=slices.reduce((sum,item)=>sum+item.value,0)||1;
      const groups=slices.map(item=>({label:item.label,value:item.value,count:item.value,percent:Number((item.value/total*100).toFixed(1))}));
      const valueLabel=pieBasis==="sum"?`${yCol} 합계`:"행 수";
      return{chart_type:chartType,title:`${xCol} 구성비 · ${valueLabel}`,x_label:xCol,y_label:valueLabel,groups,aggregation:pieBasis};
    }
    if(chartType==="bar"||chartType==="bar_horizontal"){
      const buckets=new Map();
      rows.slice(0,10000).forEach(row=>{
        const label=text(row[xCol]),value=Number(row[yCol]);
        if(!label||!Number.isFinite(value))return;
        if(!buckets.has(label))buckets.set(label,[]);
        buckets.get(label).push(value);
      });
      const groups=[...buckets.entries()].map(([label,values])=>({label,value:aggregateShot(values,barAggregation),count:values.length}));
      return{chart_type:chartType,title:`${xCol} × ${yCol}`,x_label:xCol,y_label:`${yCol} ${barAggregation}`,groups,aggregation:barAggregation};
    }
    if(chartType==="line"){
      if(trendGrain==="daily"||trendGrain==="weekly"){
        // 구간마다 값이 하나로 줄어드니 선으로 이어야 흐름이 보인다(chart_type: line).
        // 색 계열이 있으면 계열마다 따로 이어야 하므로 구간 키에 색 값을 함께 넣는다.
        const buckets=new Map();
        rows.slice(0,10000).forEach(r=>{
          const bucket=timeBucket(r[xCol],trendGrain),value=Number(r[yCol]);
          if(!bucket||!Number.isFinite(value))return;
          const series=text(rowColorValue(r));
          const key=`${series}${bucket}`,group=buckets.get(key)||{bucket,series,values:[],row:r};
          group.values.push(value);buckets.set(key,group);
        });
        if(!buckets.size)return{chart_type:"line",error:`${xCol}에서 날짜를 읽지 못했습니다. 시간 열을 X로 선택해 주세요.`};
        const points=[...buckets.values()]
          .sort((a,b)=>a.series.localeCompare(b.series)||a.bucket.localeCompare(b.bucket))
          .map(group=>({...group.row,x:group.bucket,x_label:group.bucket,y:aggregateShot(group.values,trendAggregation),n:group.values.length,color_value:group.series,trellis_value:trellisCol?group.row[trellisCol]:""}));
        const unit=trendGrain==="daily"?"일별":"주별";
        return{chart_type:"line",title:`${yCol} Trend · ${unit} ${trendAggregation}`,x_label:`${xCol} (${unit})`,y_label:`${yCol} ${trendAggregation}`,color_by:colorLabel,color_map:chartColorMap,points,point_size:8,trend_grain:trendGrain,aggregation:trendAggregation};
      }
      if(trendGrain==="wafer"&&(!rootLotCol||!waferCol))return{chart_type:"scatter",error:"Wafer 집계 Trend에는 root_lot_id와 wafer_id가 모두 있어야 합니다."};
      if(trendGrain==="wafer"){
        const groups=new Map();
        rows.slice(0,10000).forEach(r=>{
          const value=Number(r[yCol]),lot=text(r[rootLotCol]),wafer=text(r[waferCol]);
          if(!Number.isFinite(value)||!lot||!wafer)return;
          const key=`${lot}\u001f${wafer}`,group=groups.get(key)||{lot,wafer,values:[],times:[],row:r};
          group.values.push(value);group.times.push(text(r[xCol]));groups.set(key,group);
        });
        const points=[...groups.values()].map((group,i)=>({
          ...group.row,x:i,x_label:group.times.filter(Boolean).sort().at(-1)||group.row[xCol],y:aggregateShot(group.values,trendAggregation),
          root_lot_id:group.lot,wafer_id:group.wafer,lot_wf:`${group.lot} / W${group.wafer}`,n:group.values.length,color_value:rowColorValue(group.row),trellis_value:trellisCol?group.row[trellisCol]:"",
        }));
        return{chart_type:"scatter",title:`${yCol} Trend · wafer ${trendAggregation}`,x_label:xCol,y_label:`${yCol} ${trendAggregation}`,color_by:colorLabel,color_map:chartColorMap,points,point_size:10,trend_grain:"wafer",aggregation:trendAggregation};
      }
      const points=rows.slice(0,10000).map((r,i)=>({...r,x:i,x_label:r[xCol],y:Number(r[yCol]),color_value:rowColorValue(r),trellis_value:trellisCol?r[trellisCol]:""})).filter(p=>Number.isFinite(p.y));
      return{chart_type:"scatter",title:`${yCol} Trend · shot raw`,x_label:xCol,y_label:yCol,color_by:colorLabel,color_map:chartColorMap,points,point_size:7,trend_grain:"shot"};
    }
    const numericX=rows.slice(0,80).some(r=>text(r[xCol]).trim()!==""&&Number.isFinite(Number(r[xCol])));
    const points=rows.slice(0,10000).map((r,i)=>({...r,x:numericX?Number(r[xCol]):i,x_label:r[xCol],y:Number(r[yCol]),color_value:rowColorValue(r),trellis_value:trellisCol?r[trellisCol]:""})).filter(p=>Number.isFinite(p.y)&&Number.isFinite(p.x));
    const fit=chartType==="scatter"&&numericX&&corrFitMode==="linear"?linearFit(points):null;
    return{chart_type:chartType,title:`${xCol} × ${yCol}`,x_label:xCol,y_label:yCol,color_by:colorLabel,color_map:chartColorMap,points,fit,corr:fit?.corr,emphasize_markers:chartType==="scatter",point_size:chartType==="scatter"?7:undefined};
  },[rows,xCol,yCol,mapYCol,colorCol,colorLabel,colorListText,customColorRules,customColorElse,trellisCol,chartType,result,shotPairs,rootLotCol,waferCol,mapScope,mapGroups,mapTarget,mapAggregation,trendGrain,trendAggregation,barAggregation,radiusAggregation,radiusFitMode,corrFitMode,pieBasis,radiusLayout,radiusMatcher,radiusBusy,radiusError]);
  const displayChart=useMemo(()=>{
    if(!chart)return chart;
    const decorate=point=>({...point,spec_low:specLowCol?point?.[specLowCol]:point?.spec_low,spec_high:specHighCol?point?.[specHighCol]:point?.spec_high});
    return{
      ...chart,
      title:text(chartTitle).trim()||chart.title,
      x_label:text(xAxisLabel).trim()||chart.x_label,
      y_label:text(yAxisLabel).trim()||chart.y_label,
      points:Array.isArray(chart.points)?chart.points.map(decorate):chart.points,
      point_size:Number(pointSize)||9,marker_opacity:Number(markerOpacity)||0.82,line_width:Number(lineWidth)||2.3,
      y_min:text(yMin).trim(),y_max:text(yMax).trim(),y_scale:yScale,show_grid:showGrid,
      legend_position:legendPosition,box_points:boxPoints,show_legend:showLegend,
    };
  },[chart,chartTitle,xAxisLabel,yAxisLabel,pointSize,markerOpacity,lineWidth,yMin,yMax,yScale,showGrid,legendPosition,boxPoints,showLegend,specLowCol,specHighCol]);
  const isPie=chartType==="pie"||chartType==="donut";
  // 상자별 통계는 그림과 같은 묶음(색 계열 × x 값)에서 낸다 — 표와 그림이 어긋나면 안 된다.
  const boxBuckets=useMemo(()=>chartType==="box"&&Array.isArray(displayChart?.points)?boxBucketsFromPoints(displayChart.points,colorCol):[],[chartType,displayChart,colorCol]);
  const boxStatsOn=chartType==="box"&&showBoxStats&&!displayChart?.error&&!trellisCol&&boxBuckets.length>0;
  // Color 로 상자를 쪼개면 표는 (색 × x) 순서라 그림의 x 눈금과 열이 1:1 이 아니다 — 정렬 포기.
  const boxAlignGeometry=boxStatsOn&&!colorCol?boxGeometry:null;
  // 표를 눈금에 "맞춰" 붙일 때만 차트의 x 눈금 글자를 끈다 — 정렬을 포기하면
  // 이름을 읽을 곳이 사라지므로 눈금을 도로 켜야 한다.
  const boxStatsAligned=boxStatsAlignment(boxAlignGeometry,boxBuckets.length).aligned;
  const applyAutoReportPreset=key=>{
    const exact=(names,pool=columns)=>names.map(name=>pool.find(column=>column.toLowerCase()===name)).find(Boolean)||"";
    const value=exact(["value","item_value","measurement_value","shot_yield"],numericCols)
      ||numericCols.find(column=>!["wafer_id","shot_x","shot_y","chip_x_pos","chip_y_pos"].includes(column.toLowerCase()))
      ||numericCols[0]||"";
    const lot=exact(["root_lot_id"]),wafer=exact(["wafer_id"]);
    if(key==="box"){
      const category=columns.find(column=>/^(knob_|fab_|mask_|split)/i.test(column))||lot||wafer||columns.find(column=>column!==value)||"";
      setChartType("box");setXCol(category);setYCol(value);setColorCol(category===lot?wafer:lot);setTrellisCol("");setShowBoxStats(true);setShowLegend(true);
      if(!category||!value)toast.warn("Box 기본 차트에는 범주 열과 숫자 Value 열이 필요합니다.");
      else toast.ok("Auto Report Box 기본 차트를 적용했습니다.");
      return;
    }
    if(key==="trend"){
      const time=exact(["tkout_time","time","date","datetime","timestamp"])
        ||columns.find(column=>/(?:time|date)$/i.test(column))||columns[0]||"";
      setChartType("line");setXCol(time);setYCol(value);setColorCol(lot);setTrellisCol("");setTrendGrain("shot");setShowLegend(true);
      if(!time||!value)toast.warn("Trend 기본 차트에는 시간 열과 숫자 Value 열이 필요합니다.");
      else toast.ok("Auto Report Trend 기본 차트를 적용했습니다.");
      return;
    }
    const pair=shotPairs[0];
    setChartType("wafer_map");setXCol(pair?.x||"");setMapYCol(pair?.y||"");setYCol(value);setColorCol("");setTrellisCol("");setMapScope("root_wafer");setMapTarget("");setMapAggregation("median");
    if(!pair||!value)toast.warn("WF MAP 기본 차트에는 shot X/Y 좌표와 숫자 Value 열이 필요합니다.");
    else toast.ok("Auto Report WF MAP 기본 차트를 적용했습니다.");
  };
  const pinnedHistory=history.filter(entry=>entry.pinned);
  const recentHistory=history.filter(entry=>!entry.pinned);
  const renderHistoryEntry=entry=><details key={entry.history_id} style={{borderBottom:"1px solid var(--border)",background:entry.pinned?"color-mix(in srgb, var(--accent-glow) 58%, var(--bg-primary))":"transparent"}}>
    <summary style={{cursor:"pointer",padding:"10px 14px",display:"flex",gap:10,alignItems:"center",flexWrap:"wrap",listStyle:"none"}}>
      {entry.pinned&&<span title="고정 차트" aria-label="고정 차트" style={{fontSize:14}}>📌</span>}
      <b style={{fontSize:13,color:"var(--text-primary)"}}>{entry.name}</b>
      <code style={{fontSize:10,color:"var(--text-secondary)",background:"var(--bg-tertiary)",padding:"3px 5px",borderRadius:4}}>{entry.history_id}</code>
      <b style={{fontSize:13,color:"var(--accent)"}}>{entry.username||"anonymous"}</b>
      <span style={{fontSize:12,color:"var(--text-secondary)"}}>{historyTime(entry.timestamp)}</span>
      <span style={{fontSize:12,color:"var(--text-secondary)"}}>Query {entry.source_count||0} · JOIN {entry.join_count||0} · 결과 {Number(entry.row_count||0).toLocaleString()}행</span>
      <span style={{fontSize:12,color:"var(--text-secondary)",overflow:"hidden",textOverflow:"ellipsis",whiteSpace:"nowrap",minWidth:180,flex:1}}>{(entry.sources||[]).map(source=>`${source.id}:${source.root}/${source.product}`).join(" · ")}</span>
      {canManageHistory&&<button type="button" disabled={pinBusy===entry.history_id} title={entry.pinned?"고정 해제":"히스토리 위에 고정"} onClick={event=>{event.preventDefault();event.stopPropagation();toggleHistoryPin(entry);}} style={{...btn,padding:"5px 9px",opacity:pinBusy===entry.history_id?0.55:1}}>{pinBusy===entry.history_id?"처리 중…":entry.pinned?"고정 해제":"위에 고정"}</button>}
      <button type="button" onClick={event=>{event.preventDefault();event.stopPropagation();loadHistoryEntry(entry);}} style={{...btn,padding:"5px 9px"}}>폼으로 불러오기</button>
    </summary>
    <div style={{padding:"0 14px 12px"}}>
      <pre style={{margin:0,padding:11,borderRadius:7,background:"var(--bg-primary)",border:"1px solid var(--border)",whiteSpace:"pre-wrap",fontFamily:"'JetBrains Mono',monospace",fontSize:11,lineHeight:1.55,color:"var(--text-secondary)"}}>{entry.definition_code}</pre>
    </div>
  </details>;
  return <div style={{padding:"20px 24px 60px",maxWidth:1500,margin:"0 auto",color:"var(--text-primary)"}}>
    <section style={{...card,marginBottom:16,borderColor:"var(--accent)",background:"linear-gradient(135deg,var(--bg-secondary),var(--accent-glow))"}}>
      <div style={{display:"flex",alignItems:"center",gap:8,flexWrap:"wrap",marginBottom:8}}>
        <strong style={{fontSize:15}}>🧭 차트 어시스트</strong>
      </div>
      <div style={{display:"flex",gap:8,alignItems:"stretch"}}>
        <textarea aria-label="차트 어시스트 요청" value={assistantPrompt} onChange={event=>setAssistantPrompt(event.target.value)} rows={2}
          onKeyDown={event=>{if(event.key==="Enter"&&!event.shiftKey){if(event.nativeEvent?.isComposing||event.keyCode===229)return;event.preventDefault();askChartAssistant();}}}
          placeholder="예: 기본 차트 자동 추천 · X축은 tkout_time, Y축은 value · 범례 숨겨줘 · 첫 JOIN을 inner로"
          style={{...input,resize:"vertical",lineHeight:1.5,flex:1}}/>
        <button type="button" onClick={()=>askChartAssistant()} disabled={assistantBusy||busy||codeBusy}
          style={{...btn,minWidth:96,background:"var(--accent)",borderColor:"var(--accent)",color:"#fff",opacity:(assistantBusy||busy||codeBusy)?0.65:1}}>{assistantBusy?"수정 중…":"바꿔줘"}</button>
      </div>
      <div style={{display:"flex",gap:6,flexWrap:"wrap",marginTop:8}}>
        {["기본 차트 자동 추천해줘","scatter로 바꾸고 X축은 shot_x, Y축은 value로","Y축을 log scale로 바꿔줘","범례 숨겨줘","Trellis 해제해줘","첫 JOIN을 inner로 바꿔줘"].map(example=><button type="button" key={example} onClick={()=>askChartAssistant(example)} disabled={assistantBusy||busy||codeBusy} style={{...btn,padding:"4px 8px",fontSize:11,fontWeight:700}}>{example}</button>)}
      </div>
      {assistantReply&&<div style={{marginTop:9,padding:"8px 10px",borderRadius:7,border:`1px solid ${assistantReply.ok===false?"var(--danger-line)":"var(--border)"}`,background:"var(--bg-primary)",fontSize:12,lineHeight:1.55,color:assistantReply.ok===false?"var(--danger)":"var(--text-primary)"}}>
        <b>어시스트</b> · {assistantReply.message}
        {assistantReply.requires_rerun&&<span style={{marginLeft:7,color:"var(--accent)",fontWeight:800}}>JOIN 변경 · 자동 재조회</span>}
        {!!assistantReply.warnings?.length&&<div style={{marginTop:3,color:"var(--warn)"}}>{assistantReply.warnings.join(" · ")}</div>}
      </div>}
    </section>
    <details style={{...card,marginBottom:16,padding:0,overflow:"hidden"}}>
      <summary style={{cursor:"pointer",padding:"11px 14px",fontSize:14,fontWeight:900,userSelect:"none"}}>사용 가이드</summary>
      <div style={{padding:"0 14px 13px",borderTop:"1px solid var(--border)",fontSize:13,lineHeight:1.75,color:"var(--text-secondary)"}}>
        <div style={{marginTop:10}}><b style={{color:"var(--text-primary)"}}>Query / JOIN</b> — Query를 1~10개 추가하고 root_lot_id·wafer_id 같은 공통 열로 연결합니다. SQL의 FROM은 선택한 DB와 Product로 자동 지정됩니다.</div>
        <div style={{marginTop:8}}><b style={{color:"var(--text-primary)"}}>JOIN 방식</b> — 왼쪽(left)은 지금까지 합쳐진 결과, 오른쪽(right)은 새로 붙일 query입니다. 기본값은 <b style={{color:"var(--text-primary)"}}>left</b>입니다.</div>
        <div style={{margin:"4px 0 4px 2px",display:"grid",gap:3}}>
          {JOIN_HOWS.map(item=><div key={item.how} style={{display:"flex",gap:8,alignItems:"baseline"}}>
            <code style={{flex:"0 0 54px",fontFamily:"'JetBrains Mono',monospace",fontWeight:900,color:"var(--accent)"}}>{item.how}</code>
            <span style={{minWidth:0}}>{item.desc}</span>
          </div>)}
        </div>
        <div>같은 이름의 열이 양쪽에 있으면 오른쪽 열은 <code style={{fontFamily:"'JetBrains Mono',monospace"}}>q2__열이름</code>처럼 query id가 앞에 붙습니다. wafer_id가 DB마다 숫자/문자로 달라도 JOIN 직전에 문자열로 맞춰 비교합니다.</div>
        <div><b style={{color:"var(--text-primary)"}}>Trend / Corr</b> — Trend는 시간축 scatter, Corr는 X·Y scatter입니다. Corr는 같은 DB의 두 item 또는 서로 다른 DB 결과도 비교할 수 있습니다.</div>
        <div><b style={{color:"var(--text-primary)"}}>Color / Trellis</b> — JOIN한 KNOB·FAB 등의 열을 Color로 구분하거나 Trellis 패널로 나눠 봅니다.</div>
        <div><b style={{color:"var(--text-primary)"}}>Custom Color / 시간 강조</b> — 연동표의 고정 색상과 <code>tkout_time WITHIN 7 DAYS THEN #ef4444</code> 같은 수식 규칙을 따로 관리합니다. 연동표가 먼저 적용되고, 일치하지 않은 행만 수식 규칙을 위에서부터 확인한 뒤 나머지는 ELSE 색으로 표시됩니다. Plotly의 Box/Lasso Select 강조는 <code>HIGHLIGHT</code>로 별도 제어합니다.</div>
        <div><b style={{color:"var(--text-primary)"}}>연동 필터</b> — <code>root_lot_id</code>·<code>wafer_id</code>·<code>color</code> 3열 표를 붙여 넣습니다. 정확히 일치하는 Lot/Wafer 조합만 모든 Query에 적용되며, 색상 규칙은 저장 코드에 남아 현재 데이터가 없어도 다음 실행에서 같은 조합에 같은 색을 씁니다.</div>
        <div><b style={{color:"var(--text-primary)"}}>기간</b> — Query마다 <code>RECENT_DAYS = 7</code>(선택 <code>DATE_COLUMN = tkout_time</code>)을 적으면 그 열 기준 최근 7일만 조회합니다. 저장값이 기본이며 Template Report 실행 컨텍스트에서 여러 차트의 기간을 한꺼번에 바꿀 수도 있습니다.</div>
        <div><b style={{color:"var(--text-primary)"}}>특정 값 필터 / 합친 열</b> — Query의 표에서 <code>DERIVE = lot_wafer | columns=root_lot_id,wafer_id | separator=_</code>로 열을 합치고, <code>FILTER = lot_wafer | operator=in | values=A1234_1,A1234_2</code>로 원본 열이나 합친 열을 필터합니다. 저장 코드와 Template Report 재실행에도 그대로 유지됩니다.</div>
        <div><b style={{color:"var(--text-primary)"}}>조회와 강조의 차이</b> — <code>RECENT_DAYS</code>는 오래된 행을 조회 결과에서 제외하고, <code>WITHIN N DAYS</code>는 조회된 행을 지우지 않고 색만 바꿉니다. 시간 열은 반드시 SQL SELECT 결과에 포함되어야 하며, 최근 여부는 ChartBuilder 또는 Template Report를 실행하는 현재 시각에 다시 계산됩니다.</div>
        <div><b style={{color:"var(--text-primary)"}}>차트 크기</b> — <code>CHART</code> 블록의 <code>WIDTH = 1200</code>, <code>HEIGHT = 650</code>으로 픽셀 크기를 지정합니다. 생략하면 화면 폭에 맞춰 자동 조정됩니다.</div>
        <div><b style={{color:"var(--text-primary)"}}>Y축 Scale</b> — 차트 디테일에서 Linear/Log를 고르거나 코드에 <code>Y_SCALE = log</code>를 적습니다. Log의 수동 최소·최대는 0보다 큰 값만 허용합니다.</div>
        <div><b style={{color:"var(--text-primary)"}}>ET Reformatter</b> — ET Query에서 ET 다운로드와 같은 REAL·ADDP 계산 item을 선택할 수 있습니다. 코드에서는 <code>REFORMATTER = true</code>, <code>ITEMS = alias1, alias2</code>를 사용합니다.</div>
        <div><b style={{color:"var(--text-primary)"}}>Box Plot</b> — X 열의 값마다 상자 하나를 그리고, 그림 아래 통계표에서 상자별 Count·Median·StdDev 등을 골라 볼 수 있습니다.</div>
        <div><b style={{color:"var(--text-primary)"}}>Pie / Donut</b> — X 열의 값별 구성비입니다. 기준은 행 수 또는 Y 합계이고, 조각이 많으면 상위 {PIE_SLICE_LIMIT}개만 두고 나머지는 "기타"로 묶습니다.</div>
        <div><b style={{color:"var(--text-primary)"}}>Radius / WF MAP</b> — Radius는 Chip_Radius.csv와 매칭된 shot만, WF MAP은 유효한 shot X·Y 좌표가 있는 데이터만 그립니다.</div>
        <div><b style={{color:"var(--text-primary)"}}>집계 / 피팅</b> — Radius와 WF MAP은 shot 집계를 선택할 수 있고 Radius 3차 회귀와 Corr 1차 회귀·R²는 피팅 선택기로 켜거나 끕니다.</div>
        <div style={{marginTop:12,paddingTop:12,borderTop:"1px solid var(--border)"}}><b style={{color:"var(--text-primary)"}}>전체 코드 예시와 해석</b> — 아래 코드는 Query부터 차트·색·크기·행 한도까지 모두 포함합니다. 필요한 예시를 코드 영역에 넣은 뒤 DB/Product와 열 이름만 실제 값으로 바꿔 사용하세요.</div>
        <div style={{display:"grid",gap:10,marginTop:9}}>{GUIDE_EXAMPLES.map(example=><details key={example.title} style={{border:"1px solid var(--border)",borderRadius:8,overflow:"hidden",background:"var(--bg-primary)"}}>
          <summary style={{cursor:"pointer",padding:"9px 11px",fontWeight:900,color:"var(--text-primary)"}}>{example.title}</summary>
          <div style={{padding:"0 11px 11px"}}>
            <div style={{margin:"7px 0 9px",padding:"8px 10px",borderRadius:6,background:"var(--accent-glow)",color:"var(--text-primary)"}}><b>해석</b> · {example.interpretation}</div>
            <pre style={{margin:0,padding:11,borderRadius:7,background:"#0f172a",color:"#e2e8f0",overflow:"auto",whiteSpace:"pre",fontFamily:"'JetBrains Mono',monospace",fontSize:11,lineHeight:1.55}}>{example.code}</pre>
            <button type="button" onClick={()=>{setDefinitionCode(example.code+"\n");document.getElementById("chart-builder-code")?.scrollIntoView({behavior:"smooth",block:"start"});}} style={{...btn,marginTop:8}}>이 예시를 코드 영역에 넣기</button>
          </div>
        </details>)}</div>
      </div>
    </details>
    <section id="chart-builder-code" style={{...card,marginBottom:16,borderColor:"var(--accent)"}}>
      <div style={{display:"flex",alignItems:"center",gap:8,flexWrap:"wrap",marginBottom:8}}>
        <strong style={{fontSize:15}}>전체 코드 입력 · 공유</strong>
        <span style={{fontSize:12,color:"var(--text-secondary)"}}>작성자 {user?.username||"anonymous"}</span>
        <span style={{fontSize:12,color:"var(--text-secondary)",marginLeft:"auto"}}>코드를 적용하면 아래 Query/JOIN 폼이 자동으로 채워집니다.</span>
      </div>
      <div style={{fontSize:12,lineHeight:1.65,color:"var(--text-secondary)",marginBottom:8}}>
        Query는 <code>Q1</code> 다음 줄에 <code>TABLE</code>·<code>PRODUCT</code>·<code>SQL</code>을 적고, 연결은 <code>JOIN q1 LEFT q2 ON root_lot_id, wafer_id</code>처럼 작성합니다.
        서로 다른 열은 <code>ON lot_id, wf_id = root_lot_id, wafer_id</code>로 씁니다. 한 줄 형식 <code>Q1 | TABLE=INLINE | PRODUCT=PRODA | SQL=SELECT ...</code>도 지원합니다.
        차트는 <code>CHART</code> 아래 <code>TYPE</code>·<code>X</code>·<code>Y</code>·<code>COLOR</code>·<code>TRELLIS</code>를 적으며, 생략하면 결과 열에서 기본 축을 자동 선택합니다.
        합친 열과 필터는 Query 안에 <code>DERIVE</code>와 <code>FILTER</code>로 적습니다. 필터는 원본 열과 앞에서 만든 파생열 모두 사용할 수 있습니다.
        기간은 Query의 <code>RECENT_DAYS = 30</code>로 적고, 최근 데이터 색상은 별도 수식에 <code>COLOR_RULE = tkout_time WITHIN 7 DAYS THEN red</code>처럼 적습니다. 두 값 모두 저장되어 Template Report 실행에도 그대로 쓰입니다.
      </div>
      <SqlColumnAutocomplete id="chart-builder-definition-editor" ariaLabel="차트생성 전체 코드" value={definitionCode} onChange={setDefinitionCode} resolveContext={resolveDefinitionAutocomplete} rows={16}
        style={{...input,fontFamily:"'JetBrains Mono',monospace",fontSize:12,lineHeight:1.55,resize:"vertical",tabSize:2}}/>
      <div style={{display:"flex",gap:8,alignItems:"center",flexWrap:"wrap",marginTop:9}}>
        <button type="button" onClick={()=>applyDefinition(false)} disabled={codeBusy||busy} style={{...btn,background:"var(--accent)",color:"#fff",borderColor:"var(--accent)"}}>{codeBusy?"코드 확인 중…":"코드 → 폼 적용"}</button>
        <button type="button" onClick={()=>applyDefinition(true)} disabled={codeBusy||busy} style={btn}>코드 적용 · SQL 실행</button>
        <button type="button" onClick={formToCode} disabled={codeBusy||busy} style={btn}>현재 폼 → 코드</button>
        <button type="button" onClick={copyDefinition} style={btn}>코드 복사</button>
      </div>
    </section>
    <details style={{...card,marginBottom:16,padding:0,overflow:"hidden"}}>
      <summary style={{cursor:"pointer",padding:"11px 14px",fontSize:14,userSelect:"none"}}>
        <span style={{display:"inline-flex",width:"calc(100% - 18px)",alignItems:"center",gap:8,flexWrap:"wrap",verticalAlign:"middle"}}>
          <strong style={{fontSize:15}}>root_lot_id/wafer_id coloring list</strong>
          <span style={{fontSize:11,color:"var(--text-secondary)"}}>모든 Query / Report 공용 · 3열 spreadsheet</span>
          <span style={{marginLeft:"auto",fontSize:10,fontWeight:900,color:colorListPreview.errors.length?"var(--danger)":"var(--ok)",border:`1px solid ${colorListPreview.errors.length?"var(--danger-line)":"var(--ok-line)"}`,borderRadius:999,padding:"3px 8px"}}>{colorListPreview.errors.length?"입력 확인":`${colorListPreview.rows.length}개 조합`}</span>
        </span>
      </summary>
      <div style={{padding:"12px 14px 14px",borderTop:"1px solid var(--border)",display:"grid",gap:10}}>
        <div style={{overflow:"auto",maxHeight:365,border:"1px solid var(--border)",borderRadius:7,background:"var(--bg-primary)"}}>
          <table aria-label="root_lot_id wafer_id coloring list" style={{width:"100%",minWidth:560,tableLayout:"fixed",borderCollapse:"separate",borderSpacing:0,fontSize:12}}>
            <colgroup><col style={{width:42}}/><col/><col/><col style={{width:"34%"}}/></colgroup>
            <thead><tr>
              <th aria-label="행 번호" style={{position:"sticky",top:0,zIndex:2,padding:"8px 6px",textAlign:"center",background:"var(--bg-tertiary)",borderRight:"1px solid var(--border)",borderBottom:"1px solid var(--border)",color:"var(--text-secondary)"}}>#</th>
              {COLOR_LIST_COLUMNS.map(name=><th key={name} style={{position:"sticky",top:0,zIndex:2,padding:"8px 9px",textAlign:"left",background:"var(--bg-tertiary)",borderRight:"1px solid var(--border)",borderBottom:"1px solid var(--border)",fontFamily:"monospace"}}>{name}</th>)}
            </tr></thead>
            <tbody>{colorListRows.map((row,rowIndex)=><tr key={rowIndex}>
              <th scope="row" style={{padding:"7px 6px",textAlign:"center",fontWeight:500,color:"var(--text-secondary)",background:"var(--bg-secondary)",borderRight:"1px solid var(--border)",borderBottom:"1px solid var(--border)"}}>{rowIndex+1}</th>
              {COLOR_LIST_COLUMNS.map((name,columnIndex)=><td key={name} style={{position:"relative",padding:0,borderRight:"1px solid var(--border)",borderBottom:"1px solid var(--border)"}}>
                {name==="color"&&text(row.color).trim()&&<span aria-hidden="true" style={{position:"absolute",left:8,top:"50%",transform:"translateY(-50%)",width:13,height:13,borderRadius:3,background:row.color,border:"1px solid #94a3b8",pointerEvents:"none"}}/>}
                <input aria-label={`${rowIndex+1}행 ${name}`} value={row[name]} onChange={event=>updateColorListCell(rowIndex,name,event.target.value)} onPaste={event=>pasteColorList(event,rowIndex,columnIndex)} spellCheck={false} placeholder={rowIndex===0?name==="root_lot_id"?"A1234":name==="wafer_id"?"1":"#dc2626":""} style={{width:"100%",boxSizing:"border-box",border:0,borderRadius:0,outlineOffset:-2,background:"transparent",color:"var(--text-primary)",padding:name==="color"&&text(row.color).trim()?"7px 9px 7px 29px":"7px 9px",fontFamily:"monospace",fontSize:12}}/>
              </td>)}
            </tr>)}</tbody>
          </table>
        </div>
        <div style={{display:"flex",gap:8,alignItems:"center",flexWrap:"wrap"}}>
          <button type="button" onClick={applyColorList} style={{...btn,background:"var(--accent)",borderColor:"var(--accent)",color:"#fff"}}>목록 적용</button>
          {!!colorListText&&<button type="button" onClick={()=>replaceColorListRows([])} style={btn}>표 비우기</button>}
          <span style={{fontSize:11,color:colorListPreview.errors.length?"var(--danger)":"var(--text-secondary)"}}>{colorListPreview.errors[0]||`${colorListPreview.rows.length}개 조합 · 정확한 Lot/Wafer 조합에 컬러 적용${colorListPreview.truncated?" · 앞 200개만 사용":""}`}</span>
        </div>
      </div>
    </details>
    <div style={{display:"grid",gridTemplateColumns:"repeat(auto-fit,minmax(390px,1fr))",gap:12}}>{sources.map((s,i)=><QueryCard key={`query-${i}`} source={s} index={i} roots={roots} autocompleteSource={queryAutocompleteSource(s,i)} onChange={next=>updateQuery(i,next)} onClone={()=>addQuery(s)} onRemove={sources.length>1?()=>removeQuery(i):null}/>)}</div>
    <div style={{display:"flex",gap:8,margin:"10px 0 16px",flexWrap:"wrap",alignItems:"center"}}><button type="button" style={btn} onClick={()=>addQuery()} disabled={sources.length>=10}>＋ DB Query</button><span style={{fontSize:12,color:"var(--text-secondary)"}}>현재 {sources.length}개 · 최대 10개</span></div>
    {sources.length>1&&<div style={card}><strong style={{fontSize:14}}>JOIN 설정</strong>{joins.map((j,i)=><div key={i} style={{display:"grid",gridTemplateColumns:"110px 1fr 28px 110px 1fr 110px 36px",gap:7,alignItems:"center",marginTop:9}}>
      <select value={j.left} onChange={e=>setJoins(v=>v.map((x,k)=>k===i?{...x,left:e.target.value}:x))} style={input}>{ids.map(id=><option key={id}>{id}</option>)}</select>
      <input value={j.left_on} placeholder="root_lot_id, wafer_id" onChange={e=>setJoins(v=>v.map((x,k)=>k===i?{...x,left_on:e.target.value}:x))} style={input}/><b style={{textAlign:"center"}}>=</b>
      <select value={j.right} onChange={e=>setJoins(v=>v.map((x,k)=>k===i?{...x,right:e.target.value}:x))} style={input}>{ids.map(id=><option key={id}>{id}</option>)}</select>
      <input value={j.right_on} placeholder="root_lot_id, wafer_id" onChange={e=>setJoins(v=>v.map((x,k)=>k===i?{...x,right_on:e.target.value}:x))} style={input}/>
      <select value={j.how} onChange={e=>setJoins(v=>v.map((x,k)=>k===i?{...x,how:e.target.value}:x))} style={input}>{JOIN_HOWS.map(x=><option key={x.how} value={x.how}>{x.how} · {x.short}</option>)}</select>
      <button type="button" onClick={()=>setJoins(v=>v.filter((_,k)=>k!==i))} style={btn}>×</button>
    </div>)}<button type="button" onClick={()=>setJoins(v=>[...v,{left:ids[0]||"q1",right:ids[1]||ids[0]||"q1",left_on:"root_lot_id, wafer_id",right_on:"root_lot_id, wafer_id",how:"left"}])} style={{...btn,marginTop:10}}>＋ JOIN</button><div style={{fontSize:12,color:"var(--text-secondary)",marginTop:7}}>복수 JOIN key는 쉼표로 구분합니다. 예: root_lot_id, wafer_id, step_id</div></div>}
    <div style={{display:"flex",alignItems:"end",gap:9,margin:"14px 0",flexWrap:"wrap"}}>
      <label style={{fontSize:13}}>결과 한도 <input aria-label="결과 한도" type="number" min="1" max="10000" value={maxRows} onChange={e=>setMaxRows(e.target.value)} style={{...input,width:105,marginLeft:6}}/></label>
      <label style={{fontSize:12,fontWeight:800,color:"var(--text-secondary)"}}>저장 차트 이름
        <input aria-label="저장 차트 이름" value={chartName} maxLength={120} onChange={e=>setChartName(e.target.value)} placeholder="예: PRODA 주간 VTH Trend" style={{...input,width:270,marginTop:4}}/>
      </label>
      <button type="button" onClick={()=>run()} disabled={busy} style={{...btn,background:"var(--accent)",color:"#fff",borderColor:"var(--accent)"}}>{busy?"SQL 실행 중…":"SQL 실행 · JOIN 및 저장"}</button>
      {rows.length>0&&<button type="button" onClick={download} style={btn}>CSV 다운로드</button>}
      <span style={{fontSize:11,color:"var(--text-secondary)"}}>동일 이름은 자동으로 (2), (3)…을 붙여 저장합니다.</span>
    </div>
    <section style={{...card,marginBottom:14,padding:0,overflow:"hidden",borderColor:"#93c5fd"}}>
      <div style={{display:"flex",alignItems:"stretch",gap:7,flexWrap:"wrap",padding:"10px 12px",background:"linear-gradient(90deg,#eff6ff,#fff)"}}>
        <div style={{minWidth:190,alignSelf:"center"}}><b style={{fontSize:13,color:"#0f172a"}}>Auto Report 기본 차트</b><div style={{fontSize:10,color:"#64748b",marginTop:2}}>공용 코드 히스토리 위에 항상 표시 · 조회 결과 열을 찾아 즉시 배치</div></div>
        {AUTO_REPORT_PRESETS.map(preset=><button type="button" key={preset.key} onClick={()=>applyAutoReportPreset(preset.key)} style={{...btn,background:chartType===(preset.key==="trend"?"line":preset.key)?"#dbeafe":"#f8fafc",borderColor:chartType===(preset.key==="trend"?"line":preset.key)?"#3b82f6":"#cbd5e1",color:"#0f172a",padding:"6px 10px",textAlign:"left"}}><span style={{display:"block",fontSize:12}}>{preset.label}</span><span style={{display:"block",fontSize:9,color:"#64748b",fontWeight:600,marginTop:2}}>{preset.desc}</span></button>)}
      </div>
    </section>
    <details open style={{...card,marginBottom:14,padding:0,overflow:"hidden"}}>
      <summary style={{cursor:"pointer",padding:"11px 14px",fontSize:14,fontWeight:900,userSelect:"none"}}>공용 코드 히스토리 · 고정 {pinnedHistory.length}건 + 최근 {recentHistory.length}건 / 최대 500</summary>
      <div style={{borderTop:"1px solid var(--border)",maxHeight:430,overflow:"auto"}}>
        <div style={{position:"sticky",top:0,zIndex:2,padding:"10px 14px",background:"var(--bg-secondary)",borderBottom:"1px solid var(--border)"}}>
          <input aria-label="저장 차트 검색" value={historySearch} onChange={event=>setHistorySearch(event.target.value)} placeholder="Chart ID · Name · 작성자 · SQL 코드 검색" style={input}/>
        </div>
        {historyBusy&&!history.length&&<div style={{padding:16,fontSize:13,color:"var(--text-secondary)"}}>히스토리를 불러오는 중입니다.</div>}
        {!historyBusy&&!history.length&&<div style={{padding:16,fontSize:13,color:"var(--text-secondary)"}}>{historySearch?"검색 조건에 맞는 저장 차트가 없습니다.":"아직 실행 이력이 없습니다. SQL 실행이 성공하면 Chart ID, Name, 사용자 ID와 시각, 전체 코드가 여기에 남습니다."}</div>}
        {!!pinnedHistory.length&&<div style={{position:"sticky",top:55,zIndex:1,padding:"6px 14px",fontSize:10,fontWeight:900,color:"var(--accent)",background:"var(--accent-glow)",borderBottom:"1px solid var(--border)"}}>📌 고정 차트 · 최근 500건 한도에 포함되지 않음</div>}
        {pinnedHistory.map(renderHistoryEntry)}
        {!!recentHistory.length&&<div style={{padding:"6px 14px",fontSize:10,fontWeight:900,color:"var(--text-secondary)",background:"var(--bg-tertiary)",borderBottom:"1px solid var(--border)"}}>최근 저장 차트</div>}
        {recentHistory.map(renderHistoryEntry)}
      </div>
    </details>
    {result&&<div style={{display:"grid",gap:12}}>
      {(result.warnings||[]).map((w,i)=><div key={i} style={{padding:"8px 10px",border:"1px solid var(--warn-line)",background:"var(--warn-50)",color:"var(--warn)",borderRadius:7,fontSize:13}}>{w}</div>)}
      <div style={{display:"grid",gridTemplateColumns:"repeat(auto-fit,minmax(250px,1fr))",gap:8}}>{(result.sources||[]).map(s=>{
        const mapping=s.inline_coordinate_mapping;
        return <details key={s.id} style={card}><summary style={{cursor:"pointer",fontWeight:900}}>{s.id} · {s.root}/{s.product} · {s.row_count.toLocaleString()}행</summary>
          {mapping&&<div style={{marginTop:9,padding:"7px 9px",borderRadius:6,border:`1px solid ${mapping.applied?"#86efac":"var(--warn-line)"}`,background:mapping.applied?"#f0fdf4":"var(--warn-50)",color:mapping.applied?"#166534":"var(--warn)",fontSize:11,lineHeight:1.55}}>
            <b>TEG Inline map</b> · {mapping.applied?`${Number(mapping.matched_rows||0).toLocaleString()}/${Number((mapping.matched_rows||0)+(mapping.unmatched_rows||0)).toLocaleString()}행 매칭 (${Number(mapping.match_rate||0).toFixed(2)}%)`:mapping.configured?"사용 가능한 좌표 없음":"연결 규칙 없음"}
            {!!mapping.map_names?.length&&<><br/>TABLE {mapping.map_names.join(", ")}</>}
            {!!mapping.vehicles?.length&&<> · 제품 map {mapping.vehicles.join(", ")}</>}
          </div>}
          <pre style={{whiteSpace:"pre-wrap",fontSize:11,margin:"9px 0 0",color:"var(--text-secondary)"}}>{s.sql}</pre>
        </details>;
      })}</div>
      <DataTable title={`JOIN 결과 · ${Number(joined.row_count||0).toLocaleString()}행`} columns={columns} rows={rows.slice(0,500)}/>
      {rows.length>0&&<div style={{...card,background:"#fff",color:"#111827"}}>
        {/* 차트 설정 — 어떤 열이 X·Y·Color·Trellis 로 갔고 집계가 무엇인지 한눈에 보이게
            같은 크기의 칸에 이름표를 얹어 늘어놓는다. 예전엔 한 줄 flex 라 항목이
            늘어날수록 어디가 무엇인지 읽히지 않았다. */}
        <div style={{border:"1px solid #e2e8f0",borderRadius:8,background:"#f8fafc",padding:"10px 12px",marginBottom:10}}>
          <div style={{display:"flex",alignItems:"center",gap:8,marginBottom:8,flexWrap:"wrap"}}>
            <strong style={{fontSize:13,color:"#0f172a"}}>차트 설정</strong>
            {chart?.corr!=null&&!trellisCol&&<span style={{fontFamily:"'JetBrains Mono',monospace",fontSize:12,color:"#475569"}}>corr={Number(chart.corr).toFixed(4)} · R²={Number(chart.fit?.r2||0).toFixed(4)}</span>}
            {chartType==="box"&&<label style={{marginLeft:"auto",display:"inline-flex",alignItems:"center",gap:6,fontSize:12,fontWeight:800,color:"#334155",cursor:"pointer"}}>
              <input type="checkbox" checked={showBoxStats} onChange={e=>setShowBoxStats(e.target.checked)}/>통계표 표시
            </label>}
          </div>
          <div style={{display:"grid",gridTemplateColumns:"repeat(auto-fit,minmax(165px,1fr))",gap:9,alignItems:"end"}}>
            <Field label="차트"><select aria-label="차트 종류" value={chartType} onChange={e=>setChartType(e.target.value)} style={fieldInput}><option value="scatter">Corr / Scatter</option><option value="line">Trend</option><option value="box">Box Plot</option><option value="bar">Bar Vertical</option><option value="bar_horizontal">Bar Horizontal</option><option value="pie">Pie</option><option value="donut">Donut</option><option value="radius">Radius Plot</option><option value="wafer_map">WF MAP</option></select></Field>
            <Field label="Width (px · 자동은 빈칸)"><input aria-label="차트 Width" type="number" min="320" max="2400" value={chartWidth} onChange={e=>setChartWidth(e.target.value)} placeholder="auto" style={fieldInput}/></Field>
            <Field label="Height (px · 자동은 빈칸)"><input aria-label="차트 Height" type="number" min="240" max="1600" value={chartHeight} onChange={e=>setChartHeight(e.target.value)} placeholder="auto" style={fieldInput}/></Field>
            {chartType==="wafer_map"?<>
              <Field label="Shot 좌표"><select aria-label="Shot 좌표" value={xCol} onChange={e=>{const pair=shotPairs.find(p=>p.x===e.target.value);setXCol(e.target.value);setMapYCol(pair?.y||"");}} style={fieldInput}><option value="">좌표 열 없음</option>{shotPairs.map(pair=><option key={`${pair.x}:${pair.y}`} value={pair.x}>{pair.label}</option>)}</select></Field>
              <Field label="Value"><select value={yCol} onChange={e=>setYCol(e.target.value)} style={fieldInput}>{numericCols.map(c=><option key={c}>{c}</option>)}</select></Field>
              <Field label="Map 표시"><select aria-label="Map 표시" value={mapScope} onChange={e=>{setMapScope(e.target.value);setMapTarget("");}} style={fieldInput}><option value="root_wafer">단일 · root lot | wafer</option><option value="root_lot">단일 · root lot 전체</option><option value="trellis_wafer">Trellis · wafer별</option><option value="trellis_root_wafer">Trellis · root_lot_id | wafer_id</option></select></Field>
              {!mapScope.startsWith("trellis_")&&<Field label="대상"><select aria-label="WF MAP 대상" value={mapTarget} onChange={e=>setMapTarget(e.target.value)} style={fieldInput}>{mapGroups.map(group=><option key={group.key} value={group.key}>{group.label}</option>)}</select></Field>}
              <Field label="Shot 집계"><select aria-label="Shot 집계" value={mapAggregation} onChange={e=>setMapAggregation(e.target.value)} style={fieldInput}>{AGGREGATIONS.map(method=><option key={method} value={method}>{method}</option>)}</select></Field>
            </>:<>
              {chartType==="radius"
                ?<Field label="Shot 좌표"><select aria-label="Radius Shot 좌표" value={xCol} onChange={e=>{const pair=shotPairs.find(p=>p.x===e.target.value);setXCol(e.target.value);setMapYCol(pair?.y||"");}} style={fieldInput}><option value="">좌표 열 없음</option>{shotPairs.map(pair=><option key={`${pair.x}:${pair.y}`} value={pair.x}>{pair.label}</option>)}</select></Field>
                :<Field label="X"><select value={xCol} onChange={e=>setXCol(e.target.value)} style={fieldInput}>{columns.map(c=><option key={c}>{c}</option>)}</select></Field>}
              {(!isPie||pieBasis==="sum")&&<Field label="Y"><select value={yCol} onChange={e=>setYCol(e.target.value)} style={fieldInput}>{numericCols.map(c=><option key={c}>{c}</option>)}</select></Field>}
              {isPie&&<Field label="Pie 기준"><select aria-label="Pie 기준" value={pieBasis} onChange={e=>setPieBasis(e.target.value)} style={fieldInput}><option value="count">행 수</option><option value="sum">Y 합계</option></select></Field>}
              {chartType==="line"&&<Field label="Trend 단위"><select aria-label="Trend 단위" value={trendGrain} onChange={e=>setTrendGrain(e.target.value)} style={fieldInput}>{TREND_GRAINS.map(g=><option key={g.key} value={g.key}>{g.label}</option>)}</select></Field>}
              {chartType==="line"&&trendGrain!=="shot"&&<Field label="집계"><select aria-label="Trend 집계" value={trendAggregation} onChange={e=>setTrendAggregation(e.target.value)} style={fieldInput}>{AGGREGATIONS.map(method=><option key={method} value={method}>{method}</option>)}</select></Field>}
              {chartType.startsWith("bar")&&<Field label="Bar 집계"><select aria-label="Bar 집계" value={barAggregation} onChange={e=>setBarAggregation(e.target.value)} style={fieldInput}>{AGGREGATIONS.map(method=><option key={method} value={method}>{method}</option>)}</select></Field>}
              {chartType==="radius"&&<Field label="Radius 단위"><select aria-label="Radius 단위" value={radiusAggregation} onChange={e=>setRadiusAggregation(e.target.value)} style={fieldInput}><option value="raw">Shot raw</option>{AGGREGATIONS.map(method=><option key={method} value={method}>{method}</option>)}</select></Field>}
              {!isPie&&<Field label="Color"><select aria-label="Color" value={colorCol} onChange={e=>setColorCol(e.target.value)} style={fieldInput}><option value="">없음</option><option value="__custom__">Custom 규칙</option>{columns.map(c=><option key={c}>{c}</option>)}</select></Field>}
              {!chartType.startsWith("bar")&&!isPie&&<Field label="Trellis"><select aria-label="Trellis" value={trellisCol} onChange={e=>setTrellisCol(e.target.value)} style={fieldInput}><option value="">없음</option>{columns.map(c=><option key={c}>{c}</option>)}</select></Field>}
              {!isPie&&<Field label="Highlight"><label style={{...fieldInput,display:"flex",alignItems:"center",gap:7,minHeight:31,cursor:"pointer"}}><input type="checkbox" checked={highlightEnabled} onChange={e=>setHighlightEnabled(e.target.checked)}/>Box / Lasso 선택 강조</label></Field>}
              <Field label="Legend"><label style={{...fieldInput,display:"flex",alignItems:"center",gap:7,minHeight:31,cursor:"pointer"}}><input aria-label="차트 내부 Legend 표시" type="checkbox" checked={showLegend} onChange={e=>setShowLegend(e.target.checked)}/>차트 내부 표시</label></Field>
              {chartType==="radius"&&<Field label="피팅"><select aria-label="Radius 피팅" value={radiusFitMode} onChange={e=>setRadiusFitMode(e.target.value)} style={fieldInput}><option value="none">없음</option><option value="cubic">3차 회귀</option></select></Field>}
              {chartType==="scatter"&&<Field label="피팅"><select aria-label="Corr 피팅" value={corrFitMode} onChange={e=>setCorrFitMode(e.target.value)} style={fieldInput}><option value="none">없음</option><option value="linear">1차 회귀 + R²</option></select></Field>}
            </>}
          </div>
          <details style={{marginTop:10,border:"1px solid #cbd5e1",borderRadius:7,background:"#fff",overflow:"hidden"}}>
            <summary style={{cursor:"pointer",padding:"8px 10px",fontSize:12,fontWeight:900,color:"#0f172a"}}>차트 디테일 · 제목 / 축 / 점 / 선 / Spec / WF MAP 색</summary>
            <div style={{padding:10,borderTop:"1px solid #e2e8f0",display:"grid",gridTemplateColumns:"repeat(auto-fit,minmax(155px,1fr))",gap:9,alignItems:"end"}}>
              <Field label="차트 제목"><input value={chartTitle} onChange={e=>setChartTitle(e.target.value)} placeholder="자동 제목" style={fieldInput}/></Field>
              <Field label="X축 제목"><input value={xAxisLabel} onChange={e=>setXAxisLabel(e.target.value)} placeholder={xCol||"자동"} style={fieldInput}/></Field>
              <Field label="Y축 제목"><input value={yAxisLabel} onChange={e=>setYAxisLabel(e.target.value)} placeholder={yCol||"자동"} style={fieldInput}/></Field>
              <Field label="Y축 최소"><input aria-label="Y축 최소" type="number" value={yMin} onChange={e=>setYMin(e.target.value)} placeholder="auto" style={fieldInput}/></Field>
              <Field label="Y축 최대"><input aria-label="Y축 최대" type="number" value={yMax} onChange={e=>setYMax(e.target.value)} placeholder="auto" style={fieldInput}/></Field>
              <Field label="Y축 Scale"><select value={yScale} onChange={e=>setYScale(e.target.value)} style={fieldInput}><option value="linear">Linear</option><option value="log">Log</option></select></Field>
              <Field label="Point 크기"><input aria-label="Point 크기" type="number" min="2" max="30" value={pointSize} onChange={e=>setPointSize(e.target.value)} style={fieldInput}/></Field>
              <Field label="Marker 투명도"><input aria-label="Marker 투명도" type="number" min="0.05" max="1" step="0.05" value={markerOpacity} onChange={e=>setMarkerOpacity(e.target.value)} style={fieldInput}/></Field>
              <Field label="Line 굵기"><input aria-label="Line 굵기" type="number" min="0.5" max="8" step="0.1" value={lineWidth} onChange={e=>setLineWidth(e.target.value)} style={fieldInput}/></Field>
              <Field label="Legend 위치"><select value={legendPosition} onChange={e=>setLegendPosition(e.target.value)} style={fieldInput}><option value="bottom">아래</option><option value="top">위</option><option value="right">오른쪽</option><option value="inside">차트 안</option></select></Field>
              <Field label="Grid"><label style={{...fieldInput,display:"flex",alignItems:"center",gap:7,minHeight:31,cursor:"pointer"}}><input type="checkbox" checked={showGrid} onChange={e=>setShowGrid(e.target.checked)}/>격자 표시</label></Field>
              {chartType==="box"&&<Field label="Box 점 표시"><select value={boxPoints} onChange={e=>setBoxPoints(e.target.value)} style={fieldInput}><option value="outliers">Outlier만</option><option value="all">전체 점</option><option value="none">점 숨김</option></select></Field>}
              {!isPie&&chartType!=="wafer_map"&&<><Field label="Spec Low 열"><select value={specLowCol} onChange={e=>setSpecLowCol(e.target.value)} style={fieldInput}><option value="">없음</option>{numericCols.map(c=><option key={c}>{c}</option>)}</select></Field><Field label="Spec High 열"><select value={specHighCol} onChange={e=>setSpecHighCol(e.target.value)} style={fieldInput}><option value="">없음</option>{numericCols.map(c=><option key={c}>{c}</option>)}</select></Field></>}
              {chartType==="wafer_map"&&<><Field label="WF MAP Palette"><select value={waferPalette} onChange={e=>setWaferPalette(e.target.value)} style={fieldInput}><option value="blue_gray_red">Blue · Gray · Red</option><option value="red_gray_blue">Red · Gray · Blue</option><option value="viridis">Viridis</option><option value="gray">Gray</option></select></Field><Field label="WF Low"><input type="number" value={waferLow} onChange={e=>setWaferLow(e.target.value)} placeholder="P10 자동" style={fieldInput}/></Field><Field label="WF Center"><input type="number" value={waferCenter} onChange={e=>setWaferCenter(e.target.value)} placeholder="Median 자동" style={fieldInput}/></Field><Field label="WF High"><input type="number" value={waferHigh} onChange={e=>setWaferHigh(e.target.value)} placeholder="P90 자동" style={fieldInput}/></Field></>}
            </div>
            <div style={{padding:"0 10px 9px",fontSize:10,color:"#64748b"}}>이 설정은 전체 코드에 저장되며 Template Report 화면과 PPT 캡처에도 동일하게 적용됩니다. Y축 수동 범위는 최소·최대를 함께 입력합니다.</div>
          </details>
          {colorCol==="__custom__"&&<div style={{display:"grid",gridTemplateColumns:"minmax(320px,1fr) minmax(130px,220px)",gap:9,marginTop:10,alignItems:"start"}}>
            <Field label="tkout_time / 수식 컬러링 · 연동표 다음 순서"><textarea aria-label="Custom Color 수식 규칙" value={customColorRules} onChange={e=>setCustomColorRules(e.target.value)} rows={4} placeholder={"tkout_time WITHIN 3 DAYS THEN #dc2626\ntkout_time WITHIN 7 DAYS THEN #f59e0b"} style={{...fieldInput,fontFamily:"'JetBrains Mono',monospace",resize:"vertical"}}/></Field>
            <Field label="ELSE 색상"><input aria-label="Custom Color ELSE" value={customColorElse} onChange={e=>setCustomColorElse(e.target.value)} placeholder="gray 또는 #9ca3af" style={fieldInput}/></Field>
            <div style={{gridColumn:"1 / -1",fontSize:11,color:colorRuleError?"#b91c1c":"#475569"}}>{colorRuleError||`연동표 ${linkedColorRuleLines.length}개 + 수식 ${formulaColorRuleLines.length}개 · 연동표가 먼저 적용되고, tkout_time 수식과 색은 별도로 저장되어 Template Report에서도 재사용됩니다.`}</div>
          </div>}
        </div>
        {chartType==="wafer_map"&&<div style={{fontSize:12,color:chart?.error?"#b91c1c":"#475569",margin:"0 0 9px"}}>{chart?.error||(mapScope.startsWith("trellis_")?`${mapScope==="trellis_wafer"?"wafer":"root_lot_id | wafer_id"}별 패널에서 같은 shot 좌표의 값을 ${mapAggregation}로 집계하고 공통 컬러 스케일을 적용합니다.`:`선택한 ${mapScope==="root_wafer"?"root lot·wafer":"root lot의 모든 wafer"}에서 같은 shot 좌표의 값을 ${mapAggregation}로 집계합니다.`)}</div>}
        {chartType==="line"&&<div style={{fontSize:12,color:chart?.error?"#b91c1c":"#475569",margin:"0 0 9px"}}>{chart?.error||(TREND_GRAINS.find(g=>g.key===trendGrain)?.desc||"")}</div>}
        {chartType==="radius"&&<div style={{fontSize:12,color:chart?.error?"#b91c1c":"#475569",margin:"0 0 9px"}}>{chart?.error||(radiusBusy?"Chip_Radius.csv를 불러오는 중입니다.":`${radiusLayout?.file||"Chip_Radius.csv"} · ${radiusLayout?.mask||radiusSource?.product||"-"} · 좌표 ${chart?.radius_matched||0}/${chart?.radius_source_count||0} shot 매칭 · ${chart?.radius_mapping||""}`)}</div>}
        {displayChart?.error?<div style={{padding:14,border:"1px solid #fecaca",borderRadius:8,background:"#fff7f7",color:"#b91c1c"}}>{displayChart.error}</div>:displayChart&&chartType==="wafer_map"?<div style={{width:chartWidth?`min(100%, ${chartWidth}px)`:"100%",margin:"0 auto"}}><TegValueWaferMap vehicle={displayChart.product} points={displayChart.points} panels={displayChart.panels} title={displayChart.title||"WF MAP"} valueLabel={displayChart.y_label} palette={waferPalette} low={waferLow} center={waferCenter} high={waferHigh} onScaleChange={scale=>{setWaferPalette(scale.palette);setWaferLow(text(scale.low));setWaferCenter(text(scale.center));setWaferHigh(text(scale.high));}}/></div>:displayChart&&trellisCol&&!chartType.startsWith("bar")?<TrellisPlot chart={{...displayChart,width:chartWidth,height:chartHeight}} column={trellisCol} enableHighlight={highlightEnabled}/>:displayChart&&<FlowPlotlyChart chart={displayChart} cfg={{...displayChart,width:chartWidth,height:chartHeight,hide_title:!text(chartTitle).trim(),emphasize_axes:true,hide_x_ticks:boxStatsAligned}} dark={false} enableHighlight={highlightEnabled} onGeometry={chartType==="box"?setBoxGeometry:null}/>}
        {boxStatsOn&&<BoxStatsTable boxes={boxBuckets} valueLabel={displayChart?.y_label||yCol} geometry={boxAlignGeometry}/>}</div>}
    </div>}
  </div>;
}
