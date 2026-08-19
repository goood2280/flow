import { useCallback, useEffect, useMemo, useRef, useState } from "react";
import { FlowPlotlyChart } from "../../components/PlotlyChart";
import { toast } from "../../components/Toast";
import Plotly from "../../lib/plotlyCustom";
import { computeBoxStats } from "../../lib/boxStats";
import { chartColorMap, chartColorValue, parseChartColorRules } from "../../lib/chartColorRules";
import { chartColorListRules, parseChartColorList } from "../../lib/chartColorList";
import { postDownload, postJson, sf } from "../../lib/api";

const API="/api/template-report";
// 슬라이드 규약은 PPTX(backend/routers/template_report.py)와 같은 값이어야 한다 —
// 화면에서 맞춰 놓은 자리가 파일에서 달라지면 템플릿이 의미를 잃는다.
const NAVY="#1F497D",TITLE_BAR_PCT=0.62/7.5*100,SLIDE_DESIGN_WIDTH=1920,SLIDE_DESIGN_HEIGHT=1080,DEFAULT_CHART_WIDTH=1200,DEFAULT_CHART_HEIGHT=650;
const DEFAULT_BLOCK_WIDTH_PCT=46,DEFAULT_BLOCK_HEIGHT_PCT=26;
const REPORT_QUERY_CONCURRENCY=2;
const card={border:"1px solid var(--border)",borderRadius:9,background:"var(--bg-secondary)"};
const input={width:"100%",boxSizing:"border-box",border:"1px solid var(--border)",borderRadius:6,background:"var(--bg-primary)",color:"var(--text-primary)",padding:"8px 9px",fontSize:13};
const btn={border:"1px solid var(--border)",borderRadius:6,background:"var(--bg-tertiary)",color:"var(--text-primary)",padding:"7px 10px",fontWeight:800,cursor:"pointer"};
const primary={...btn,background:"var(--accent)",borderColor:"var(--accent)",color:"#fff"};
const label={fontSize:11,fontWeight:800,color:"var(--text-secondary)"};
const KIND_LABEL={chart:"차트",split:"Split 표",text:"글",stats:"통계표"};

function text(value){return value==null?"":String(value);}
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
function formatTime(value){const date=new Date(value);return Number.isNaN(date.getTime())?text(value):date.toLocaleString("ko-KR",{hour12:false});}
function templateApiError(error){
  const message=error?.message||String(error||"");
  return message.includes("API not found")
    ?"Template Report API가 로드되지 않았습니다. 최신 setup.py를 적용한 뒤 Flow 백엔드를 재시작해 주세요. 재시작 시 누락된 Template Report 라우터를 번들에서 자동 복원합니다."
    :message;
}
function median(values){const sorted=values.filter(Number.isFinite).sort((a,b)=>a-b);if(!sorted.length)return NaN;const mid=(sorted.length-1)/2,lo=Math.floor(mid),hi=Math.ceil(mid);return(sorted[lo]+sorted[hi])/2;}
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
    return[slot.title,(chart?.variables||[]).map(name=>`{{${name}}}`).join(" ")].map(text).join("\n");
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
  if(["radius","wafer_map"].includes(type))type="scatter";
  const custom=["custom","__custom__"].includes(text(config.color).toLowerCase());
  const rules=custom?parseChartColorRules(config.color_rules).filter(rule=>!rule.error):[];
  const requestedColor=custom?"Custom Color":text(config.color);
  const trellis=text(config.trellis);
  const colorBy=requestedColor||(trellis?trellis:"");
  const colorMap=custom?chartColorMap(rules,config.color_else||"gray"):{};
  const colorValue=row=>{
    if(custom)return chartColorValue(row,rules);
    if(config.color&&columns.includes(config.color))return row[config.color];
    return trellis&&columns.includes(trellis)?row[trellis]:"";
  };
  const title=[x,y].filter(Boolean).join(" × ")||run.chart_label||run.chart_id;
  if(type==="pie"||type==="donut"){
    const buckets=new Map();rows.forEach(row=>{const label=text(row[x]).trim()||"(빈값)";buckets.set(label,(buckets.get(label)||0)+1);});
    return{chart_type:type,title,x_label:x,y_label:"행 수",groups:[...buckets.entries()].map(([label,value])=>({label,value,count:value}))};
  }
  if(type==="bar"||type==="bar_horizontal"){
    const buckets=new Map();rows.forEach(row=>{const label=text(row[x]),value=Number(row[y]);if(!label||!Number.isFinite(value))return;if(!buckets.has(label))buckets.set(label,[]);buckets.get(label).push(value);});
    return{chart_type:type,title,x_label:x,y_label:y,groups:[...buckets.entries()].map(([label,values])=>({label,value:median(values),count:values.length}))};
  }
  if(!x||!y)return null;
  const points=rows.slice(0,10000).map((row,index)=>({...row,x:row[x]??index,x_label:row[x],y:Number(row[y]),color_value:colorValue(row),trellis_value:trellis?row[trellis]:""})).filter(point=>Number.isFinite(point.y));
  return{chart_type:type,title,x_label:x,y_label:y,color_by:colorBy,color_map:colorMap,points,point_size:6,emphasize_markers:type==="scatter"};
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

function BlockTable({table,dense=false}){
  if(!table)return null;
  const columns=table.columns||[],rows=table.rows||[];
  const cell={padding:dense?"2px 4px":"4px 6px",borderBottom:"1px solid #e2e8f0",borderRight:"1px solid #eef2f7",whiteSpace:"nowrap",overflow:"hidden",textOverflow:"ellipsis",fontSize:dense?9:11,color:"#1a1a1a"};
  return <div style={{height:"100%",display:"flex",flexDirection:"column",minHeight:0}}>
    {table.title&&<div style={{fontSize:12,fontWeight:900,color:NAVY,padding:"0 0 4px"}}>{table.title}</div>}
    <div style={{flex:1,overflow:"auto",border:"1px solid #cbd5e1",borderRadius:5,background:"#fff",minHeight:0}}>
      <div style={{display:"grid",gridTemplateColumns:`minmax(90px,1.6fr) repeat(${Math.max(0,columns.length-1)},minmax(46px,1fr))`}}>
        {columns.map((column,index)=><div key={`h-${index}`} style={{...cell,background:NAVY,color:"#fff",fontWeight:900,position:"sticky",top:0,textAlign:index?"center":"left"}}>{text(column)}</div>)}
        {rows.map((row,rowIndex)=>columns.map((_column,columnIndex)=><div key={`c-${rowIndex}-${columnIndex}`} style={{...cell,background:rowIndex%2?"#f2f5fa":"#fff",fontWeight:columnIndex?400:800,textAlign:columnIndex?"center":"left"}}>{text(row?.[columnIndex])}</div>))}
      </div>
    </div>
    {table.note&&<div style={{fontSize:9,color:"#707a8a",paddingTop:3}}>{table.note}</div>}
  </div>;
}

function SlideCanvas({page,pageIndex,runs={},tables={},editing=false,charts=[],updatePage,updateSlot,removeSlot,addSlot}){
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
  const addChart=()=>{
    const value=text(chartRef).trim(),chart=charts.find(item=>item.id===value||text(item.name).toLowerCase()===value.toLowerCase());
    if(!chart){toast.error("저장된 Chart ID 또는 Name을 선택해 주세요.");return;}
    if(!guardRoom())return;
    const position=nextPosition(),offset=((page.slots||[]).length%6)*3;
    const size=chartDimensions({chart_width:Number(chart.chart?.width)||DEFAULT_CHART_WIDTH,chart_height:Number(chart.chart?.height)||DEFAULT_CHART_HEIGHT});
    addSlot(pageIndex,{position,kind:"chart",chart_id:chart.id,chart_name:chart.name,chart_label:chart.name||chart.label||chart.id,title:"",x:clamp(5+offset,0,100-size.chart_width/SLIDE_DESIGN_WIDTH*100),y:clamp(16+offset,0,100-size.chart_height/SLIDE_DESIGN_HEIGHT*100),...size});
    setSelectedPosition(position);setChartRef("");
  };
  const addBlock=kind=>{
    if(!guardRoom())return;
    const position=nextPosition(),offset=((page.slots||[]).length%5)*3;
    const base={position,kind,title:"",x:clamp(5+offset,0,100-DEFAULT_BLOCK_WIDTH_PCT),y:clamp(58+offset,0,100-DEFAULT_BLOCK_HEIGHT_PCT),width:DEFAULT_BLOCK_WIDTH_PCT,height:DEFAULT_BLOCK_HEIGHT_PCT};
    if(kind==="split")Object.assign(base,{product:"{{PRODUCT}}",lot:"{{LOT}}",columns:"",display_mode:"matrix"});
    if(kind==="text")Object.assign(base,{text:"내용을 입력하세요. {{LOT}} 같은 변수를 쓸 수 있습니다."});
    if(kind==="stats")Object.assign(base,{source_position:(page.slots||[]).find(slot=>slotKind(slot)==="chart")?.position||1,stats:"n,mean,median,std"});
    addSlot(pageIndex,base);
    setSelectedPosition(position);
  };
  const titleStyle={position:"absolute",left:0,top:0,width:"100%",height:`${TITLE_BAR_PCT}%`,background:NAVY,zIndex:30,display:"flex",alignItems:"center",gap:8,padding:"0 2.1%",boxSizing:"border-box"};
  return <section style={editing?{...card,padding:13}:{}}>
    {editing&&<div style={{display:"flex",gap:8,alignItems:"center",marginBottom:10,flexWrap:"wrap"}}>
      <strong>Page {pageIndex+1}</strong>
      <input aria-label={`Page ${pageIndex+1} 차트 선택`} list="template-report-chart-ids" value={chartRef} onChange={event=>setChartRef(event.target.value)} onKeyDown={event=>{if(event.key==="Enter")addChart();}} placeholder="Chart ID 또는 Name" style={{...input,flex:"1 1 220px",fontFamily:"monospace"}}/>
      <button type="button" onClick={addChart} style={primary}>＋ 차트</button>
      <button type="button" onClick={()=>addBlock("split")} style={btn}>＋ Split 표</button>
      <button type="button" onClick={()=>addBlock("text")} style={btn}>＋ 글</button>
      <button type="button" onClick={()=>addBlock("stats")} style={btn}>＋ 통계표</button>
    </div>}
    <div ref={canvasRef} aria-label={`Page ${pageIndex+1} 자유 배치 슬라이드`} onPointerMove={moveGesture} onPointerUp={endGesture} onPointerCancel={endGesture} style={{position:"relative",background:"#fff",color:"#0f172a",aspectRatio:"16 / 9",overflow:"hidden",boxSizing:"border-box",boxShadow:"0 8px 24px rgba(15,23,42,.10)",border:editing?"1px solid #cbd5e1":"1px solid #e2e8f0",touchAction:"none",userSelect:editing?"none":"auto"}}>
      {/* 상단 네이비 바 — HOL Auto Report 슬라이드 헤더와 같은 규약(#1F497D) */}
      <div style={titleStyle}>
        {editing
          ?<>
            <input aria-label={`Page ${pageIndex+1} 제목`} value={page.title} onChange={event=>updatePage(pageIndex,{title:event.target.value})} placeholder="TITLE" style={{flex:"1 1 auto",minWidth:0,border:"1px dashed rgba(255,255,255,.45)",background:"transparent",color:"#fff",fontSize:"clamp(12px,1.45vw,21px)",fontWeight:900,padding:"0 .4%",boxSizing:"border-box"}}/>
            <input aria-label={`Page ${pageIndex+1} 우측 표기`} value={page.subtitle||""} onChange={event=>updatePage(pageIndex,{subtitle:event.target.value})} placeholder="우측 표기(선택)" style={{flex:"0 1 26%",minWidth:0,border:"1px dashed rgba(255,255,255,.3)",background:"transparent",color:"#d6e3f3",fontSize:"clamp(9px,.85vw,12px)",textAlign:"right",padding:"0 .4%",boxSizing:"border-box"}}/>
          </>
          :<>
            <div style={{flex:"1 1 auto",minWidth:0,color:"#fff",fontSize:"clamp(12px,1.45vw,21px)",fontWeight:900,overflow:"hidden",whiteSpace:"nowrap",textOverflow:"ellipsis"}}>{page.title||"TITLE"}</div>
            {page.subtitle&&<div style={{flex:"0 0 auto",color:"#d6e3f3",fontSize:"clamp(9px,.85vw,12px)",whiteSpace:"nowrap"}}>{page.subtitle}</div>}
          </>}
      </div>
      {editing&&!blocks.length&&<div style={{position:"absolute",inset:"18% 8% 8%",border:"2px dashed #cbd5e1",borderRadius:10,display:"grid",placeItems:"center",textAlign:"center",color:"#94a3b8",fontSize:13,lineHeight:1.6}}>위에서 블록을 추가하세요.<br/>추가된 번호를 드래그하면 좌상단 위치가 정해집니다.</div>}
      {blocks.map(block=>{
        const layout=block.width!=null?block:slotLayout(block),position=Number(block.position),key=block.key||`${pageIndex}:${position}`;
        const kind=slotKind(block),run=runs[key],chart=kind==="chart"?reportChart(run):null,table=kind==="chart"?null:tables[key],active=editing&&position===Number(selectedPosition);
        if(editing)return <button type="button" key={key} aria-label={`${position}번 블록 위치`} title={`${position}. ${KIND_LABEL[kind]} · ${Math.round(layout.width*10)/10}%×${Math.round(layout.height*10)/10}%`} onPointerDown={event=>beginGesture(event,block)} onClick={()=>setSelectedPosition(position)} style={{position:"absolute",left:`${layout.x}%`,top:`${layout.y}%`,width:42,height:42,display:"grid",placeItems:"center",border:active?"3px solid #1d4ed8":"2px solid #2563eb",borderRadius:8,background:active?"#dbeafe":"#eff6ff",color:"#1d4ed8",fontSize:18,fontWeight:950,cursor:"move",boxShadow:"0 3px 10px rgba(37,99,235,.25)",zIndex:20}}>{position}</button>;
        return <div key={key} data-report-chart-key={key} style={{position:"absolute",left:`${layout.x}%`,top:`${layout.y}%`,width:`${layout.width}%`,height:`${layout.height}%`,minWidth:0,minHeight:0,border:kind==="text"?"none":"1px solid #cbd5e1",borderRadius:5,overflow:"hidden",background:kind==="text"?"transparent":"#fff",boxSizing:"border-box",zIndex:5,padding:kind==="chart"?0:6}}>
          {kind==="text"&&<div style={{fontSize:"clamp(9px,1vw,13px)",color:"#1f2937",whiteSpace:"pre-wrap",lineHeight:1.5}}>{block.title&&<div style={{fontWeight:900,color:NAVY,marginBottom:3}}>{block.title}</div>}{block.text}</div>}
          {kind!=="chart"&&kind!=="text"&&table&&<BlockTable table={table} dense/>}
          {kind!=="chart"&&kind!=="text"&&!table&&<div style={{height:"100%",display:"grid",placeItems:"center",padding:8,textAlign:"center",fontSize:11,color:"#64748b"}}><div><strong>{position}. {KIND_LABEL[kind]}</strong><div style={{marginTop:4}}>{kind==="split"?`${block.product||"제품"} / ${block.lot||"랏"}`:"실행하면 표가 채워집니다."}</div></div></div>}
          {kind==="chart"&&!run&&<div style={{height:"100%",display:"grid",placeItems:"center",padding:8,textAlign:"center",fontSize:11,color:"#64748b"}}><div><strong>{position}. {block.chart_label||block.chart_name||block.chart_id}</strong><div style={{marginTop:4}}>{layout.chart_width}×{layout.chart_height}px · 좌상단 ({Math.round(layout.x*10)/10}%, {Math.round(layout.y*10)/10}%)</div></div></div>}
          {kind==="chart"&&run?.error&&<div style={{height:"100%",display:"grid",placeItems:"center",padding:8,textAlign:"center",fontSize:11,color:"#b91c1c"}}>{run.error}</div>}
          {chart&&<FlowPlotlyChart chart={chart} cfg={{...chart,compact:true,hide_title:false}} height={layout.chart_height} dark={false}/>}
        </div>;
      })}
    </div>
    {editing&&<div style={{display:"grid",gap:6,marginTop:10}}>{(page.slots||[]).map(slot=>{
      const kind=slotKind(slot),layout=slotLayout(slot),active=Number(slot.position)===Number(selectedPosition);
      const patch=values=>updateSlot(pageIndex,slot.position,values);
      return <div key={slot.position} onClick={()=>setSelectedPosition(Number(slot.position))} style={{display:"grid",gap:7,padding:"8px 10px",border:`1px solid ${active?"var(--accent)":"var(--border)"}`,borderRadius:7,background:active?"var(--accent-glow)":"var(--bg-primary)",cursor:"pointer"}}>
        <div style={{display:"flex",gap:8,alignItems:"center",flexWrap:"wrap"}}>
          <span style={{width:27,height:27,display:"grid",placeItems:"center",borderRadius:6,background:NAVY,color:"#fff",fontWeight:950}}>{slot.position}</span>
          <span style={{fontSize:10,fontWeight:900,color:"var(--accent)",border:"1px solid var(--accent)",borderRadius:999,padding:"1px 7px"}}>{KIND_LABEL[kind]}</span>
          {kind==="chart"?<><strong style={{fontSize:12}}>{slot.chart_name||slot.chart_id}</strong><code style={{fontSize:9,color:"var(--text-secondary)"}}>{slot.chart_id}</code></>
            :<input aria-label={`${slot.position}번 블록 제목`} value={slot.title||""} onChange={event=>patch({title:event.target.value})} placeholder="블록 제목(선택)" style={{...input,maxWidth:220,padding:"5px 7px",fontSize:12}}/>}
          <span style={{marginLeft:"auto",fontSize:10,color:"var(--text-secondary)"}}>{kind==="chart"?`고정 ${layout.chart_width}×${layout.chart_height}px`:`${Math.round(layout.width)}%×${Math.round(layout.height)}%`}</span>
          <button type="button" aria-label={`${slot.position}번 블록 삭제`} onClick={event=>{event.stopPropagation();removeSlot(pageIndex,slot.position);}} style={{...btn,padding:"4px 7px",color:"var(--danger)"}}>삭제</button>
        </div>
        {kind!=="chart"&&<div style={{display:"flex",gap:7,flexWrap:"wrap",alignItems:"end"}}>
          <label style={label}>폭 %<input type="number" min="8" max="100" value={slot.width??DEFAULT_BLOCK_WIDTH_PCT} onChange={event=>patch({width:Number(event.target.value)})} style={{...input,width:82,marginTop:3,padding:"5px 7px"}}/></label>
          <label style={label}>높이 %<input type="number" min="6" max="100" value={slot.height??DEFAULT_BLOCK_HEIGHT_PCT} onChange={event=>patch({height:Number(event.target.value)})} style={{...input,width:82,marginTop:3,padding:"5px 7px"}}/></label>
          {kind==="split"&&<>
            <label style={label}>제품<input value={slot.product||""} onChange={event=>patch({product:event.target.value})} style={{...input,width:150,marginTop:3,padding:"5px 7px",fontFamily:"monospace"}}/></label>
            <label style={label}>랏<input value={slot.lot||""} onChange={event=>patch({lot:event.target.value})} style={{...input,width:150,marginTop:3,padding:"5px 7px",fontFamily:"monospace"}}/></label>
            <label style={{...label,flex:"1 1 200px"}}>항목(콤마·비우면 전체)<input value={slot.columns||""} onChange={event=>patch({columns:event.target.value})} placeholder="KNOB_1.5_Vt_Split, ..." style={{...input,marginTop:3,padding:"5px 7px",fontFamily:"monospace"}}/></label>
          </>}
          {kind==="stats"&&<>
            <label style={label}>대상 차트 번호<input type="number" min="1" value={slot.source_position||1} onChange={event=>patch({source_position:Number(event.target.value)})} style={{...input,width:110,marginTop:3,padding:"5px 7px"}}/></label>
            <label style={{...label,flex:"1 1 220px"}}>통계 항목<input value={slot.stats||""} onChange={event=>patch({stats:event.target.value})} placeholder="n,mean,median,std" style={{...input,marginTop:3,padding:"5px 7px",fontFamily:"monospace"}}/></label>
          </>}
        </div>}
        {kind==="text"&&<textarea aria-label={`${slot.position}번 글 내용`} value={slot.text||""} onChange={event=>patch({text:event.target.value})} rows={3} style={{...input,fontFamily:"inherit",resize:"vertical"}}/>}
      </div>;
    })}</div>}
  </section>;
}

export default function My_TemplateReport({user}){
  const[templates,setTemplates]=useState([]),[charts,setCharts]=useState([]),[selectedId,setSelectedId]=useState("");
  const[draft,setDraft]=useState(null),[editing,setEditing]=useState(false),[loading,setLoading]=useState(true),[loadError,setLoadError]=useState("");
  const[templateSearch,setTemplateSearch]=useState("");
  const[bindings,setBindings]=useState({}),[repeatText,setRepeatText]=useState("");
  const[contextRootLots,setContextRootLots]=useState(""),[contextWafers,setContextWafers]=useState("");
  const[overrideRecentDays,setOverrideRecentDays]=useState(false),[contextRecentDays,setContextRecentDays]=useState("7"),[contextDateColumn,setContextDateColumn]=useState("tkout_time");
  const[contextColorList,setContextColorList]=useState(""),[contextColorElse,setContextColorElse]=useState("gray");
  const[deck,setDeck]=useState(null),[runs,setRuns]=useState({}),[tables,setTables]=useState({}),[images,setImages]=useState([]);
  const[busy,setBusy]=useState(false),[saving,setSaving]=useState(false),[downloading,setDownloading]=useState("");
  const[runProgress,setRunProgress]=useState("");

  const load=useCallback(async()=>{
    setLoadError("");
    const[data,chartData]=await Promise.all([sf(`${API}/templates`),sf(`${API}/charts`)]);
    setTemplates(data.templates||[]);setCharts(chartData.charts||[]);
    setSelectedId(current=>current||(data.templates?.[0]?.id||""));
  },[]);
  useEffect(()=>{load().catch(error=>{const message=templateApiError(error);setLoadError(message);toast.error(message);}).finally(()=>setLoading(false));},[load]);
  const selected=useMemo(()=>templates.find(template=>template.id===selectedId)||null,[templates,selectedId]);
  const visibleTemplates=useMemo(()=>{const query=text(templateSearch).trim().toLowerCase();return query?templates.filter(template=>`${template.id} ${template.name} ${template.created_by} ${template.updated_by}`.toLowerCase().includes(query)):templates;},[templates,templateSearch]);
  useEffect(()=>{if(selected&&!editing)setDraft(clone(selected));},[selected,editing]);

  const options=draft?.options||{};
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
  // 이 보고서가 어떤 구간을 볼지 — 값은 저장된 차트 코드에서 서버가 읽어 준 것이다.
  const chartSlots=useMemo(()=>{
    const seen=new Set(),out=[];
    (draft?.pages||[]).forEach((page,pageIndex)=>(page.slots||[]).forEach(slot=>{
      if(slotKind(slot)!=="chart")return;
      const key=text(slot.chart_id)||`${pageIndex}:${slot.position}`;
      if(seen.has(key))return;
      seen.add(key);
      out.push({key,chart_id:text(slot.chart_id),chart_name:text(slot.chart_name||slot.chart_label),recent_days:Number(slot.recent_days)||0,date_column:text(slot.date_column)});
    }));
    return out;
  },[draft]);
  const contextColorPreview=useMemo(()=>parseChartColorList(contextColorList),[contextColorList]);
  const runContext=()=>({
    root_lot_ids:listValues(contextRootLots),wafer_ids:listValues(contextWafers),
    override_recent_days:overrideRecentDays,recent_days:overrideRecentDays?Math.max(0,Math.min(3650,Number(contextRecentDays)||0)):0,
    date_column:text(contextDateColumn).trim()||"tkout_time",
    color_rules:contextColorPreview.rows.length?chartColorListRules(contextColorPreview.rows):[],color_else:text(contextColorElse).trim()||"gray",
  });

  const resetRun=()=>{setDeck(null);setRuns({});setTables({});setImages([]);setRunProgress("");};
  const choose=template=>{setSelectedId(template.id);setDraft(clone(template));setEditing(false);resetRun();};
  const createNew=()=>{setSelectedId("");setDraft({id:"",name:"새 Template Report",pages:[newPage(1)],variables:[],options:{cover:true,footer:true,subtitle:"",repeat_variable:"LOT"}});setEditing(true);resetRun();};
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
          if(kind==="chart")return{...base,chart_id:slot.chart_id};
          const sized={...base,width:layout.width,height:layout.height};
          if(kind==="split")return{...sized,product:slot.product||"",lot:slot.lot||"",columns:slot.columns||"",display_mode:slot.display_mode||"matrix"};
          if(kind==="text")return{...sized,text:slot.text||""};
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
        if(block.kind!=="stats")return;
        const table=statsTable(block,nextRuns);
        if(table)nextTables[block.key]=table;
      }));
      setRuns(nextRuns);setTables(nextTables);
      setRunProgress("차트를 화면에 배치하는 중…");
      await new Promise(resolve=>window.setTimeout(resolve,1300));
      const captured=[];
      for(const [index,chart] of (nextDeck.charts||[]).entries()){
        setRunProgress(`PNG 캡처 ${index+1} / ${(nextDeck.charts||[]).length}`);
        if(nextRuns[chart.key]?.error)continue;
        const host=document.querySelector(`[data-report-chart-key="${chart.key}"]`),plot=host?.querySelector(".js-plotly-plot");
        if(!plot)continue;
        const captureWidth=clamp(Number(chart.chart_width)||DEFAULT_CHART_WIDTH,320,2400),captureHeight=clamp(Number(chart.chart_height)||DEFAULT_CHART_HEIGHT,240,1600);
        const dataUrl=await Plotly.toImage(plot,{format:"png",width:captureWidth,height:captureHeight,scale:1});
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
            {editing?<><input aria-label="Template 이름" value={draft.name} onChange={event=>setDraft(old=>({...old,name:event.target.value}))} style={{...input,maxWidth:440,fontSize:16,fontWeight:900}}/><span style={{fontSize:11,color:"var(--text-secondary)"}}>동일 이름은 (2), (3)…으로 자동 저장</span></>:<><strong style={{fontSize:20}}>{draft.name}</strong><code style={{fontSize:10,color:"var(--text-secondary)"}}>{draft.id}</code><span style={{fontSize:11,color:"var(--text-secondary)"}}>16:9 Wide · 표지 + 네이비 타이틀 바 · 원본 차트 크기 고정</span></>}
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
              <span style={{fontSize:11,color:"var(--text-secondary)",flex:"1 1 320px"}}>반복 변수에 랏을 여러 개 넣으면 페이지 묶음이 랏마다 반복됩니다. 조건 비교(A/B)는 ChartBuilder 코드에서 조건 열을 COLOR·X 로 잡아 만들고, 통계표 블록이 그 그룹별 숫자를 깔아 줍니다.</span>
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
            <div style={{border:"1px solid var(--border)",borderRadius:8,background:"var(--bg-primary)",padding:10}}>
              <div style={{display:"flex",gap:8,alignItems:"center",flexWrap:"wrap",marginBottom:8}}>
                <strong style={{fontSize:12}}>모든 차트 연동 컨텍스트</strong>
                <span style={{fontSize:10,color:"var(--text-secondary)"}}>Template을 수정하지 않고 이번 실행에만 적용</span>
                <span style={{marginLeft:"auto",fontSize:10,fontWeight:900,color:"var(--ok)",border:"1px solid var(--ok-line)",borderRadius:999,padding:"2px 7px"}}>동일 데이터 1회 조회 · 동시 최대 {REPORT_QUERY_CONCURRENCY}개</span>
              </div>
              <div style={{display:"grid",gridTemplateColumns:"minmax(240px,.8fr) minmax(320px,1.2fr)",gap:10,alignItems:"start"}}>
                <div style={{display:"grid",gridTemplateColumns:"1fr 1fr",gap:7}}>
                  <label style={label}>Root Lot ID 필터<textarea aria-label="Report 공통 Root Lot ID" value={contextRootLots} onChange={event=>setContextRootLots(event.target.value)} rows={4} placeholder={"A1234\nA5678"} style={{...input,marginTop:3,fontFamily:"monospace",resize:"vertical"}}/></label>
                  <label style={label}>Wafer ID 필터<textarea aria-label="Report 공통 Wafer ID" value={contextWafers} onChange={event=>setContextWafers(event.target.value)} rows={4} placeholder={"1\n2"} style={{...input,marginTop:3,fontFamily:"monospace",resize:"vertical"}}/></label>
                  <label style={{...label,gridColumn:"1 / -1",display:"flex",alignItems:"center",gap:6,cursor:"pointer"}}><input type="checkbox" checked={overrideRecentDays} onChange={event=>setOverrideRecentDays(event.target.checked)}/>저장된 차트 기간을 이번 실행에서 한꺼번에 변경</label>
                  <label style={label}>최근 일수 <span style={{fontWeight:500}}>· 비우면 전체</span><input aria-label="Report 공통 최근 일수" type="number" min="1" max="3650" value={contextRecentDays} onChange={event=>setContextRecentDays(event.target.value)} disabled={!overrideRecentDays} style={{...input,marginTop:3,opacity:overrideRecentDays?1:.55}}/></label>
                  <label style={label}>시간 열<input aria-label="Report 공통 시간 열" value={contextDateColumn} onChange={event=>setContextDateColumn(event.target.value)} disabled={!overrideRecentDays||!Number(contextRecentDays)} style={{...input,marginTop:3,fontFamily:"monospace",opacity:overrideRecentDays?1:.55}}/></label>
                  <div style={{gridColumn:"1 / -1",fontSize:10,color:"var(--text-secondary)"}}>Root Lot {listValues(contextRootLots).length}개 · Wafer {listValues(contextWafers).length}개{overrideRecentDays?` · ${contextRecentDays?`최근 ${Number(contextRecentDays)}일(${contextDateColumn||"tkout_time"})`:"전체 기간"}`:" · 차트별 저장 기간 유지"}</div>
                </div>
                <div style={{display:"grid",gap:6}}>
                  <label style={label}>root_lot_id · wafer_id · color 목록<textarea aria-label="Report 공통 색상 목록" value={contextColorList} onChange={event=>setContextColorList(event.target.value)} rows={6} placeholder={"root_lot_id\twafer_id\tcolor\nA1234\t1\t#dc2626\nA1234\t2\tblue"} style={{...input,marginTop:3,fontFamily:"monospace",resize:"vertical"}}/></label>
                  <div style={{display:"flex",gap:8,alignItems:"center",flexWrap:"wrap"}}><label style={label}>기본색<input aria-label="Report 공통 기본색" value={contextColorElse} onChange={event=>setContextColorElse(event.target.value)} style={{...input,width:105,display:"inline-block",marginLeft:5,padding:"5px 7px"}}/></label><span style={{fontSize:10,color:contextColorPreview.errors.length?"var(--danger)":"var(--text-secondary)"}}>{contextColorPreview.errors[0]||`${contextColorPreview.rows.length}개 색상 지정 · 비우면 저장 차트 색 유지`}</span></div>
                </div>
              </div>
            </div>
            <div style={{display:"flex",gap:9,alignItems:"center",flexWrap:"wrap"}}>
              <button type="button" onClick={runReport} disabled={busy} style={primary}>{busy?"실행·캡처 중…":"실행"}</button>
              <button type="button" onClick={()=>download("pptx")} disabled={!deck||!!downloading} style={btn}>{downloading==="pptx"?"PPTX 생성 중…":"PPTX 다운로드"}</button>
              <button type="button" onClick={()=>download("images")} disabled={!images.length||!!downloading} style={btn}>{downloading==="images"?"ZIP 생성 중…":"차트별 PNG ZIP"}</button>
              <span style={{fontSize:11,color:"var(--text-secondary)"}}>{runProgress||`공통 컨텍스트로 모든 차트를 함께 바꾸고 PPTX로 내려받습니다.${deck?` · ${deck.pages.length}장 생성됨`:""}`}</span>
            </div>
            {!!chartSlots.length&&<div style={{display:"flex",gap:7,flexWrap:"wrap",alignItems:"center"}}>
              <span style={{fontSize:11,color:"var(--text-secondary)"}}>{overrideRecentDays?`이번 실행은 모든 차트 ${contextRecentDays?`최근 ${Number(contextRecentDays)}일(${contextDateColumn||"tkout_time"})`:"전체 기간"}`:"저장된 차트별 조회 기간"} ·</span>
              {chartSlots.map(slot=><span key={`${slot.key}`} title={slot.chart_id} style={{fontSize:11,border:"1px solid var(--border)",borderRadius:999,padding:"2px 9px",color:"var(--text-secondary)",background:"var(--bg-primary)"}}>
                <b style={{color:"var(--text-primary)"}}>{slot.chart_name||slot.chart_id}</b> · {slot.recent_days?`최근 ${slot.recent_days}일(${slot.date_column||"tkout_time"})`:"전체 기간"}
              </span>)}
            </div>}
          </div>}
        </div>
        {editing?<div style={{display:"grid",gap:12}}>
          {draft.pages.map((page,pageIndex)=><div key={page.id} style={{display:"grid",gap:8}}><div style={{display:"flex",justifyContent:"flex-end"}}>{draft.pages.length>1&&<button type="button" onClick={()=>setDraft(old=>({...old,pages:old.pages.filter((_,index)=>index!==pageIndex)}))} style={{...btn,color:"var(--danger)"}}>Page {pageIndex+1} 삭제</button>}</div><SlideCanvas page={page} pageIndex={pageIndex} editing charts={charts} updatePage={updatePage} updateSlot={updateSlot} removeSlot={removeSlot} addSlot={addSlot}/></div>)}
          <datalist id="template-report-chart-ids">{charts.flatMap(chart=>[
            <option key={`name-${chart.id}`} value={chart.name}>{chart.id} · {windowLabel(chart)} · {formatTime(chart.timestamp)} · {chart.label}</option>,
            <option key={`id-${chart.id}`} value={chart.id}>{chart.name} · {windowLabel(chart)} · {formatTime(chart.timestamp)} · {chart.label}</option>,
          ])}</datalist>
          <button type="button" onClick={()=>setDraft(old=>({...old,pages:[...old.pages,newPage(old.pages.length+1)]}))} disabled={draft.pages.length>=30} style={{...btn,justifySelf:"start"}}>＋ 다음 페이지</button>
        </div>:<div style={{display:"grid",gap:16}}>{previewPages.map((page,pageIndex)=><SlideCanvas key={page.id||`p${page.index??pageIndex}`} page={page} pageIndex={page.index??pageIndex} runs={runs} tables={tables}/>)}</div>}
      </>}
    </main>
  </div>;
}
