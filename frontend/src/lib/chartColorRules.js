const DAY_MS=24*60*60*1000;
const IDENTIFIER="(`[^`]+`|[A-Za-z_][A-Za-z0-9_]*(?:__[A-Za-z0-9_]+)*)";
const WITHIN_RE=new RegExp(`^${IDENTIFIER}\\s+WITHIN\\s+(\\d+)\\s+DAYS?$`,"i");
const EQUAL_RE=new RegExp(`^${IDENTIFIER}\\s*(?:=|==)\\s*(?:'([^']*)'|\"([^\"]*)\"|(.+))$`);

function text(value){return value==null?"":String(value);}
function columnName(value){return text(value).replace(/^`|`$/g,"");}
function rowValue(row,column){
  if(row&&Object.prototype.hasOwnProperty.call(row,column))return row[column];
  const folded=text(column).toLowerCase();
  const key=Object.keys(row||{}).find(name=>name.toLowerCase()===folded);
  return key==null?undefined:row[key];
}
function matchValue(column,value){
  const name=text(column).toLowerCase(),raw=text(value);
  if(name==="root_lot_id"||name.endsWith("__root_lot_id"))return raw.trim().toUpperCase();
  if(name==="wafer_id"||name.endsWith("__wafer_id"))return raw.trim().toUpperCase().replace(/^(?:#|WAFER|WF|W)\s*/,"");
  return raw;
}

function parseCondition(raw){
  const part=text(raw).trim();
  const within=part.match(WITHIN_RE);
  if(within){
    const days=Number(within[2]);
    if(!Number.isInteger(days)||days<1||days>3650)return null;
    return{kind:"within_days",column:columnName(within[1]),days};
  }
  const equal=part.match(EQUAL_RE);
  if(equal)return{kind:"equals",column:columnName(equal[1]),value:text(equal[2]??equal[3]??equal[4]).trim()};
  return null;
}

export function parseChartColorRules(value){
  const lines=Array.isArray(value)?value:text(value).split(/\r?\n/);
  return lines.map(raw=>text(raw).trim()).filter(Boolean).map((raw,index)=>{
    const split=raw.match(/^(.*?)\s+THEN\s+(.+)$/i);
    if(!split)return{raw,index,error:"THEN 색상 형식이 필요합니다."};
    const conditions=split[1].split(/\s+AND\s+/i).map(parseCondition);
    if(conditions.some(condition=>!condition))return{raw,index,error:"조건은 열 = '값' 또는 시간열 WITHIN 7 DAYS 형식으로 입력해 주세요."};
    return{raw,index,label:split[1].trim(),color:split[2].trim(),conditions};
  });
}

function parseTime(value){
  const raw=text(value).trim();
  if(!raw)return NaN;
  const normalized=raw.replace(/^(\d{4}-\d{2}-\d{2})\s+/,"$1T");
  return new Date(normalized).getTime();
}

export function chartColorRuleMatches(row,rule,nowMs=Date.now()){
  return Boolean(rule&&!rule.error&&(rule.conditions||[]).every(condition=>{
    if(condition.kind==="equals")return matchValue(condition.column,rowValue(row,condition.column))===matchValue(condition.column,condition.value);
    if(condition.kind==="within_days"){
      const timestamp=parseTime(rowValue(row,condition.column));
      return Number.isFinite(timestamp)&&timestamp>=nowMs-condition.days*DAY_MS;
    }
    return false;
  }));
}

export function chartColorValue(row,rules,elseLabel="ELSE",nowMs=Date.now()){
  const match=(rules||[]).find(rule=>chartColorRuleMatches(row,rule,nowMs));
  return match?match.label:elseLabel;
}

export function chartColorMap(rules,elseColor="gray"){
  return Object.fromEntries([...(rules||[]).filter(rule=>!rule.error).map(rule=>[rule.label,rule.color]),["ELSE",elseColor||"gray"]]);
}
