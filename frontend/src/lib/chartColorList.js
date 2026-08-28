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

function ruleTableRow(rule){
  const raw=text(rule).trim(),split=raw.match(/^(.*?)\s+THEN\s+(.+)$/i);
  if(!split||!COLOR_RE.test(split[2].trim()))return null;
  const conditions=split[1].split(/\s+AND\s+/i);
  if(conditions.length!==2)return null;
  const row={root_lot_id:"",wafer_id:"",color:split[2].trim()};
  for(const condition of conditions){
    const match=condition.match(/^(`?)(root_lot_id|wafer_id)\1\s*(?:=|==)\s*['"]([^'"]+)['"]$/i);
    if(!match)return null;
    const key=match[2].toLowerCase();
    if(row[key])return null;
    row[key]=match[3].trim();
  }
  return row.root_lot_id&&row.wafer_id?row:null;
}

export function parseChartColorList(value,{limit=200}={}){
  const lines=text(value).replace(/\r\n?/g,"\n").split("\n").map(line=>line.trim()).filter(Boolean);
  if(!lines.length)return{rows:[],errors:[],truncated:false};
  const firstCells=splitLine(lines[0]).map(cell=>cell.trim());
  const first=firstCells.map(cell=>headerName(cell));
  const hasHeader=first.some(Boolean);
  if(hasHeader&&(firstCells.length!==3||new Set(first).size!==3||!["root_lot_id","wafer_id","color"].every(name=>first.includes(name)))){
    return{rows:[],errors:["헤더는 root_lot_id, wafer_id, color 3개 열만 사용할 수 있습니다."],truncated:false};
  }
  const indexes=hasHeader?{root_lot_id:first.indexOf("root_lot_id"),wafer_id:first.indexOf("wafer_id"),color:first.indexOf("color")}:{root_lot_id:0,wafer_id:1,color:2};
  const rows=[],errors=[],seen=new Set();
  lines.slice(hasHeader?1:0).forEach((line,index)=>{
    if(rows.length>=limit)return;
    const cells=splitLine(line).map(cell=>cell.trim());
    const lineNumber=index+(hasHeader?2:1);
    if(cells.length!==3){errors.push(`${lineNumber}행: root_lot_id, wafer_id, color 3개 열만 입력해 주세요.`);return;}
    const row={
      root_lot_id:indexes.root_lot_id>=0?cleanMatchValue(cells[indexes.root_lot_id]):"",
      wafer_id:indexes.wafer_id>=0?cleanMatchValue(cells[indexes.wafer_id]):"",
      color:indexes.color>=0?cleanMatchValue(cells[indexes.color]):"",
    };
    if(!row.root_lot_id){errors.push(`${lineNumber}행: root_lot_id가 필요합니다.`);return;}
    if(!row.wafer_id){errors.push(`${lineNumber}행: wafer_id가 필요합니다.`);return;}
    if(/[\r\n'"]/.test(row.root_lot_id)||/[\r\n'"]/.test(row.wafer_id)){errors.push(`${lineNumber}행: ID에 따옴표나 줄바꿈을 쓸 수 없습니다.`);return;}
    if(!COLOR_RE.test(row.color)){errors.push(`${lineNumber}행: color는 red, #2563eb, rgb(...) 형식이어야 합니다.`);return;}
    const key=`${row.root_lot_id.toLowerCase()}\u001f${row.wafer_id.toLowerCase()}`;
    if(seen.has(key)){errors.push(`${lineNumber}행: 같은 root_lot_id와 wafer_id 조합이 중복되었습니다.`);return;}
    seen.add(key);
    rows.push(row);
  });
  return{rows,errors,truncated:lines.length-(hasHeader?1:0)>limit};
}

export function chartColorListRules(rows){
  return(rows||[]).map(row=>{
    const root=text(row.root_lot_id).trim(),wafer=text(row.wafer_id).trim(),color=text(row.color).trim();
    return root&&wafer&&color?`root_lot_id = '${root}' AND wafer_id = '${wafer}' THEN ${color}`:"";
  }).filter(Boolean);
}

export function partitionChartColorRules(rules){
  const rows=[],formulaRules=[];
  (rules||[]).forEach(rule=>{
    const raw=text(rule).trim();
    if(!raw)return;
    const row=ruleTableRow(raw);
    if(row)rows.push(row);
    else formulaRules.push(raw);
  });
  return{rows,formulaRules};
}

export function chartColorListTextFromRules(rules){
  const{rows}=partitionChartColorRules(rules);
  return rows.length?["root_lot_id\twafer_id\tcolor",...rows.map(row=>`${row.root_lot_id}\t${row.wafer_id}\t${row.color}`)].join("\n"):"";
}
