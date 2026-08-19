function text(value){return value==null?"":String(value);}

const COLOR_RE=/^(?:#[0-9a-f]{3,8}|[a-z][a-z0-9_-]{0,31}|rgba?\([^\r\n)]{1,80}\)|hsla?\([^\r\n)]{1,80}\))$/i;
const HEADER_ALIASES={
  root_lot_id:"root_lot_id",root_lot:"root_lot_id",rootlotid:"root_lot_id",lot:"root_lot_id",
  wafer_id:"wafer_id",wafer:"wafer_id",wf:"wafer_id",
  color:"color",colour:"color",색상:"color",색:"color",
};

function headerName(value){return HEADER_ALIASES[text(value).trim().toLowerCase().replace(/[\s-]+/g,"_")]||"";}
function splitLine(line){return line.includes("\t")?line.split("\t"):line.split(",");}
function cleanMatchValue(value){return text(value).trim();}

export function parseChartColorList(value,{limit=300}={}){
  const lines=text(value).replace(/\r\n?/g,"\n").split("\n").map(line=>line.trim()).filter(Boolean);
  if(!lines.length)return{rows:[],errors:[],truncated:false};
  const first=splitLine(lines[0]).map(cell=>headerName(cell));
  const hasHeader=first.includes("color")&&(first.includes("root_lot_id")||first.includes("wafer_id"));
  const indexes=hasHeader?{
    root_lot_id:first.indexOf("root_lot_id"),wafer_id:first.indexOf("wafer_id"),color:first.indexOf("color"),
  }:{root_lot_id:0,wafer_id:1,color:2};
  const rows=[],errors=[];
  lines.slice(hasHeader?1:0).forEach((line,index)=>{
    if(rows.length>=limit)return;
    const cells=splitLine(line).map(cell=>cell.trim());
    const row={
      root_lot_id:indexes.root_lot_id>=0?cleanMatchValue(cells[indexes.root_lot_id]):"",
      wafer_id:indexes.wafer_id>=0?cleanMatchValue(cells[indexes.wafer_id]):"",
      color:indexes.color>=0?cleanMatchValue(cells[indexes.color]):"",
    };
    const lineNumber=index+(hasHeader?2:1);
    if(!row.root_lot_id&&!row.wafer_id){errors.push(`${lineNumber}행: root_lot_id 또는 wafer_id가 필요합니다.`);return;}
    if(/[\r\n'\"]/.test(row.root_lot_id)||/[\r\n'\"]/.test(row.wafer_id)){errors.push(`${lineNumber}행: ID에 따옴표나 줄바꿈을 쓸 수 없습니다.`);return;}
    if(!COLOR_RE.test(row.color)){errors.push(`${lineNumber}행: color는 red, #2563eb, rgb(...) 형식이어야 합니다.`);return;}
    rows.push(row);
  });
  return{rows,errors,truncated:lines.length-(hasHeader?1:0)>limit};
}

export function chartColorListRules(rows){
  return(rows||[]).map(row=>{
    const conditions=[];
    if(text(row.root_lot_id).trim())conditions.push(`root_lot_id = '${text(row.root_lot_id).trim()}'`);
    if(text(row.wafer_id).trim())conditions.push(`wafer_id = '${text(row.wafer_id).trim()}'`);
    return conditions.length?`${conditions.join(" AND ")} THEN ${text(row.color).trim()}`:"";
  }).filter(Boolean);
}

export function chartColorListTextFromRules(rules){
  const rows=[];
  (rules||[]).forEach(rule=>{
    const split=text(rule).match(/^(.*?)\s+THEN\s+(.+)$/i);if(!split)return;
    const values={root_lot_id:"",wafer_id:"",color:split[2].trim()};
    split[1].split(/\s+AND\s+/i).forEach(condition=>{
      const match=condition.match(/^(`?)(root_lot_id|wafer_id)\1\s*(?:=|==)\s*['\"]([^'\"]*)['\"]$/i);
      if(match)values[match[2].toLowerCase()]=match[3];
    });
    if((values.root_lot_id||values.wafer_id)&&COLOR_RE.test(values.color))rows.push(values);
  });
  return rows.length?["root_lot_id\twafer_id\tcolor",...rows.map(row=>`${row.root_lot_id}\t${row.wafer_id}\t${row.color}`)].join("\n"):"";
}
