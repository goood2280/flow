import { useCallback, useEffect, useMemo, useRef, useState } from "react";
import { FlowPlotlyChart } from "../../components/PlotlyChart";
import TegValueWaferMap from "../../components/TegValueWaferMap";
import { chartPalette } from "../../components/UXKit";
import SpreadsheetPasteGrid, { normalizeSpreadsheetRows, spreadsheetTextFromRows } from "../../components/SpreadsheetPasteGrid";
import PageGear from "../../components/PageGear";
import { toast } from "../../components/Toast";
import Plotly from "../../lib/plotlyCustom";
import { computeBoxStats } from "../../lib/boxStats";
import { chartColorMap, chartColorValue, parseChartColorRules } from "../../lib/chartColorRules";
import { chartColorListRules, parseChartColorList } from "../../lib/chartColorList";
import { postDownload, postJson, sf } from "../../lib/api";
import { canManagePage } from "../../lib/permissions";

const API="/api/template-report";
// 슬라이드 규약은 PPTX(backend/routers/template_report.py)와 같은 값이어야 한다 —
// 화면에서 맞춰 놓은 자리가 파일에서 달라지면 템플릿이 의미를 잃는다.
const IBM_TEXT="#171717",IBM_MUTED="#737373",IBM_PAGE="#FAFAFA",IBM_PANEL="#FFFFFF",IBM_SUBTLE="#F5F5F5",IBM_BORDER="#E5E5E5",IBM_ACCENT="#E25822",HEADER_HEIGHT_PCT=0.42/7.5*100,SLIDE_DESIGN_WIDTH=1920,SLIDE_DESIGN_HEIGHT=1080,DEFAULT_CHART_WIDTH=1200,DEFAULT_CHART_HEIGHT=650;
// PPT 안에서 차트의 물리적 크기는 그대로 두고 PNG 픽셀 밀도만 2배로 만든다.
// python-pptx는 전달받은 PNG 원본을 그대로 보관하므로 여기서 올린 해상도가 최종 품질을 결정한다.
const PPTX_CHART_CAPTURE_SCALE=2;
const DEFAULT_BLOCK_WIDTH_PCT=46,DEFAULT_BLOCK_HEIGHT_PCT=26;
const REPORT_QUERY_CONCURRENCY=2;
const card={border:"1px solid var(--border)",borderRadius:9,background:"var(--bg-secondary)"};
const input={width:"100%",boxSizing:"border-box",border:"1px solid var(--border)",borderRadius:6,background:"var(--bg-primary)",color:"var(--text-primary)",padding:"8px 9px",fontSize:13};
const btn={border:"1px solid var(--border)",borderRadius:6,background:"var(--bg-tertiary)",color:"var(--text-primary)",padding:"7px 10px",fontWeight:800,cursor:"pointer"};
const primary={...btn,background:"var(--accent)",borderColor:"var(--accent)",color:"#fff"};
const label={fontSize:11,fontWeight:800,color:"var(--text-secondary)"};
const KIND_LABEL={chart:"차트",split:"Split 표",text:"글",stats:"통계표",legend:"공통 Legend"};
const CHART_BUILDER_TRANSFER_KEY="flow:chartbuilder:definition-transfer";
const COLOR_LIST_COLUMNS=["root_lot_id","wafer_id","color"];
const COLOR_LIST_ALIASES={root_lot:"root_lot_id",rootlotid:"root_lot_id",lot:"root_lot_id",wafer:"wafer_id",wf:"wafer_id",colour:"color",색상:"color",색:"color"};

function text(value){return value==null?"":String(value);}
function defaultPageSubtitle(user,date=new Date()){
  const stamp=`${date.getFullYear()}-${String(date.getMonth()+1).padStart(2,"0")}-${String(date.getDate()).padStart(2,"0")}`;
  return `${stamp} ${text(user?.username).trim()}`.trim();
}
function listValues(value){return text(value).split(/[,\n]+/).map(item=>item.trim()).filter((item,index,all)=>item&&all.findIndex(other=>other.toLowerCase()===item.toLowerCase())===index).slice(0,200);}
function dataRequestKey(request){return JSON.stringify({sources:request?.sources||[],joins:request?.joins||[],max_rows:Number(request?.max_rows)||10000});}
async function runPool(items,worker,limit=3){
  let cursor=0;
  const runners=Array.from({length:Math.min(Math.max(1,limit),items.length)},async()=>{
    while(cursor<items.length){const index=cursor++;await worker(items[index],index);}
  });
  await Promise.all(runners);
}
// 저장 차트의 기본 시간 창을 표시하고, 실행 컨텍스트를 켜면 공통 창으로 덮어쓴다.
function windowLabel(item){const days=Number(item?.recent_days)||0;return days?`최근 ${days}일`:"전체 기간";}
function clone(value){return JSON.parse(JSON.stringify(value));}
function clamp(value,min,max){return Math.min(max,Math.max(min,value));}
const legacyLayouts={
  1:{x:3.4,y:13.6},2:{x:51.5,y:13.6},3:{x:3.4,y:54.4},4:{x:51.5,y:54.4},
};
function newPage(index){return{id:`page_${Date.now()}_${index}`,title:"TITLE",subtitle:"",slots:[]};}
function chartDimensions(slot){let width=clamp(Number(slot?.chart_width)||DEFAULT_CHART_WIDTH,320,2400),height=clamp(Number(slot?.chart_height)||DEFAULT_CHART_HEIGHT,240,1600);const scale=Math.min(1,SLIDE_DESIGN_WIDTH/width,SLIDE_DESIGN_HEIGHT/height);return{chart_width:Math.round(width*scale),chart_height:Math.round(height*scale)};}
// 버튼으로 배치 종류를 고르지 않는다. 차트를 추가하는 순간 현재 개수에 맞춰 읽기 좋은
// 기본 구성을 잡고, 엔지니어는 이후 번호 드래그/크기 입력으로 필요한 부분만 고친다.
function autoChartLayout(count,index){
  if(count<=1)return{x:4,y:10,chart_width:1760,chart_height:850};
  if(count===2)return{x:index?52:4,y:12,chart_width:870,chart_height:810};
  if(count===3){
    if(index===0)return{x:4,y:10,chart_width:1760,chart_height:360};
    return{x:index===1?4:52,y:48,chart_width:870,chart_height:430};
  }
  if(count===4)return{x:index%2?52:4,y:index<2?12:53,chart_width:870,chart_height:390};
  if(count===5){
    if(index===0)return{x:4,y:9,chart_width:1760,chart_height:300};
    const cell=index-1;
    return{x:cell%2?52:4,y:cell<2?39:68,chart_width:870,chart_height:280};
  }
  const column=index%3,row=Math.floor(index/3);
  return{x:[4,35.5,67][column],y:row===0?12:53,chart_width:560,chart_height:390};
}
function slotKind(slot){const kind=text(slot?.kind||"chart");return KIND_LABEL[kind]?kind:"chart";}
function slotLayout(slot){
  const fallback=legacyLayouts[Number(slot?.position)]||{x:5,y:16};
  const x=Number(slot?.x??fallback.x),y=Number(slot?.y??fallback.y);
  if(slotKind(slot)!=="chart"){
    const width=clamp(Number(slot?.width)||DEFAULT_BLOCK_WIDTH_PCT,8,100),height=clamp(Number(slot?.height)||DEFAULT_BLOCK_HEIGHT_PCT,6,100);
    return{x,y,width,height,chart_width:Math.round(width/100*SLIDE_DESIGN_WIDTH),chart_height:Math.round(height/100*SLIDE_DESIGN_HEIGHT)};
  }
  const size=chartDimensions(slot);
  return{x,y,width:size.chart_width/SLIDE_DESIGN_WIDTH*100,height:size.chart_height/SLIDE_DESIGN_HEIGHT*100,...size};
}
function templateCodeFromDraft(draft,variables=[]){
  if(!draft)return"";
  const pages=(draft.pages||[]).map((page,pageIndex)=>({
    id:page.id||`page_${pageIndex+1}`,
    title:page.title||`Page ${pageIndex+1}`,
    subtitle:page.subtitle||"",
    slots:(page.slots||[]).map(slot=>{
      const kind=slotKind(slot),layout=slotLayout(slot);
      const base={position:Number(slot.position),kind,title:slot.title||"",x:layout.x,y:layout.y};
      if(kind==="chart")return{...base,chart_id:slot.chart_id||"",chart_name:slot.chart_name||slot.chart_label||"",chart_width:layout.chart_width,chart_height:layout.chart_height,definition_code:slot.definition_code||""};
      const sized={...base,width:layout.width,height:layout.height};
      if(kind==="split")return{...sized,product:slot.product||"",lot:slot.lot||"",columns:slot.columns||"",display_mode:slot.display_mode||"matrix"};
      if(kind==="text")return{...sized,text:slot.text||""};
      if(kind==="legend")return{...sized,source_position:Number(slot.source_position)||1};
      return{...sized,source_position:Number(slot.source_position)||1,stats:slot.stats||"n,mean,median,std"};
    }),
  }));
  return JSON.stringify({
    $schema:"flow-template-report/v1",
    id:draft.id||"",
    name:draft.name||"Template Report",
    options:{cover:draft.options?.cover!==false,footer:draft.options?.footer!==false,subtitle:text(draft.options?.subtitle),repeat_variable:text(draft.options?.repeat_variable||"LOT")},
    variables:(variables||draft.variables||[]).map(item=>({name:item.name,label:item.label||item.name,default:item.default||""})),
    pages,
  },null,2);
}
function formatTime(value){const date=new Date(value);return Number.isNaN(date.getTime())?text(value):date.toLocaleString("ko-KR",{hour12:false});}
function templateApiError(error){
  const message=error?.message||String(error||"");
  return message.includes("API not found")
    ?"Template Report API가 로드되지 않았습니다. 최신 setup.py를 적용한 뒤 Flow 백엔드를 재시작해 주세요. 재시작 시 누락된 Template Report 라우터를 번들에서 자동 복원합니다."
    :message;
}
function median(values){const sorted=values.filter(Number.isFinite).sort((a,b)=>a-b);if(!sorted.length)return NaN;const mid=(sorted.length-1)/2,lo=Math.floor(mid),hi=Math.ceil(mid);return(sorted[lo]+sorted[hi])/2;}
function aggregateValues(values,method="median"){
  const sorted=values.map(Number).filter(Number.isFinite).sort((a,b)=>a-b);if(!sorted.length)return NaN;
  if(method==="avg")return sorted.reduce((sum,value)=>sum+value,0)/sorted.length;
  if(method==="min")return sorted[0];if(method==="max")return sorted[sorted.length-1];if(method==="sum")return sorted.reduce((sum,value)=>sum+value,0);if(method==="count")return sorted.length;
  const quantile=method==="p10"?.1:method==="p90"?.9:.5,position=(sorted.length-1)*quantile,lower=Math.floor(position),upper=Math.ceil(position);
  return sorted[lower]+(sorted[upper]-sorted[lower])*(position-lower);
}
function reportTimeBucket(value,mode){
  const raw=text(value).trim(),parsed=new Date(raw.replace(" ","T"));if(!raw||Number.isNaN(parsed.getTime()))return raw.slice(0,10);
  if(mode==="weekly")parsed.setDate(parsed.getDate()-((parsed.getDay()+6)%7));
  return `${parsed.getFullYear()}-${String(parsed.getMonth()+1).padStart(2,"0")}-${String(parsed.getDate()).padStart(2,"0")}`;
}
function reportLinearFit(points){
  if(points.length<2)return null;const n=points.length,sx=points.reduce((a,p)=>a+p.x,0),sy=points.reduce((a,p)=>a+p.y,0),sxx=points.reduce((a,p)=>a+p.x*p.x,0),sxy=points.reduce((a,p)=>a+p.x*p.y,0),den=n*sxx-sx*sx;
  if(!den)return null;const slope=(n*sxy-sx*sy)/den,intercept=(sy-slope*sx)/n;return{slope,intercept,equation:`y = ${slope.toFixed(4)}x ${intercept<0?"-":"+"} ${Math.abs(intercept).toFixed(4)}`};
}
// 저장된 코드 안의 {{LOT}} 같은 토큰 — 서버(core/report_variables)와 같은 문법이다.
const VARIABLE_RE=/\{\{\s*([A-Za-z_][A-Za-z0-9_]*)\s*(?:\|\s*[A-Za-z_]+\s*)?\}\}/g;
function usedVariables(...texts){
  const found=[];
  texts.forEach(value=>{const raw=text(value);let match;VARIABLE_RE.lastIndex=0;while((match=VARIABLE_RE.exec(raw))){if(!found.includes(match[1]))found.push(match[1]);}});
  return found;
}
function templateVariableNames(draft,charts){
  const codes=(draft?.pages||[]).flatMap(page=>(page.slots||[]).map(slot=>{
    if(slotKind(slot)!=="chart")return[slot.text,slot.product,slot.lot,slot.columns,slot.title].map(text).join("\n");
    const chart=charts.find(item=>item.id===slot.chart_id);
    return[slot.title,slot.definition_code,(chart?.variables||[]).map(name=>`{{${name}}}`).join(" ")].map(text).join("\n");
  }));
  const titles=(draft?.pages||[]).flatMap(page=>[page.title,page.subtitle]);
  return usedVariables(draft?.options?.subtitle,...titles,...codes);
}

function chartAxes(run){
  const rows=run?.result?.joined?.rows||[],columns=run?.result?.joined?.columns||[],config=run?.definition?.chart||{};
  const numeric=columns.filter(column=>rows.slice(0,80).some(row=>text(row[column]).trim()!==""&&Number.isFinite(Number(row[column]))));
  const x=config.x&&columns.includes(config.x)?config.x:columns.find(column=>column==="tkout_time")||columns[0];
  const y=config.y&&columns.includes(config.y)?config.y:numeric.find(column=>column==="value")||numeric[0];
  return{rows,columns,config,x,y};
}

function reportChart(run){
  const{rows,columns,config,x,y}=chartAxes(run);
  if(!rows.length)return null;
  let type=text(config.type||"scatter").toLowerCase();
  const custom=["custom","__custom__"].includes(text(config.color).toLowerCase());
  const rules=custom?parseChartColorRules(config.color_rules).filter(rule=>!rule.error):[];
  const requestedColor=custom?"Custom Color":text(config.color);
  const trellis=text(config.trellis);
  const colorBy=requestedColor||(trellis?trellis:"");
  const colorMap=custom?chartColorMap(rules,config.color_else||"gray"):{};
  const showLegend=config.show_legend!==false;
  const colorValue=row=>{
    if(custom)return chartColorValue(row,rules);
    if(config.color&&columns.includes(config.color))return row[config.color];
    return trellis&&columns.includes(trellis)?row[trellis]:"";
  };
  const title=text(config.title).trim()||[x,y].filter(Boolean).join(" × ")||run.chart_label||run.chart_id;
  const details={
    point_size:Number(config.point_size)||10,marker_opacity:Number(config.marker_opacity)||.82,line_width:Number(config.line_width)||2.3,
    x_min:text(config.x_min).trim(),x_max:text(config.x_max).trim(),y_min:text(config.y_min).trim(),y_max:text(config.y_max).trim(),y_scale:config.y_scale||"linear",show_grid:config.show_grid!==false,
    legend_position:config.legend_position||"bottom",box_points:config.box_points||"outliers",show_legend:showLegend,
    wafer_mode:config.wafer_mode||"value",wafer_spec_low:config.wafer_spec_low,wafer_spec_high:config.wafer_spec_high,
  };
  const labels={x_label:text(config.x_label).trim()||x,y_label:text(config.y_label).trim()||y};
  const decorate=row=>({...row,spec_low:config.spec_low&&columns.includes(config.spec_low)?row[config.spec_low]:row.spec_low,spec_high:config.spec_high&&columns.includes(config.spec_high)?row[config.spec_high]:row.spec_high});
  if(type==="wafer_map"){
    const mapY=config.map_y&&columns.includes(config.map_y)?config.map_y:columns.find(column=>["shot_y","chip_y_pos"].includes(column.toLowerCase()));
    const mapX=x&&columns.includes(x)?x:columns.find(column=>["shot_x","chip_x_pos"].includes(column.toLowerCase()));
    if(!mapX||!mapY||!y)return null;
    const lotCol=columns.find(column=>column.toLowerCase()==="root_lot_id")||"",waferCol=columns.find(column=>column.toLowerCase()==="wafer_id")||"";
    const scope=config.map_scope||"root_wafer",aggregation=config.aggregation||"median",grouped=new Map();
    rows.forEach(row=>{const lot=lotCol?text(row[lotCol]):"",wafer=waferCol?text(row[waferCol]):"",key=scope==="trellis_wafer"?wafer:`${lot}|${wafer}`,label=scope==="root_lot"?lot:scope==="trellis_wafer"?`W${wafer}`:`${lot} | W${wafer}`;if(!grouped.has(key))grouped.set(key,{key,label,lot,wafer,shots:new Map()});const group=grouped.get(key),coord=`${Number(row[mapX])},${Number(row[mapY])}`,shot=group.shots.get(coord)||{x:Number(row[mapX]),y:Number(row[mapY]),values:[]};const value=Number(row[y]);if(Number.isFinite(shot.x)&&Number.isFinite(shot.y)&&Number.isFinite(value)){shot.values.push(value);group.shots.set(coord,shot);}});
    const groups=[...grouped.values()].map(group=>({...group,points:[...group.shots.values()].map(shot=>({x:shot.x,y:shot.y,value:aggregateValues(shot.values,aggregation),n:shot.values.length}))})).filter(group=>group.points.length);
    const trellis=scope.startsWith("trellis_"),selected=groups.find(group=>group.key===config.map_target)||groups[0],source=(run?.result?.sources||[]).find(item=>item.product)||{};
    return{chart_type:"wafer_map",title,x_label:labels.x_label,map_y_label:mapY,y_label:labels.y_label,product:source.product||"",points:trellis?[]:(selected?.points||[]),panels:trellis?groups.map(group=>({key:group.key,label:group.label,points:group.points})):null,aggregation,map_scope:scope,map_target:selected,...details,wafer_palette:config.wafer_palette||"blue_gray_red",wafer_low:config.wafer_low,wafer_center:config.wafer_center,wafer_high:config.wafer_high};
  }
  if(type==="pie"||type==="donut"){
    const basis=config.pie_basis||"count",buckets=new Map();rows.forEach(row=>{const label=text(row[x]).trim()||"(빈값)",value=basis==="sum"?Number(row[y]):1;if(Number.isFinite(value))buckets.set(label,(buckets.get(label)||0)+value);});
    return{chart_type:type,title,x_label:labels.x_label,y_label:basis==="sum"?labels.y_label:"행 수",groups:[...buckets.entries()].map(([label,value])=>({label,value,count:value})),aggregation:basis,...details};
  }
  if(type==="bar"||type==="bar_horizontal"){
    const buckets=new Map();rows.forEach(row=>{const label=text(row[x]),value=Number(row[y]);if(!label||!Number.isFinite(value))return;if(!buckets.has(label))buckets.set(label,[]);buckets.get(label).push(value);});
    const aggregation=config.aggregation||"median";
    return{chart_type:type,title,x_label:labels.x_label,y_label:labels.y_label,groups:[...buckets.entries()].map(([label,values])=>({label,value:aggregateValues(values,aggregation),count:values.length})),aggregation,...details};
  }
  if(!x||!y)return null;
  const pointFrom=(row,xValue,yValue)=>({...decorate(row),x:xValue,x_label:xValue,y:Number(yValue),color_value:colorValue(row),trellis_value:trellis?row[trellis]:""});
  let points=[];
  if(type==="line"&&["daily","weekly"].includes(config.trend_grain)){
    const buckets=new Map();rows.forEach(row=>{const period=reportTimeBucket(row[x],config.trend_grain),series=text(colorValue(row)),key=`${series}\u001f${period}`;if(!period||!Number.isFinite(Number(row[y])))return;if(!buckets.has(key))buckets.set(key,{row,period,values:[]});buckets.get(key).values.push(Number(row[y]));});
    points=[...buckets.values()].map(bucket=>pointFrom(bucket.row,bucket.period,aggregateValues(bucket.values,config.aggregation||"median")));
  }else if(type==="line"&&config.trend_grain==="wafer"){
    const lot=columns.find(column=>column.toLowerCase()==="root_lot_id"),wafer=columns.find(column=>column.toLowerCase()==="wafer_id"),buckets=new Map();rows.forEach(row=>{const key=`${text(row[lot])}|${text(row[wafer])}`,value=Number(row[y]);if(!Number.isFinite(value))return;if(!buckets.has(key))buckets.set(key,{row,values:[]});buckets.get(key).values.push(value);});points=[...buckets.values()].map(bucket=>pointFrom(bucket.row,bucket.row[x],aggregateValues(bucket.values,config.aggregation||"median")));type="scatter";
  }else{
    points=rows.slice(0,10000).map((row,index)=>pointFrom(row,row[x]??index,row[y])).filter(point=>Number.isFinite(point.y));
    if(type==="line"&&(config.trend_grain||"shot")==="shot")type="scatter";
  }
  const numericFit=points.map((point,index)=>({x:Number(point.x),y:Number(point.y),index})).filter(point=>Number.isFinite(point.x)&&Number.isFinite(point.y));
  const fit=config.fit==="linear"?reportLinearFit(numericFit):null;
  return{chart_type:type==="radius"?"scatter":type,title,...labels,color_by:colorBy,color_map:colorMap,points,fit,cubic_fit:config.fit==="cubic",trend_grain:config.trend_grain||"",emphasize_markers:type==="scatter",...details};
}

/* 통계표 — 가리킨 차트의 결과를 그룹별로 갈라 숫자를 깐다. 그룹은 차트 코드가 정한
 * COLOR 열(없으면 X 열)이라, A/B 비교는 여기가 아니라 ChartBuilder 코드에서 만든다.
 * 그림은 median 과 IQR 만 말해 주므로 n·산포를 표로 함께 싣고, 그룹이 정확히 둘이면
 * Δ 와 Δ% 열을 덧붙여 A/B 판단을 바로 할 수 있게 한다.
 * 통계 계산은 box plot 통계표와 같은 lib/boxStats.js 를 쓴다(두 화면의 숫자가 같아야 한다). */
const STAT_LABEL={n:"Count",mean:"Mean",median:"Median",std:"StdDev",min:"Min",max:"Max",q1:"Q1",q3:"Q3",cv:"CV%",iqr:"IQR",range:"Range"};
const STAT_GROUP_LIMIT=8;
function statNumber(value,key){
  if(value==null||!Number.isFinite(Number(value)))return"—";
  const number=Number(value);
  if(key==="n")return String(Math.round(number));
  const abs=Math.abs(number);
  return abs>=1000||abs===0?number.toFixed(2):number.toPrecision(4);
}
function statsTable(block,runs){
  const run=runs[block.source_key];
  if(!run||run.error)return null;
  const{rows,columns,config,x,y}=chartAxes(run);
  if(!rows.length||!y)return null;
  // 그룹 열: 차트가 COLOR 로 지정한 열 > X 열. 둘 다 아니면 전체를 한 덩어리로 본다.
  const groupBy=[text(config.color),x].find(column=>column&&columns.includes(column)&&column!==y)||"";
  const buckets=new Map();
  rows.forEach(row=>{
    const value=Number(row[y]);
    if(!Number.isFinite(value))return;
    const key=groupBy?text(row[groupBy]).trim()||"(빈값)":"전체";
    if(!buckets.has(key))buckets.set(key,[]);
    buckets.get(key).push(value);
  });
  const groups=[...buckets.entries()].sort((a,b)=>a[0].localeCompare(b[0],"ko")).slice(0,STAT_GROUP_LIMIT);
  if(!groups.length)return null;
  const stats=groups.map(([name,values])=>[name,computeBoxStats(values)]);
  const keys=text(block.stats||"n,mean,median,std").split(",").map(item=>item.trim()).filter(item=>STAT_LABEL[item]);
  const pair=stats.length===2;
  const rowsOut=(keys.length?keys:["n","mean","median","std"]).map(key=>{
    const cells=stats.map(([,values])=>statNumber(values?.[key],key));
    if(!pair)return[STAT_LABEL[key],...cells];
    const left=Number(stats[0][1]?.[key]),right=Number(stats[1][1]?.[key]);
    const gap=Number.isFinite(left)&&Number.isFinite(right)?right-left:null;
    const percent=gap!=null&&Number.isFinite(left)&&left!==0?`${(gap/Math.abs(left)*100).toFixed(2)}%`:"—";
    return[STAT_LABEL[key],...cells,gap==null?"—":statNumber(gap,key),key==="n"?"—":percent];
  });
  const header=["통계",...stats.map(([name])=>name),...(pair?["Δ","Δ%"]:[])];
  const dropped=buckets.size-groups.length;
  return{
    columns:header,rows:rowsOut,
    title:block.title||`${y} 통계${groupBy?` · ${groupBy}별`:""}`,
    note:dropped>0?`그룹 ${buckets.size}개 중 앞 ${groups.length}개`:"",
  };
}

function legendTable(block,runs){
  const run=runs[block.source_key];
  if(!run||run.error)return null;
  const chart=reportChart(run);
  if(!chart)return null;
  const counts=new Map();
  (chart.points||[]).forEach(point=>{const name=text(point.color_value).trim()||"Series";counts.set(name,(counts.get(name)||0)+1);});
  if(!counts.size)(chart.groups||[]).forEach(group=>{const name=text(group.label).trim()||"Series";counts.set(name,Number(group.count)||0);});
  const rows=[...counts.entries()].slice(0,24).map(([name,count],index)=>[
    name,
    chart.color_map?.[name]||chartPalette.series[index%chartPalette.series.length],
    String(count),
  ]);
  if(!rows.length)return null;
  return{title:block.title||chart.color_by||"Legend",columns:["Label","Color","Count"],rows,note:counts.size>rows.length?`${counts.size}개 중 앞 ${rows.length}개`:""};
}

function LegendBlock({table}){
  if(!table)return null;
  return <div style={{height:"100%",display:"flex",flexDirection:"column",minHeight:0,color:"#1f2937"}}>
    {table.title&&<div style={{fontSize:12,fontWeight:600,color:IBM_TEXT,marginBottom:4}}>{table.title}</div>}
    <div style={{display:"flex",flexWrap:"wrap",gap:"5px 12px",alignContent:"flex-start",overflow:"hidden"}}>
      {(table.rows||[]).map((row,index)=><div key={`${row?.[0]}-${index}`} style={{display:"flex",alignItems:"center",gap:5,minWidth:0,fontSize:10,whiteSpace:"nowrap"}}>
        <span aria-hidden="true" style={{width:10,height:10,borderRadius:2,background:text(row?.[1])||chartPalette.series[index%chartPalette.series.length],flex:"0 0 auto"}}/>
        <span style={{overflow:"hidden",textOverflow:"ellipsis"}}>{text(row?.[0])}</span>
        {text(row?.[2])&&<span style={{color:IBM_MUTED}}>({text(row?.[2])})</span>}
      </div>)}
    </div>
  </div>;
}

function BlockTable({table,dense=false}){
  if(!table)return null;
  const columns=table.columns||[],rows=table.rows||[];
  const cell={padding:dense?"2px 6px":"4px 8px",borderBottom:`1px solid ${IBM_BORDER}`,whiteSpace:"nowrap",overflow:"hidden",textOverflow:"ellipsis",fontSize:dense?9:11,color:IBM_TEXT};
  return <div style={{height:"100%",display:"flex",flexDirection:"column",minHeight:0}}>
    {table.title&&<div style={{fontSize:12,fontWeight:600,color:IBM_TEXT,padding:"0 0 4px"}}>{table.title}</div>}
    <div style={{flex:1,overflow:"auto",border:`1px solid ${IBM_BORDER}`,borderRadius:0,background:IBM_PANEL,minHeight:0}}>
      <div style={{display:"grid",gridTemplateColumns:`minmax(90px,1.6fr) repeat(${Math.max(0,columns.length-1)},minmax(46px,1fr))`}}>
        {columns.map((column,index)=><div key={`h-${index}`} style={{...cell,background:IBM_SUBTLE,color:IBM_TEXT,fontWeight:600,position:"sticky",top:0,textAlign:"left"}}>{text(column)}</div>)}
        {rows.map((row,rowIndex)=>columns.map((_column,columnIndex)=><div key={`c-${rowIndex}-${columnIndex}`} style={{...cell,background:rowIndex%2?IBM_PAGE:IBM_PANEL,fontWeight:columnIndex?400:600,textAlign:"left"}}>{text(row?.[columnIndex])}</div>))}
      </div>
    </div>
    {table.note&&<div style={{fontSize:9,color:IBM_MUTED,paddingTop:3}}>{table.note}</div>}
  </div>;
}

/* Plotly는 PPT 캡처용 원본 픽셀로 그리고, 화면에서는 슬롯 크기에 맞춰 같은 비율로
 * 축소한다. 슬롯만 작아지고 Plotly 높이는 원본으로 남으면 아래 축·레전드가 잘린다. */
function ReportChart({chart,layout}){
  const hostRef=useRef(null),[hostSize,setHostSize]=useState({width:0,height:0});
  useEffect(()=>{
    const host=hostRef.current;if(!host)return undefined;
    const measure=()=>setHostSize({width:host.clientWidth,height:host.clientHeight});
    measure();
    const observer=new ResizeObserver(measure);observer.observe(host);
    return()=>observer.disconnect();
  },[]);
  const width=Math.max(320,Number(layout.chart_width)||DEFAULT_CHART_WIDTH),height=Math.max(240,Number(layout.chart_height)||DEFAULT_CHART_HEIGHT);
  const scale=hostSize.width&&hostSize.height?Math.min(hostSize.width/width,hostSize.height/height):0;
  return <div ref={hostRef} style={{width:"100%",height:"100%",overflow:"hidden",position:"relative"}}>
    {!!scale&&<div style={{position:"absolute",left:"50%",top:"50%",width,height,transform:`translate(-50%, -50%) scale(${scale})`,transformOrigin:"center center"}}>
      {chart.chart_type==="wafer_map"?<TegValueWaferMap vehicle={chart.product} points={chart.points} panels={chart.panels} title={chart.title||"WF MAP"} valueLabel={chart.y_label} palette={chart.wafer_palette} low={chart.wafer_low} center={chart.wafer_center} high={chart.wafer_high} mode={chart.wafer_mode} specLow={chart.wafer_spec_low} specHigh={chart.wafer_spec_high} interactive={false}/>:<FlowPlotlyChart chart={chart} cfg={{...chart,width,height,point_size:12,compact:true,hide_title:true,emphasize_axes:true,axis_title_size:22,axis_line_width:2.6,tick_font_size:13}} height={height} dark={false}/>}
    </div>}
  </div>;
}

function SlideCanvas({page,pageIndex,runs={},tables={},editing=false,charts=[],defaultSubtitle="",backgroundImage="",updatePage,updateSlot,removeSlot,addSlot}){
  const canvasRef=useRef(null),gestureRef=useRef(null);
  const[selectedPosition,setSelectedPosition]=useState(null),[chartRef,setChartRef]=useState("");
  const blocks=page.blocks||(page.slots||[]).map(slot=>({...slot,key:`${pageIndex}:${slot.position}`,...slotLayout(slot)}));
  const beginGesture=(event,block)=>{
    if(!editing)return;
    event.preventDefault();event.stopPropagation();
    event.currentTarget.setPointerCapture?.(event.pointerId);
    gestureRef.current={position:Number(block.position),pointerId:event.pointerId,startX:event.clientX,startY:event.clientY,layout:slotLayout(block)};
    setSelectedPosition(Number(block.position));
  };
  const moveGesture=event=>{
    const gesture=gestureRef.current,rect=canvasRef.current?.getBoundingClientRect();if(!gesture||!rect?.width||!rect?.height)return;
    const dx=(event.clientX-gesture.startX)/rect.width*100,dy=(event.clientY-gesture.startY)/rect.height*100,base=gesture.layout;
    updateSlot(pageIndex,gesture.position,{x:clamp(base.x+dx,0,100-base.width),y:clamp(base.y+dy,0,100-base.height)});
  };
  const endGesture=event=>{if(gestureRef.current?.pointerId===event.pointerId)gestureRef.current=null;};
  const nextPosition=()=>Math.max(0,...(page.slots||[]).map(slot=>Number(slot.position)||0))+1;
  const guardRoom=()=>{
    if((page.slots||[]).length>=20){toast.error("한 페이지에는 블록을 최대 20개까지 배치할 수 있습니다.");return false;}
    return true;
  };
  const appendChart=chart=>{
    if(!chart){toast.error("저장된 Chart ID 또는 Name을 선택해 주세요.");return;}
    if(!guardRoom())return;
    const position=nextPosition();
    const size=chartDimensions({chart_width:Number(chart.chart?.width)||DEFAULT_CHART_WIDTH,chart_height:Number(chart.chart?.height)||DEFAULT_CHART_HEIGHT});
    const next={position,kind:"chart",chart_id:chart.id,chart_name:chart.name,chart_label:chart.name||chart.label||chart.id,definition_code:chart.definition_code||"",title:"",x:5,y:16,...size};
    const chartSlots=[...(page.slots||[]).filter(slot=>slotKind(slot)==="chart"),next];
    chartSlots.forEach((slot,index)=>{
      const layout=autoChartLayout(chartSlots.length,index);
      if(Number(slot.position)===position)Object.assign(next,layout);
      else updateSlot(pageIndex,slot.position,layout);
    });
    addSlot(pageIndex,next);
    setSelectedPosition(position);setChartRef("");
  };
  const addChart=()=>{
    const value=text(chartRef).trim(),chart=charts.find(item=>item.id===value||text(item.name).toLowerCase()===value.toLowerCase());
    appendChart(chart);
  };
  const paletteQuery=text(chartRef).trim().toLowerCase();
  const visibleCharts=charts.filter(chart=>!paletteQuery||`${chart.name} ${chart.id} ${chart.label} ${chart.chart?.type||""}`.toLowerCase().includes(paletteQuery)).slice(0,40);
  const addBlock=kind=>{
    if(!guardRoom())return;
    const position=nextPosition(),offset=((page.slots||[]).length%5)*3;
    const base={position,kind,title:"",x:clamp(5+offset,0,100-DEFAULT_BLOCK_WIDTH_PCT),y:clamp(58+offset,0,100-DEFAULT_BLOCK_HEIGHT_PCT),width:DEFAULT_BLOCK_WIDTH_PCT,height:DEFAULT_BLOCK_HEIGHT_PCT};
    if(kind==="split")Object.assign(base,{product:"{{PRODUCT}}",lot:"{{LOT}}",columns:"",display_mode:"matrix"});
    if(kind==="text")Object.assign(base,{text:"내용을 입력하세요. {{LOT}} 같은 변수를 쓸 수 있습니다."});
    if(kind==="stats")Object.assign(base,{source_position:(page.slots||[]).find(slot=>slotKind(slot)==="chart")?.position||1,stats:"n,mean,median,std"});
    if(kind==="legend")Object.assign(base,{title:"Legend",source_position:(page.slots||[]).find(slot=>slotKind(slot)==="chart")?.position||1,x:70,y:8,width:26,height:11});
    addSlot(pageIndex,base);
    setSelectedPosition(position);
  };
  const titleStyle={position:"absolute",left:0,top:0,width:"100%",height:`${HEADER_HEIGHT_PCT}%`,background:IBM_PAGE,borderBottom:`1px solid ${IBM_ACCENT}`,zIndex:30,display:"flex",alignItems:"center",gap:8,padding:"0 1.8%",boxSizing:"border-box"};
  return <section style={editing?{...card,padding:13}:{}}>
    {editing&&<div style={{display:"flex",gap:8,alignItems:"center",marginBottom:10,flexWrap:"wrap"}}>
      <strong>Page {pageIndex+1}</strong>
      <input aria-label={`Page ${pageIndex+1} 차트 선택`} list="template-report-chart-ids" value={chartRef} onChange={event=>setChartRef(event.target.value)} onKeyDown={event=>{if(event.key==="Enter")addChart();}} placeholder="Chart ID 또는 Name" style={{...input,flex:"1 1 220px",fontFamily:"monospace"}}/>
      <button type="button" onClick={addChart} style={primary}>＋ 차트</button>
      <button type="button" onClick={()=>addBlock("split")} style={btn}>＋ Split 표</button>
      <button type="button" onClick={()=>addBlock("text")} style={btn}>＋ 글</button>
      <button type="button" onClick={()=>addBlock("stats")} style={btn}>＋ 통계표</button>
      <button type="button" onClick={()=>addBlock("legend")} style={btn}>＋ 공통 Legend</button>
    </div>}
    {editing&&<div style={{display:"grid",gap:8,marginBottom:10,padding:10,border:"1px solid var(--border)",borderRadius:8,background:"var(--bg-primary)"}}>
      <div>
        <div style={{display:"flex",alignItems:"baseline",gap:7,marginBottom:6}}><strong style={{fontSize:12}}>사용 가능한 차트</strong><span style={{fontSize:10,color:"var(--text-secondary)"}}>{visibleCharts.length} / {charts.length}개 표시 · 클릭하면 추가</span></div>
        <div style={{display:"grid",gridTemplateColumns:"repeat(auto-fill,minmax(210px,1fr))",gap:6,maxHeight:180,overflowY:"auto"}}>
          {visibleCharts.map(chart=><button type="button" key={chart.id} onClick={()=>appendChart(chart)} style={{...btn,textAlign:"left",padding:"7px 8px",background:"var(--bg-secondary)"}}>
            <div style={{display:"flex",gap:6,alignItems:"center"}}><span style={{fontSize:9,fontWeight:900,color:"var(--accent)",textTransform:"uppercase"}}>{chart.chart?.type||"chart"}</span><strong style={{fontSize:11,overflow:"hidden",textOverflow:"ellipsis",whiteSpace:"nowrap"}}>{chart.name}</strong></div>
            <div style={{fontSize:9,color:"var(--text-secondary)",marginTop:3,overflow:"hidden",textOverflow:"ellipsis",whiteSpace:"nowrap"}}>{chart.label} · {windowLabel(chart)}</div>
          </button>)}
        </div>
      </div>
    </div>}
    <div ref={canvasRef} aria-label={`Page ${pageIndex+1} 자유 배치 슬라이드`} onPointerMove={moveGesture} onPointerUp={endGesture} onPointerCancel={endGesture} style={{position:"relative",backgroundColor:IBM_PAGE,backgroundImage:backgroundImage?`url(${JSON.stringify(backgroundImage)})`:"none",backgroundPosition:"center",backgroundSize:"cover",backgroundRepeat:"no-repeat",color:IBM_TEXT,aspectRatio:"16 / 9",overflow:"hidden",boxSizing:"border-box",boxShadow:"none",border:`1px solid ${editing?"#A3A3A3":IBM_BORDER}`,touchAction:"none",userSelect:editing?"none":"auto"}}>
      {/* 내부 회의용 얇은 헤더 — 제목보다 차트와 축을 우선한다. */}
      <div style={titleStyle}>
        {editing
          ?<>
            <input aria-label={`Page ${pageIndex+1} 제목`} value={page.title} onChange={event=>updatePage(pageIndex,{title:event.target.value})} placeholder="TITLE" style={{flex:"1 1 auto",minWidth:0,border:`1px dashed ${IBM_MUTED}`,background:"transparent",color:IBM_TEXT,fontSize:"clamp(10px,1vw,15px)",fontWeight:600,padding:"0 .4%",boxSizing:"border-box"}}/>
            <input aria-label={`Page ${pageIndex+1} 우측 표기`} value={page.subtitle||""} onChange={event=>updatePage(pageIndex,{subtitle:event.target.value})} placeholder={`비우면 ${defaultSubtitle}`} style={{flex:"0 1 26%",minWidth:0,border:`1px dashed ${IBM_BORDER}`,background:"transparent",color:IBM_MUTED,fontSize:"clamp(8px,.72vw,10px)",textAlign:"right",padding:"0 .4%",boxSizing:"border-box"}}/>
          </>
          :<>
            <div style={{flex:"1 1 auto",minWidth:0,color:IBM_TEXT,fontSize:"clamp(10px,1vw,15px)",fontWeight:600,overflow:"hidden",whiteSpace:"nowrap",textOverflow:"ellipsis"}}>{page.title||"TITLE"}</div>
            {(page.subtitle||defaultSubtitle)&&<div style={{flex:"0 0 auto",color:IBM_MUTED,fontSize:"clamp(8px,.72vw,10px)",whiteSpace:"nowrap"}}>{page.subtitle||defaultSubtitle}</div>}
          </>}
      </div>
      {editing&&!blocks.length&&<div style={{position:"absolute",inset:"18% 8% 8%",border:"2px dashed #cbd5e1",borderRadius:10,display:"grid",placeItems:"center",textAlign:"center",color:"#94a3b8",fontSize:13,lineHeight:1.6}}>위에서 블록을 추가하세요.<br/>추가된 번호를 드래그하면 좌상단 위치가 정해집니다.</div>}
      {blocks.map(block=>{
        const layout=block.width!=null?block:slotLayout(block),position=Number(block.position),key=block.key||`${pageIndex}:${position}`;
        const kind=slotKind(block),run=runs[key],chart=kind==="chart"?reportChart(run):null,table=kind==="chart"?null:tables[key],active=editing&&position===Number(selectedPosition);
        if(editing)return <button type="button" key={key} aria-label={`${position}번 블록 위치`} title={`${position}. ${KIND_LABEL[kind]} · ${Math.round(layout.width*10)/10}%×${Math.round(layout.height*10)/10}%`} onPointerDown={event=>beginGesture(event,block)} onClick={()=>setSelectedPosition(position)} style={{position:"absolute",left:`${layout.x}%`,top:`${layout.y}%`,width:42,height:42,display:"grid",placeItems:"center",border:active?`3px solid ${IBM_ACCENT}`:`2px solid ${IBM_ACCENT}`,borderRadius:0,background:active?"#FDF2EB":IBM_PANEL,color:IBM_ACCENT,fontSize:18,fontWeight:600,cursor:"move",boxShadow:"none",zIndex:20}}>{position}</button>;
        return <div key={key} data-report-chart-key={key} style={{position:"absolute",left:`${layout.x}%`,top:`${layout.y}%`,width:`${layout.width}%`,height:`${layout.height}%`,minWidth:0,minHeight:0,border:kind==="chart"?(run?"none":`1px solid ${IBM_BORDER}`):["text","legend"].includes(kind)?"none":`1px solid ${IBM_BORDER}`,borderRadius:0,overflow:"hidden",background:kind==="text"?"transparent":IBM_PANEL,boxSizing:"border-box",zIndex:5,padding:kind==="chart"?0:6}}>
          {kind==="text"&&<div style={{fontSize:"clamp(9px,1vw,13px)",color:IBM_TEXT,whiteSpace:"pre-wrap",lineHeight:1.5}}>{block.title&&<div style={{fontWeight:600,color:IBM_TEXT,marginBottom:3}}>{block.title}</div>}{block.text}</div>}
          {kind==="legend"&&table&&<LegendBlock table={table}/>}
          {kind!=="chart"&&kind!=="text"&&kind!=="legend"&&table&&<BlockTable table={table} dense/>}
          {kind!=="chart"&&kind!=="text"&&!table&&<div style={{height:"100%",display:"grid",placeItems:"center",padding:8,textAlign:"center",fontSize:11,color:IBM_MUTED}}><div><strong>{position}. {KIND_LABEL[kind]}</strong><div style={{marginTop:4}}>{kind==="split"?`${block.product||"제품"} / ${block.lot||"랏"}`:kind==="legend"?"실행하면 공통 범례가 채워집니다.":"실행하면 표가 채워집니다."}</div></div></div>}
          {kind==="chart"&&!run&&<div style={{height:"100%",display:"flex",flexDirection:"column",padding:"2.2% 2.5%",boxSizing:"border-box",overflow:"hidden",background:IBM_PANEL,color:IBM_MUTED}}>
            <div style={{display:"flex",gap:5,alignItems:"baseline",minWidth:0,marginBottom:"1.5%"}}><strong style={{fontSize:"clamp(7px,.65vw,11px)",color:IBM_TEXT,overflow:"hidden",textOverflow:"ellipsis",whiteSpace:"nowrap"}}>{position}. {block.chart_label||block.chart_name||block.chart_id}</strong><code style={{marginLeft:"auto",fontSize:"clamp(5px,.42vw,7px)",color:IBM_MUTED,whiteSpace:"nowrap"}}>{layout.chart_width}×{layout.chart_height}</code></div>
            <pre style={{flex:1,minHeight:0,margin:0,overflow:"hidden",whiteSpace:"pre-wrap",wordBreak:"break-word",fontFamily:"'JetBrains Mono',monospace",fontSize:"clamp(5px,.47vw,8px)",lineHeight:1.28,color:IBM_MUTED,textAlign:"left"}}>{block.definition_code||block.chart_id||"차트 생성식 없음"}</pre>
          </div>}
          {kind==="chart"&&run?.error&&<div style={{height:"100%",display:"grid",placeItems:"center",padding:8,textAlign:"center",fontSize:11,color:"#b91c1c"}}>{run.error}</div>}
          {chart&&<ReportChart chart={chart} layout={layout}/>}
        </div>;
      })}
    </div>
    {editing&&<div style={{display:"grid",gap:6,marginTop:10}}>{(page.slots||[]).map(slot=>{
      const kind=slotKind(slot),layout=slotLayout(slot),active=Number(slot.position)===Number(selectedPosition);
      const patch=values=>updateSlot(pageIndex,slot.position,values);
      return <div key={slot.position} onClick={()=>setSelectedPosition(Number(slot.position))} style={{display:"grid",gap:7,padding:"8px 10px",border:`1px solid ${active?"var(--accent)":"var(--border)"}`,borderRadius:7,background:active?"var(--accent-glow)":"var(--bg-primary)",cursor:"pointer"}}>
        <div style={{display:"flex",gap:8,alignItems:"center",flexWrap:"wrap"}}>
          <span style={{width:27,height:27,display:"grid",placeItems:"center",borderRadius:0,background:IBM_ACCENT,color:"#fff",fontWeight:600}}>{slot.position}</span>
          <span style={{fontSize:10,fontWeight:900,color:"var(--accent)",border:"1px solid var(--accent)",borderRadius:999,padding:"1px 7px"}}>{KIND_LABEL[kind]}</span>
          {kind==="chart"?<><strong style={{fontSize:12}}>{slot.chart_name||slot.chart_id}</strong><code style={{fontSize:9,color:"var(--text-secondary)"}}>{slot.chart_id}</code></>
            :<input aria-label={`${slot.position}번 블록 제목`} value={slot.title||""} onChange={event=>patch({title:event.target.value})} placeholder="블록 제목(선택)" style={{...input,maxWidth:220,padding:"5px 7px",fontSize:12}}/>}
          <span style={{marginLeft:"auto",fontSize:10,color:"var(--text-secondary)"}}>{kind==="chart"?`${layout.chart_width}×${layout.chart_height}px`:`${Math.round(layout.width)}%×${Math.round(layout.height)}%`}</span>
          <button type="button" aria-label={`${slot.position}번 블록 삭제`} onClick={event=>{event.stopPropagation();removeSlot(pageIndex,slot.position);}} style={{...btn,padding:"4px 7px",color:"var(--danger)"}}>삭제</button>
        </div>
        {kind==="chart"&&<div style={{display:"flex",gap:7,flexWrap:"wrap",alignItems:"end"}}>
          <label style={label}>차트 폭 px<input type="number" min="320" max="2400" step="20" value={layout.chart_width} onChange={event=>patch({chart_width:Number(event.target.value)})} style={{...input,width:105,marginTop:3,padding:"5px 7px"}}/></label>
          <label style={label}>차트 높이 px<input type="number" min="240" max="1600" step="20" value={layout.chart_height} onChange={event=>patch({chart_height:Number(event.target.value)})} style={{...input,width:105,marginTop:3,padding:"5px 7px"}}/></label>
          <span style={{fontSize:10,color:"var(--text-secondary)"}}>슬라이드 비율에 맞춰 축소되며 PPT 원본 비율도 동일하게 유지됩니다.</span>
        </div>}
        {kind==="chart"&&<div style={{display:"grid",gap:6}}>
          <div style={{display:"flex",gap:7,alignItems:"center",flexWrap:"wrap"}}>
            <strong style={{fontSize:11}}>차트 생성식</strong>
            <span style={{fontSize:10,color:"var(--text-secondary)"}}>이 슬롯이 실행할 Query / JOIN / 차트 설정입니다. 복사해 다른 슬롯에 붙이거나 바로 수정할 수 있습니다.</span>
            <button type="button" onClick={async event=>{event.stopPropagation();try{await navigator.clipboard.writeText(text(slot.definition_code));toast.ok("차트 생성식을 복사했습니다.");}catch(_error){toast.error("복사하지 못했습니다. 코드 영역을 직접 선택해 주세요.");}}} style={{...btn,marginLeft:"auto",padding:"4px 8px",fontSize:11}}>복사</button>
            <button type="button" onClick={event=>{event.stopPropagation();try{window.sessionStorage.setItem(CHART_BUILDER_TRANSFER_KEY,JSON.stringify({definition_code:text(slot.definition_code),chart_name:text(slot.chart_name||slot.chart_label),source:"template-report",timestamp:Date.now()}));window.dispatchEvent(new CustomEvent("flow:navigate",{detail:{tab:"chartbuilder"}}));}catch(_error){toast.error("차트생성 화면으로 코드를 넘기지 못했습니다.");}}} style={{...primary,padding:"4px 8px",fontSize:11}}>차트생성에서 수정</button>
          </div>
          <textarea aria-label={`${slot.position}번 차트 생성식`} value={slot.definition_code||""} onClick={event=>event.stopPropagation()} onChange={event=>patch({definition_code:event.target.value})} rows={9} spellCheck={false} style={{...input,fontFamily:"'JetBrains Mono',monospace",fontSize:11,lineHeight:1.5,resize:"vertical",whiteSpace:"pre"}}/>
        </div>}
        {kind!=="chart"&&<div style={{display:"flex",gap:7,flexWrap:"wrap",alignItems:"end"}}>
          <label style={label}>폭 %<input type="number" min="8" max="100" value={slot.width??DEFAULT_BLOCK_WIDTH_PCT} onChange={event=>patch({width:Number(event.target.value)})} style={{...input,width:82,marginTop:3,padding:"5px 7px"}}/></label>
          <label style={label}>높이 %<input type="number" min="6" max="100" value={slot.height??DEFAULT_BLOCK_HEIGHT_PCT} onChange={event=>patch({height:Number(event.target.value)})} style={{...input,width:82,marginTop:3,padding:"5px 7px"}}/></label>
          {kind==="split"&&<>
            <label style={label}>제품<input value={slot.product||""} onChange={event=>patch({product:event.target.value})} style={{...input,width:150,marginTop:3,padding:"5px 7px",fontFamily:"monospace"}}/></label>
            <label style={label}>랏<input value={slot.lot||""} onChange={event=>patch({lot:event.target.value})} style={{...input,width:150,marginTop:3,padding:"5px 7px",fontFamily:"monospace"}}/></label>
            <label style={{...label,flex:"1 1 200px"}}>항목(콤마·비우면 전체)<input value={slot.columns||""} onChange={event=>patch({columns:event.target.value})} placeholder="KNOB_1.5_Vt_Split, ..." style={{...input,marginTop:3,padding:"5px 7px",fontFamily:"monospace"}}/></label>
          </>}
          {["stats","legend"].includes(kind)&&<>
            <label style={label}>대상 차트 번호<input type="number" min="1" value={slot.source_position||1} onChange={event=>patch({source_position:Number(event.target.value)})} style={{...input,width:110,marginTop:3,padding:"5px 7px"}}/></label>
            {kind==="stats"&&<label style={{...label,flex:"1 1 220px"}}>통계 항목<input value={slot.stats||""} onChange={event=>patch({stats:event.target.value})} placeholder="n,mean,median,std" style={{...input,marginTop:3,padding:"5px 7px",fontFamily:"monospace"}}/></label>}
            {kind==="legend"&&<span style={{fontSize:10,color:"var(--text-secondary)"}}>대상 차트의 색상 그룹을 별도 범례로 만듭니다. 같은 범례를 쓰는 차트는 ChartBuilder에서 내부 Legend를 꺼 주세요.</span>}
          </>}
        </div>}
        {kind==="text"&&<textarea aria-label={`${slot.position}번 글 내용`} value={slot.text||""} onChange={event=>patch({text:event.target.value})} rows={3} style={{...input,fontFamily:"inherit",resize:"vertical"}}/>}
      </div>;
    })}</div>}
  </section>;
}

function TemplateBackgroundSettings({settings,canEdit,onChanged}){
  const background=settings?.background||{};
  const[pastedImage,setPastedImage]=useState("");
  const[busy,setBusy]=useState(false);
  const shownImage=pastedImage||background.data_url||"";
  const handlePaste=event=>{
    if(!canEdit)return;
    const item=Array.from(event.clipboardData?.items||[]).find(entry=>entry.kind==="file"&&entry.type.startsWith("image/"));
    const file=item?.getAsFile();
    if(!file){toast.warn("클립보드에서 그림을 찾지 못했습니다. 그림을 복사한 뒤 이 영역에서 Ctrl+V 해 주세요.");return;}
    event.preventDefault();
    if(file.size>12*1024*1024){toast.error("배경 이미지는 12MB 이하만 사용할 수 있습니다.");return;}
    const reader=new FileReader();
    reader.onload=()=>{setPastedImage(text(reader.result));toast.ok("그림을 받았습니다. 미리보기 확인 후 저장해 주세요.");};
    reader.onerror=()=>toast.error("클립보드 그림을 읽지 못했습니다.");
    reader.readAsDataURL(file);
  };
  const saveBackground=async()=>{
    if(!pastedImage){toast.warn("먼저 아래 영역을 누르고 그림을 붙여넣어 주세요.");return;}
    setBusy(true);
    try{
      const out=await postJson(`${API}/settings/background`,{data_url:pastedImage});
      setPastedImage("");onChanged(out.settings||{});toast.ok("기본 PPT 배경을 저장했습니다.");
    }catch(error){toast.error(error.message||String(error));}finally{setBusy(false);}
  };
  const removeBackground=async()=>{
    if(!window.confirm("Template Report 기본 배경을 제거할까요?"))return;
    setBusy(true);
    try{
      const out=await sf(`${API}/settings/background`,{method:"DELETE"});
      setPastedImage("");onChanged(out.settings||{});toast.ok("기본 PPT 배경을 제거했습니다.");
    }catch(error){toast.error(error.message||String(error));}finally{setBusy(false);}
  };
  return <div style={{display:"grid",gap:10}}>
    <div style={{fontSize:12,lineHeight:1.6,color:"var(--text-secondary)"}}>그림을 복사한 뒤 아래 영역을 한 번 누르고 <b style={{color:"var(--text-primary)"}}>Ctrl+V</b> 하세요. 저장하면 표지·본문·Appendix와 화면 미리보기의 기본 배경으로 사용합니다.</div>
    <div
      tabIndex={canEdit?0:undefined}
      onPaste={handlePaste}
      aria-label="Template Report 기본 배경 그림 붙여넣기"
      style={{aspectRatio:"16 / 9",border:`2px dashed ${pastedImage?"var(--accent)":"var(--border)"}`,borderRadius:8,backgroundColor:"var(--bg-primary)",backgroundImage:shownImage?`url(${JSON.stringify(shownImage)})`:"none",backgroundPosition:"center",backgroundSize:"cover",backgroundRepeat:"no-repeat",display:"grid",placeItems:"center",outline:"none",cursor:canEdit?"text":"default",overflow:"hidden"}}
    >
      {!shownImage&&<span style={{padding:18,textAlign:"center",fontSize:12,color:"var(--text-secondary)"}}>{canEdit?"여기를 누르고 Ctrl+V":"설정된 기본 배경이 없습니다."}</span>}
      {shownImage&&pastedImage&&<span style={{alignSelf:"end",margin:8,padding:"3px 8px",borderRadius:999,background:"rgba(0,0,0,.65)",color:"#fff",fontSize:10,fontWeight:800}}>저장 전 미리보기</span>}
    </div>
    <div style={{display:"flex",gap:7,alignItems:"center",flexWrap:"wrap"}}>
      {canEdit&&<button type="button" onClick={saveBackground} disabled={!pastedImage||busy} style={primary}>{busy?"처리 중…":"붙여넣은 그림 저장"}</button>}
      {canEdit&&pastedImage&&<button type="button" onClick={()=>setPastedImage("")} disabled={busy} style={btn}>붙여넣기 취소</button>}
      {canEdit&&background.configured&&!pastedImage&&<button type="button" onClick={removeBackground} disabled={busy} style={{...btn,color:"var(--danger)"}}>기본 배경 제거</button>}
    </div>
    {background.configured&&<div style={{fontSize:10,color:"var(--text-secondary)"}}>현재 배경 · {background.updated_by||"—"} · {formatTime(background.updated_at)}</div>}
  </div>;
}

export default function My_TemplateReport({user}){
  const[templates,setTemplates]=useState([]),[charts,setCharts]=useState([]),[selectedId,setSelectedId]=useState("");
  const[draft,setDraft]=useState(null),[editing,setEditing]=useState(false),[loading,setLoading]=useState(true),[loadError,setLoadError]=useState("");
  const[templateSearch,setTemplateSearch]=useState("");
  const[reportSettings,setReportSettings]=useState({background:{configured:false,data_url:""}});
  const[bindings,setBindings]=useState({}),[repeatText,setRepeatText]=useState("");
  const[contextRootLots,setContextRootLots]=useState(""),[contextWafers,setContextWafers]=useState("");
  const[overrideRecentDays,setOverrideRecentDays]=useState(false),[contextRecentDays,setContextRecentDays]=useState("7"),[contextDateColumn,setContextDateColumn]=useState("tkout_time");
  const[contextColorRows,setContextColorRows]=useState(()=>normalizeSpreadsheetRows([],COLOR_LIST_COLUMNS)),[contextColorElse,setContextColorElse]=useState("gray");
  const[deck,setDeck]=useState(null),[runs,setRuns]=useState({}),[tables,setTables]=useState({}),[images,setImages]=useState([]);
  const[busy,setBusy]=useState(false),[saving,setSaving]=useState(false),[downloading,setDownloading]=useState("");
  const[runProgress,setRunProgress]=useState("");
  const[templateCode,setTemplateCode]=useState(""),[templateCodeBusy,setTemplateCodeBusy]=useState(false);
  const[templateAiPrompt,setTemplateAiPrompt]=useState(""),[templateAiBusy,setTemplateAiBusy]=useState(false),[templateAiMessage,setTemplateAiMessage]=useState("");

  const load=useCallback(async()=>{
    setLoadError("");
    const[data,chartData,settingsData]=await Promise.all([sf(`${API}/templates`),sf(`${API}/charts`),sf(`${API}/settings`)]);
    setTemplates(data.templates||[]);setCharts(chartData.charts||[]);setReportSettings(settingsData.settings||{background:{configured:false,data_url:""}});
    setSelectedId(current=>current||(data.templates?.[0]?.id||""));
  },[]);
  useEffect(()=>{load().catch(error=>{const message=templateApiError(error);setLoadError(message);toast.error(message);}).finally(()=>setLoading(false));},[load]);
  const selected=useMemo(()=>templates.find(template=>template.id===selectedId)||null,[templates,selectedId]);
  const visibleTemplates=useMemo(()=>{const query=text(templateSearch).trim().toLowerCase();return query?templates.filter(template=>`${template.id} ${template.name} ${template.created_by} ${template.updated_by}`.toLowerCase().includes(query)):templates;},[templates,templateSearch]);
  useEffect(()=>{if(selected&&!editing)setDraft(clone(selected));},[selected,editing]);

  const options=draft?.options||{};
  const canManageSettings=canManagePage(user,"templatereport");
  const backgroundImage=reportSettings?.background?.data_url||"";
  const defaultSubtitle=defaultPageSubtitle(user);
  const repeatVariable=text(options.repeat_variable||"LOT");
  // 실행 폼에 낼 변수 — 저장된 목록 + 지금 편집 중에 새로 등장한 이름.
  const variables=useMemo(()=>{
    const declared=(draft?.variables||[]).map(item=>({...item}));
    const seen=new Set(declared.map(item=>item.name));
    templateVariableNames(draft,charts).forEach(name=>{if(!seen.has(name)){seen.add(name);declared.push({name,label:name,default:""});}});
    return declared;
  },[draft,charts]);
  useEffect(()=>{
    setBindings(old=>{
      const next={};
      variables.forEach(item=>{next[item.name]=old[item.name]??item.default??"";});
      return next;
    });
  },[variables]);
  const contextColorList=useMemo(()=>spreadsheetTextFromRows(contextColorRows,COLOR_LIST_COLUMNS),[contextColorRows]);
  const contextColorPreview=useMemo(()=>parseChartColorList(contextColorList),[contextColorList]);
  const runContext=()=>({
    root_lot_ids:listValues(contextRootLots),wafer_ids:listValues(contextWafers),
    override_recent_days:overrideRecentDays,recent_days:overrideRecentDays?Math.max(0,Math.min(3650,Number(contextRecentDays)||0)):0,
    date_column:text(contextDateColumn).trim()||"tkout_time",
    color_rules:contextColorPreview.rows.length?chartColorListRules(contextColorPreview.rows):[],color_else:text(contextColorElse).trim()||"gray",
  });

  const resetRun=()=>{setDeck(null);setRuns({});setTables({});setImages([]);setRunProgress("");};
  const choose=template=>{setSelectedId(template.id);setDraft(clone(template));setEditing(false);setTemplateCode("");setTemplateAiMessage("");resetRun();};
  const createNew=()=>{setSelectedId("");setDraft({id:"",name:"새 Template Report",pages:[newPage(1)],variables:[],options:{cover:true,footer:true,subtitle:"",repeat_variable:"LOT"}});setEditing(true);setTemplateCode("");setTemplateAiMessage("");resetRun();};
  const updatePage=(index,patch)=>setDraft(old=>({...old,pages:old.pages.map((page,i)=>i===index?{...page,...patch}:page)}));
  const addSlot=(pageIndex,slot)=>setDraft(old=>({...old,pages:old.pages.map((page,index)=>index===pageIndex?{...page,slots:[...(page.slots||[]),slot]}:page)}));
  const updateSlot=(pageIndex,position,patch)=>setDraft(old=>({...old,pages:old.pages.map((page,index)=>index===pageIndex?{...page,slots:(page.slots||[]).map(slot=>Number(slot.position)===Number(position)?{...slot,...patch}:slot)}:page)}));
  const removeSlot=(pageIndex,position)=>setDraft(old=>({...old,pages:old.pages.map((page,index)=>index===pageIndex?{...page,slots:(page.slots||[]).filter(slot=>Number(slot.position)!==Number(position))}:page)}));
  const updateOptions=patch=>setDraft(old=>({...old,options:{...(old.options||{}),...patch}}));
  const updateVariable=(name,patch)=>setDraft(old=>{
    const list=(old.variables||[]).slice();
    const index=list.findIndex(item=>item.name===name);
    if(index>=0)list[index]={...list[index],...patch};
    else list.push({name,label:name,default:"",...patch});
    return{...old,variables:list};
  });
  const canonicalTemplateCode=useMemo(()=>templateCodeFromDraft(draft,variables),[draft,variables]);
  const visibleTemplateCode=templateCode||canonicalTemplateCode;
  const refreshTemplateCode=()=>{setTemplateCode(canonicalTemplateCode);setTemplateAiMessage("");toast.ok("현재 화면을 Template 전체 코드로 정리했습니다.");};
  const applyTemplateCode=async()=>{
    setTemplateCodeBusy(true);
    try{
      const out=await postJson(`${API}/code/parse`,{code:visibleTemplateCode});
      setDraft(clone(out.template));setSelectedId(out.template.id||"");setEditing(true);setTemplateCode("");setTemplateAiMessage("");resetRun();
      toast.ok("전체 코드를 Template 편집 화면에 적용했습니다. 확인 후 저장해 주세요.");
    }catch(error){toast.error(error.message||String(error));}
    finally{setTemplateCodeBusy(false);}
  };
  const copyTemplateCode=async()=>{
    try{await navigator.clipboard.writeText(visibleTemplateCode);toast.ok("Template 전체 코드를 복사했습니다.");}
    catch(_error){toast.error("복사하지 못했습니다. 코드 영역을 직접 선택해 주세요.");}
  };
  const askTemplateAi=async()=>{
    const instruction=text(templateAiPrompt).trim();
    if(!instruction){toast.warn("AI에게 만들거나 수정할 Template 내용을 입력해 주세요.");return;}
    setTemplateAiBusy(true);setTemplateAiMessage("");
    try{
      const out=await postJson(`${API}/assistant`,{instruction,template_code:visibleTemplateCode});
      setTemplateAiMessage(out.message||"");
      if(out.changed&&out.template){setTemplateCode(templateCodeFromDraft(out.template,out.template.variables||[]));toast.ok("AI가 만든 전체 코드를 아래에 준비했습니다. 검토 후 Template에 적용해 주세요.");}
      else toast.info(out.message||"AI가 코드를 변경하지 않았습니다.");
    }catch(error){setTemplateAiMessage(error.message||String(error));toast.error(error.message||String(error));}
    finally{setTemplateAiBusy(false);}
  };

  const save=async()=>{
    if(!text(draft?.name).trim()){toast.error("Template 이름을 입력해 주세요.");return;}
    if(!(draft?.pages||[]).some(page=>(page.slots||[]).length)){toast.error("블록을 한 개 이상 배치해 주세요.");return;}
    setSaving(true);
    try{
      const payload={
        id:draft.id||"",name:draft.name,
        variables:variables.map(item=>({name:item.name,label:item.label||item.name,default:item.default||""})),
        options:{cover:options.cover!==false,footer:options.footer!==false,subtitle:text(options.subtitle),repeat_variable:repeatVariable},
        pages:draft.pages.map(page=>({id:page.id,title:page.title,subtitle:page.subtitle||"",slots:(page.slots||[]).map(slot=>{
          const kind=slotKind(slot),layout=slotLayout(slot);
          const base={position:slot.position,kind,title:slot.title||"",x:layout.x,y:layout.y};
           if(kind==="chart")return{...base,chart_id:slot.chart_id,chart_name:slot.chart_name||slot.chart_label||"",definition_code:slot.definition_code||"",chart_width:layout.chart_width,chart_height:layout.chart_height};
          const sized={...base,width:layout.width,height:layout.height};
          if(kind==="split")return{...sized,product:slot.product||"",lot:slot.lot||"",columns:slot.columns||"",display_mode:slot.display_mode||"matrix"};
          if(kind==="text")return{...sized,text:slot.text||""};
          if(kind==="legend")return{...sized,source_position:Number(slot.source_position)||1};
          return{...sized,source_position:Number(slot.source_position)||1,stats:slot.stats||"n,mean,median,std"};
        })})),
      };
      const out=await postJson(`${API}/templates`,payload);
      await load();setSelectedId(out.template.id);setDraft(clone(out.template));setEditing(false);toast.ok(`'${out.template.name}' 저장 완료 · ${out.template.id}`);
    }catch(error){toast.error(error.message||String(error));}finally{setSaving(false);}
  };

  const runReport=async()=>{
    const templateId=selectedId||draft?.id;if(!templateId){toast.error("먼저 Template을 저장해 주세요.");return;}
    if(contextColorPreview.errors.length){toast.error(contextColorPreview.errors[0]);return;}
    setBusy(true);resetRun();
    try{
      const repeatValues=text(repeatText).split(/[,\n]+/).map(item=>item.trim()).filter(Boolean);
      const context=runContext();
      setRunProgress("Report 실행 계획을 만드는 중…");
      const prepared=await postJson(`${API}/run`,{template_id:templateId,bindings,repeat_values:repeatValues,context});
      const nextDeck=prepared.deck;setDeck(nextDeck);
      const nextRuns={},nextTables={};
      const splitBlocks=(nextDeck.pages||[]).flatMap(page=>(page.blocks||[]).filter(block=>block.kind==="split"));
      const groupedCharts=new Map();
      (nextDeck.charts||[]).forEach(chart=>{const key=dataRequestKey(chart.request),group=groupedCharts.get(key)||[];group.push(chart);groupedCharts.set(key,group);});
      const tasks=[
        ...[...groupedCharts.values()].map(group=>({kind:"chart",group})),
        ...splitBlocks.map(block=>({kind:"split",block})),
      ];
      let completed=0;setRunProgress(`데이터 조회 0 / ${tasks.length} · 동시 최대 ${REPORT_QUERY_CONCURRENCY}개`);
      await runPool(tasks,async task=>{
        if(task.kind==="chart"){
          const request=task.group[0].request;
          try{
            const result=await postJson("/api/filebrowser/chart-builder/run",request);
            task.group.forEach(chart=>{nextRuns[chart.key]={...chart,definition:chart.request,result};});
          }catch(error){task.group.forEach(chart=>{nextRuns[chart.key]={...chart,definition:chart.request,error:error.message||String(error)};});}
        }else{
          const block=task.block;
          try{
            const out=await postJson(`${API}/split-table`,{product:block.product,lot_id:block.lot,columns:block.columns||"",display_mode:block.display_mode||"matrix"});
            nextTables[block.key]={title:block.title||`Split · ${block.lot}`,columns:out.columns||[],rows:out.rows||[],note:out.truncated?`${out.row_total}행 중 앞 ${(out.rows||[]).length}행`:text(out.note).slice(0,120)};
          }catch(error){nextTables[block.key]={title:block.title||"Split",columns:["오류"],rows:[[error.message||String(error)]],note:""};}
        }
        completed+=1;setRunProgress(`데이터 조회 ${completed} / ${tasks.length} · 동시 최대 ${REPORT_QUERY_CONCURRENCY}개`);
      },REPORT_QUERY_CONCURRENCY);
      (nextDeck.pages||[]).forEach(page=>(page.blocks||[]).forEach(block=>{
        if(!["stats","legend"].includes(block.kind))return;
        const table=block.kind==="legend"?legendTable(block,nextRuns):statsTable(block,nextRuns);
        if(table)nextTables[block.key]=table;
      }));
      setRuns(nextRuns);setTables(nextTables);
      setRunProgress("차트를 화면에 배치하는 중…");
      await new Promise(resolve=>window.setTimeout(resolve,1300));
      const captured=[];
      for(const [index,chart] of (nextDeck.charts||[]).entries()){
        setRunProgress(`고해상도 PNG 캡처 ${index+1} / ${(nextDeck.charts||[]).length} · ${PPTX_CHART_CAPTURE_SCALE}×`);
        if(nextRuns[chart.key]?.error)continue;
        const host=document.querySelector(`[data-report-chart-key="${chart.key}"]`),plot=host?.querySelector(".js-plotly-plot");
        if(!plot)continue;
        const captureWidth=clamp(Number(chart.chart_width)||DEFAULT_CHART_WIDTH,320,2400),captureHeight=clamp(Number(chart.chart_height)||DEFAULT_CHART_HEIGHT,240,1600);
        const dataUrl=await Plotly.toImage(plot,{format:"png",width:captureWidth,height:captureHeight,scale:PPTX_CHART_CAPTURE_SCALE});
        captured.push({key:chart.key,page_index:chart.page_index,position:chart.position,chart_id:chart.chart_id,data_url:dataUrl});
      }
      setImages(captured);
      setRunProgress(`완료 · 데이터 조회 ${tasks.length}회 / 차트 ${(nextDeck.charts||[]).length}개`);
      const failed=Object.values(nextRuns).filter(item=>item.error).length;
      if(failed)toast.error(`${failed}개 차트 실행에 실패했습니다. 나머지는 생성했습니다.`);
      else toast.ok(`${(nextDeck.pages||[]).length}장 Report를 생성했습니다. PPTX로 내려받을 수 있습니다.`);
    }catch(error){toast.error(error.message||String(error));}finally{setBusy(false);}
  };

  const download=async kind=>{
    if(!images.length&&!Object.keys(tables).length){toast.error("먼저 Report를 실행해 주세요.");return;}
    const template=selected||draft;
    const repeatValues=text(repeatText).split(/[,\n]+/).map(item=>item.trim()).filter(Boolean);
    const payload={
      template_id:template.id,bindings,repeat_values:repeatValues,context:runContext(),images,
      tables:Object.entries(tables).map(([key,table])=>({key,title:table.title||"",columns:table.columns||[],rows:table.rows||[],note:table.note||""})),
    };
    // 파일 이름의 날짜는 서버(export/pptx)와 같은 규칙 — 같은 보고서를 매일 받아도 겹치지 않는다.
    const stamp=new Date().toISOString().slice(0,10).replace(/-/g,"");
    setDownloading(kind);
    try{await postDownload(`${API}/export/${kind}`,payload,kind==="pptx"?`${template.name}_${stamp}.pptx`:`${template.name}_chart_images.zip`);toast.ok(kind==="pptx"?"PPTX 다운로드를 시작했습니다.":"차트 PNG ZIP 다운로드를 시작했습니다.");}
    catch(error){toast.error(error.message||String(error));}finally{setDownloading("");}
  };

  const remove=async()=>{
    if(!selected||!window.confirm(`'${selected.name}' Template을 삭제할까요?`))return;
    try{await sf(`${API}/templates/${encodeURIComponent(selected.id)}`,{method:"DELETE"});setSelectedId("");setDraft(null);resetRun();await load();toast.ok("Template을 삭제했습니다.");}catch(error){toast.error(error.message||String(error));}
  };

  const previewPages=deck?.pages||(draft?.pages||[]).map((page,index)=>({index,title:page.title,subtitle:page.subtitle,slots:page.slots}));

  if(loading)return<div style={{padding:24,color:"var(--text-secondary)"}}>Template Report를 불러오는 중…</div>;
  if(loadError)return <div style={{...card,maxWidth:760,margin:"32px auto",padding:24,borderColor:"var(--danger)",color:"var(--text-primary)"}}><strong style={{display:"block",marginBottom:8}}>Template Report API 연결 실패</strong><div style={{fontSize:13,lineHeight:1.65,color:"var(--text-secondary)"}}>{loadError}</div><button type="button" onClick={()=>{setLoading(true);load().catch(error=>setLoadError(templateApiError(error))).finally(()=>setLoading(false));}} style={{...primary,marginTop:14}}>다시 연결</button></div>;
  return <div style={{display:"grid",gridTemplateColumns:"290px minmax(0,1fr)",gap:16,padding:"18px 22px 60px",maxWidth:1600,margin:"0 auto"}}>
    <aside style={{...card,padding:12,alignSelf:"start",position:"sticky",top:68}}>
      <div style={{display:"flex",alignItems:"center",gap:8,marginBottom:10}}><strong style={{fontSize:15}}>저장된 Template</strong><button type="button" onClick={createNew} style={{...primary,marginLeft:"auto",padding:"6px 8px"}}>＋ 신규 만들기</button></div>
      <input aria-label="Template 검색" value={templateSearch} onChange={event=>setTemplateSearch(event.target.value)} placeholder="Template ID · Name 검색" style={{...input,marginBottom:9}}/>
      <div style={{maxHeight:"calc(100vh - 145px)",overflowY:"auto",display:"grid",gap:7,paddingRight:3}}>
        {!templates.length&&<div style={{padding:"28px 10px",textAlign:"center",fontSize:12,color:"var(--text-secondary)"}}>저장된 Template이 없습니다.</div>}
        {!!templates.length&&!visibleTemplates.length&&<div style={{padding:"22px 10px",textAlign:"center",fontSize:12,color:"var(--text-secondary)"}}>검색 조건에 맞는 Template이 없습니다.</div>}
        {visibleTemplates.map(template=><button type="button" key={template.id} onClick={()=>choose(template)} style={{...btn,textAlign:"left",padding:10,background:selectedId===template.id?"var(--accent-glow)":"var(--bg-primary)",borderColor:selectedId===template.id?"var(--accent)":"var(--border)"}}>
          <div style={{fontSize:13,fontWeight:900,overflow:"hidden",textOverflow:"ellipsis",whiteSpace:"nowrap"}}>{template.name}</div>
          <code style={{display:"block",fontSize:9,color:"var(--text-secondary)",marginTop:3,overflow:"hidden",textOverflow:"ellipsis"}}>{template.id}</code>
          <div style={{fontSize:11,color:"var(--text-secondary)",marginTop:4}}>{template.pages.length} pages · {template.updated_by||template.created_by}</div>
          <div style={{fontSize:10,color:"var(--text-secondary)",marginTop:2}}>{formatTime(template.updated_at)}</div>
        </button>)}
      </div>
    </aside>
    <main style={{minWidth:0}}>
      {!draft&&<div style={{...card,padding:40,textAlign:"center",color:"var(--text-secondary)"}}>좌측에서 Template을 고르거나 신규 만들기를 눌러 주세요.</div>}
      {draft&&<>
        <div style={{...card,padding:14,marginBottom:14}}>
          <div style={{display:"flex",gap:8,alignItems:"center",flexWrap:"wrap"}}>
            {editing?<><input aria-label="Template 이름" value={draft.name} onChange={event=>setDraft(old=>({...old,name:event.target.value}))} style={{...input,maxWidth:440,fontSize:16,fontWeight:900}}/><span style={{fontSize:11,color:"var(--text-secondary)"}}>동일 이름은 (2), (3)…으로 자동 저장</span></>:<><strong style={{fontSize:20}}>{draft.name}</strong><code style={{fontSize:10,color:"var(--text-secondary)"}}>{draft.id}</code><span style={{fontSize:11,color:"var(--text-secondary)"}}>16:9 Wide · 간결한 회의용 헤더 · 축 강조 · 슬라이드 맞춤</span></>}
            <div style={{marginLeft:"auto",display:"flex",gap:7,flexWrap:"wrap"}}>
              {editing?<><button type="button" onClick={save} disabled={saving} style={primary}>{saving?"저장 중…":"저장"}</button><button type="button" onClick={()=>{setEditing(false);if(selected)setDraft(clone(selected));}} style={btn}>취소</button></>:<><button type="button" onClick={()=>setEditing(true)} style={btn}>Template 편집</button><button type="button" onClick={remove} style={{...btn,color:"var(--danger)"}}>삭제</button></>}
            </div>
          </div>

          {editing&&<div style={{display:"grid",gap:10,marginTop:12,paddingTop:12,borderTop:"1px solid var(--border)"}}>
            <div style={{display:"flex",gap:10,flexWrap:"wrap",alignItems:"end"}}>
              <label style={{...label,display:"flex",alignItems:"center",gap:5}}><input type="checkbox" checked={options.cover!==false} onChange={event=>updateOptions({cover:event.target.checked})}/>표지 슬라이드</label>
              <label style={{...label,display:"flex",alignItems:"center",gap:5}}><input type="checkbox" checked={options.footer!==false} onChange={event=>updateOptions({footer:event.target.checked})}/>하단 푸터·쪽 번호</label>
              <label style={{...label,flex:"1 1 260px"}}>표지 부제(변수 사용 가능)<input value={options.subtitle||""} onChange={event=>updateOptions({subtitle:event.target.value})} placeholder="{{PRODUCT}} · {{LOT}}" style={{...input,marginTop:3,fontFamily:"monospace"}}/></label>
            </div>
            <div style={{display:"flex",gap:10,flexWrap:"wrap",alignItems:"end"}}>
              <label style={label}>반복 변수<input value={options.repeat_variable??"LOT"} onChange={event=>updateOptions({repeat_variable:event.target.value})} placeholder="LOT" style={{...input,width:130,marginTop:3,fontFamily:"monospace"}}/></label>
              <span style={{fontSize:11,color:"var(--text-secondary)",flex:"1 1 320px"}}>반복 변수에 랏을 여러 개 넣으면 페이지 묶음이 랏마다 반복됩니다. 조건 비교(A/B)는 ChartBuilder 코드에서 조건 열을 COLOR·X 로 지정해 차트 자체에 표현합니다.</span>
            </div>
            {!!variables.length&&<div style={{display:"grid",gap:6}}>
              <strong style={{fontSize:12}}>템플릿 변수 <span style={{fontWeight:500,color:"var(--text-secondary)"}}>· 저장된 차트 코드의 {"{{이름}}"} 토큰에서 자동 인식</span></strong>
              {variables.map(item=><div key={item.name} style={{display:"flex",gap:8,alignItems:"center",flexWrap:"wrap"}}>
                <code style={{fontSize:11,fontWeight:900,minWidth:110,color:"var(--accent)"}}>{`{{${item.name}}}`}</code>
                <input aria-label={`${item.name} 설명`} value={item.label||""} onChange={event=>updateVariable(item.name,{label:event.target.value})} placeholder="설명" style={{...input,maxWidth:200,padding:"5px 7px",fontSize:12}}/>
                <input aria-label={`${item.name} 기본값`} value={item.default||""} onChange={event=>updateVariable(item.name,{default:event.target.value})} placeholder="기본값" style={{...input,maxWidth:200,padding:"5px 7px",fontSize:12,fontFamily:"monospace"}}/>
                {item.name===repeatVariable&&<span style={{fontSize:10,fontWeight:800,color:"var(--accent)"}}>반복</span>}
              </div>)}
            </div>}
          </div>}

          {!editing&&<div style={{display:"grid",gap:10,marginTop:12}}>
            {!!variables.length&&<div style={{display:"flex",gap:9,flexWrap:"wrap",alignItems:"end"}}>
              {variables.filter(item=>item.name!==repeatVariable).map(item=><label key={item.name} style={label}>{item.label||item.name}<input aria-label={item.label||item.name} value={bindings[item.name]??""} onChange={event=>setBindings(old=>({...old,[item.name]:event.target.value}))} placeholder={`{{${item.name}}}`} style={{...input,width:170,marginTop:4,fontFamily:"monospace"}}/></label>)}
              {variables.some(item=>item.name===repeatVariable)&&<label style={{...label,flex:"1 1 260px"}}>{repeatVariable} 목록 <span style={{fontWeight:500}}>· 콤마로 여러 개(랏마다 페이지 반복)</span><input aria-label={`${repeatVariable} 목록`} value={repeatText} onChange={event=>setRepeatText(event.target.value)} placeholder="A1234, A5678, A9012" style={{...input,marginTop:4,fontFamily:"monospace"}}/></label>}
            </div>}
            <details style={{border:"1px solid var(--border)",borderRadius:8,background:"var(--bg-primary)",overflow:"hidden"}}>
              <summary style={{cursor:"pointer",padding:"11px 12px",userSelect:"none"}}>
                <span style={{display:"inline-flex",width:"calc(100% - 20px)",gap:8,alignItems:"center",flexWrap:"wrap",verticalAlign:"middle"}}>
                  <strong style={{fontSize:12}}>모든 차트 일괄 적용</strong>
                  <span style={{fontSize:10,color:"var(--text-secondary)"}}>Lot/Wafer 맞춤 · 컬러링 변경 · 조회 기간 변경</span>
                  <span style={{marginLeft:"auto",fontSize:10,fontWeight:800,color:"var(--text-secondary)",border:"1px solid var(--border)",borderRadius:999,padding:"2px 7px"}}>
                    Lot {listValues(contextRootLots).length} · Wafer {listValues(contextWafers).length} · Color {contextColorPreview.rows.length} · {overrideRecentDays?(contextRecentDays?`${Number(contextRecentDays)}일`:"전체 기간"):"차트별 기간"}
                  </span>
                </span>
              </summary>
              <div style={{display:"grid",gap:10,padding:12,borderTop:"1px solid var(--border)"}}>
                <section style={{display:"grid",gap:8,border:"1px solid var(--border)",borderRadius:8,padding:10,background:"var(--bg-secondary)"}}>
                  <div style={{display:"flex",gap:8,alignItems:"baseline",flexWrap:"wrap"}}>
                    <strong style={{fontSize:12}}>1. Lot/Wafer 맞추기</strong>
                    <span style={{fontSize:10,color:"var(--text-secondary)"}}>입력한 대상만 모든 차트에 적용 · 비우면 차트 저장값 유지</span>
                  </div>
                  <div style={{display:"grid",gridTemplateColumns:"repeat(auto-fit, minmax(220px, 1fr))",gap:8}}>
                    <label style={label}>Root Lot ID<textarea aria-label="Report 공통 Root Lot ID" value={contextRootLots} onChange={event=>setContextRootLots(event.target.value)} rows={3} placeholder={"A1234\nA5678"} style={{...input,marginTop:3,fontFamily:"monospace",resize:"vertical"}}/></label>
                    <label style={label}>Wafer ID<textarea aria-label="Report 공통 Wafer ID" value={contextWafers} onChange={event=>setContextWafers(event.target.value)} rows={3} placeholder={"1\n2"} style={{...input,marginTop:3,fontFamily:"monospace",resize:"vertical"}}/></label>
                  </div>
                  <div style={{fontSize:10,color:"var(--text-secondary)"}}>현재 적용: Root Lot {listValues(contextRootLots).length}개 · Wafer {listValues(contextWafers).length}개</div>
                </section>

                <section style={{display:"grid",gap:8,border:"1px solid var(--border)",borderRadius:8,padding:10,background:"var(--bg-secondary)"}}>
                  <div style={{display:"flex",gap:8,alignItems:"baseline",flexWrap:"wrap"}}>
                    <strong style={{fontSize:12}}>2. 컬러링 바꾸기</strong>
                    <span style={{fontSize:10,color:"var(--text-secondary)"}}>root_lot_id · wafer_id 조합별 색상을 모든 차트에 적용</span>
                  </div>
                  <SpreadsheetPasteGrid columns={COLOR_LIST_COLUMNS} rows={contextColorRows} onChange={setContextColorRows} ariaLabel="Report 공통 색상 목록" aliases={COLOR_LIST_ALIASES} colorColumn="color" placeholders={{root_lot_id:"A1234",wafer_id:"1",color:"#dc2626"}} minRows={10} maxRows={200} maxHeight={365}/>
                  <div style={{display:"flex",gap:8,alignItems:"center",flexWrap:"wrap"}}>
                    <label style={label}>기본색<input aria-label="Report 공통 기본색" value={contextColorElse} onChange={event=>setContextColorElse(event.target.value)} style={{...input,width:105,display:"inline-block",marginLeft:5,padding:"5px 7px"}}/></label>
                    {!!contextColorList&&<button type="button" onClick={()=>setContextColorRows(normalizeSpreadsheetRows([],COLOR_LIST_COLUMNS))} style={{...btn,padding:"5px 8px",fontSize:11}}>표 비우기</button>}
                    <span style={{fontSize:10,color:contextColorPreview.errors.length?"var(--danger)":"var(--text-secondary)"}}>{contextColorPreview.errors[0]||`${contextColorPreview.rows.length}개 색상 지정 · 비우면 저장 차트 색 유지`}</span>
                  </div>
                </section>

                <section style={{display:"grid",gap:8,border:"1px solid var(--border)",borderRadius:8,padding:10,background:"var(--bg-secondary)"}}>
                  <div style={{display:"flex",gap:8,alignItems:"baseline",flexWrap:"wrap"}}>
                    <strong style={{fontSize:12}}>3. 조회 기간 맞추기</strong>
                    <span style={{fontSize:10,color:"var(--text-secondary)"}}>필요할 때만 저장된 차트별 기간을 덮어쓰기</span>
                  </div>
                  <label style={{...label,display:"flex",alignItems:"center",gap:6,cursor:"pointer"}}><input type="checkbox" checked={overrideRecentDays} onChange={event=>setOverrideRecentDays(event.target.checked)}/>이번 실행의 모든 차트 조회 기간 변경</label>
                  <div style={{display:"grid",gridTemplateColumns:"repeat(auto-fit, minmax(180px, 1fr))",gap:8}}>
                    <label style={label}>최근 일수 <span style={{fontWeight:500}}>· 비우면 전체</span><input aria-label="Report 공통 최근 일수" type="number" min="1" max="3650" value={contextRecentDays} onChange={event=>setContextRecentDays(event.target.value)} disabled={!overrideRecentDays} style={{...input,marginTop:3,opacity:overrideRecentDays?1:.55}}/></label>
                    <label style={label}>시간 열<input aria-label="Report 공통 시간 열" value={contextDateColumn} onChange={event=>setContextDateColumn(event.target.value)} disabled={!overrideRecentDays||!Number(contextRecentDays)} style={{...input,marginTop:3,fontFamily:"monospace",opacity:overrideRecentDays?1:.55}}/></label>
                  </div>
                  <div style={{fontSize:10,color:"var(--text-secondary)"}}>{overrideRecentDays?(contextRecentDays?`현재 적용: 최근 ${Number(contextRecentDays)}일 (${contextDateColumn||"tkout_time"})`:"현재 적용: 전체 기간"):"현재 적용: 차트별 저장 기간 유지"}</div>
                </section>

                <div style={{display:"flex",justifyContent:"flex-end"}}>
                  <span style={{fontSize:10,fontWeight:900,color:"var(--ok)",border:"1px solid var(--ok-line)",borderRadius:999,padding:"2px 7px"}}>동일 데이터 1회 조회 · 동시 최대 {REPORT_QUERY_CONCURRENCY}개</span>
                </div>
              </div>
            </details>
            <div style={{display:"flex",gap:9,alignItems:"center",flexWrap:"wrap"}}>
              <button type="button" onClick={runReport} disabled={busy} style={primary}>{busy?"실행·캡처 중…":"실행"}</button>
              <button type="button" onClick={()=>download("pptx")} disabled={!deck||!!downloading} style={btn}>{downloading==="pptx"?"PPTX 생성 중…":"PPTX 다운로드"}</button>
              <button type="button" onClick={()=>download("images")} disabled={!images.length||!!downloading} style={btn}>{downloading==="images"?"ZIP 생성 중…":"차트별 PNG ZIP"}</button>
              <span style={{fontSize:11,color:"var(--text-secondary)"}}>{runProgress||`공통 컨텍스트로 모든 차트를 함께 바꾸고 PPTX로 내려받습니다.${deck?` · ${deck.pages.length}장 생성됨`:""}`}</span>
            </div>
          </div>}
        </div>
        {editing?<div style={{display:"grid",gap:12}}>
          {draft.pages.map((page,pageIndex)=><div key={page.id} style={{display:"grid",gap:8}}><div style={{display:"flex",justifyContent:"flex-end"}}>{draft.pages.length>1&&<button type="button" onClick={()=>setDraft(old=>({...old,pages:old.pages.filter((_,index)=>index!==pageIndex)}))} style={{...btn,color:"var(--danger)"}}>Page {pageIndex+1} 삭제</button>}</div><SlideCanvas page={page} pageIndex={pageIndex} editing charts={charts} defaultSubtitle={defaultSubtitle} backgroundImage={backgroundImage} updatePage={updatePage} updateSlot={updateSlot} removeSlot={removeSlot} addSlot={addSlot}/></div>)}
          <datalist id="template-report-chart-ids">{charts.flatMap(chart=>[
            <option key={`name-${chart.id}`} value={chart.name}>{chart.id} · {windowLabel(chart)} · {formatTime(chart.timestamp)} · {chart.label}</option>,
            <option key={`id-${chart.id}`} value={chart.id}>{chart.name} · {windowLabel(chart)} · {formatTime(chart.timestamp)} · {chart.label}</option>,
          ])}</datalist>
          <button type="button" onClick={()=>setDraft(old=>({...old,pages:[...old.pages,newPage(old.pages.length+1)]}))} disabled={draft.pages.length>=30} style={{...btn,justifySelf:"start"}}>＋ 다음 페이지</button>
        </div>:<div style={{display:"grid",gap:16}}>{previewPages.map((page,pageIndex)=><SlideCanvas key={page.id||`p${page.index??pageIndex}`} page={page} pageIndex={page.index??pageIndex} runs={runs} tables={tables} defaultSubtitle={defaultSubtitle} backgroundImage={backgroundImage}/>)}</div>}
        {editing&&<section id="template-report-code" style={{...card,padding:14,marginTop:16,borderColor:"var(--accent)"}}>
          <div style={{display:"flex",gap:8,alignItems:"baseline",flexWrap:"wrap",marginBottom:10}}>
            <strong style={{fontSize:15}}>Template 전체 코드</strong>
            <code style={{fontSize:10,color:"var(--accent)"}}>flow-template-report/v1</code>
            <span style={{fontSize:11,color:"var(--text-secondary)"}}>페이지·배치·변수·차트 생성식까지 이 JSON 하나로 저장하고 다시 만들 수 있습니다.</span>
          </div>
          <div style={{display:"grid",gridTemplateColumns:"minmax(280px,.72fr) minmax(420px,1.28fr)",gap:10,alignItems:"stretch"}}>
            <div style={{display:"grid",gap:8,alignContent:"start"}}>
              <div style={{border:"1px solid var(--border)",borderRadius:8,padding:10,background:"var(--bg-primary)",display:"grid",gap:7}}>
                <div style={{display:"flex",gap:7,alignItems:"center",flexWrap:"wrap"}}><strong style={{fontSize:12}}>✨ AI Template Assistant</strong><span style={{fontSize:10,color:"var(--text-secondary)"}}>전체 코드를 생성하거나 필요한 부분만 수정</span></div>
                <textarea aria-label="AI Template 요청" value={templateAiPrompt} onChange={event=>setTemplateAiPrompt(event.target.value)} rows={5} placeholder={"예: 최근 14일 VTH trend를 위에 길게 두고, 아래에는 IDSAT corr·chamber box·leakage bar를 배치해줘\n예: 모든 차트의 root lot을 {{LOT}} 변수로 바꿔줘"} style={{...input,resize:"vertical",lineHeight:1.5}}/>
                <button type="button" onClick={askTemplateAi} disabled={templateAiBusy} style={{...primary,justifySelf:"start"}}>{templateAiBusy?"AI가 전체 코드 작성 중…":"AI로 전체 코드 만들기·수정"}</button>
                {templateAiMessage&&<div style={{fontSize:11,lineHeight:1.5,color:"var(--text-secondary)",whiteSpace:"pre-wrap"}}>{templateAiMessage}</div>}
              </div>
              <div style={{fontSize:11,lineHeight:1.65,color:"var(--text-secondary)",padding:"2px 3px"}}>
                <b style={{color:"var(--text-primary)"}}>권장 흐름</b><br/>
                1. 화면에서 차트를 배치하거나 AI에게 요청<br/>
                2. 전체 코드를 검토·복사·직접 수정<br/>
                3. <b>코드 → Template 적용</b> 후 화면 확인<br/>
                4. 저장하면 이후 같은 코드로 재현
              </div>
            </div>
            <div style={{display:"grid",gap:7,minWidth:0}}>
              <div style={{display:"flex",gap:7,alignItems:"center",flexWrap:"wrap"}}>
                <button type="button" onClick={refreshTemplateCode} style={btn}>현재 화면 → 전체 코드</button>
                <button type="button" onClick={applyTemplateCode} disabled={templateCodeBusy} style={primary}>{templateCodeBusy?"코드 확인 중…":"코드 → Template 적용"}</button>
                <button type="button" onClick={copyTemplateCode} style={btn}>전체 코드 복사</button>
                {!!templateCode&&<button type="button" onClick={()=>setTemplateCode("")} style={{...btn,color:"var(--text-secondary)"}}>화면 코드로 되돌리기</button>}
              </div>
              <textarea aria-label="Template 전체 코드" value={visibleTemplateCode} onChange={event=>setTemplateCode(event.target.value)} rows={28} spellCheck={false} style={{...input,minHeight:510,fontFamily:"'JetBrains Mono',monospace",fontSize:11,lineHeight:1.5,resize:"vertical",whiteSpace:"pre",tabSize:2}}/>
            </div>
          </div>
        </section>}
      </>}
    </main>
    <PageGear title="Template Report 기본 배경" canEdit={canManageSettings} position="bottom-left" width={430}>
      <TemplateBackgroundSettings settings={reportSettings} canEdit={canManageSettings} onChanged={setReportSettings}/>
    </PageGear>
  </div>;
}
