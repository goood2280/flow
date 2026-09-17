import { useState, useRef, useEffect } from "react";

const DEFAULT_MIN_ROWS=10;
const DEFAULT_MAX_ROWS=200;
const VIRTUAL_ROW_HEIGHT=33;
const VIRTUAL_OVERSCAN=10;

function text(value){return value==null?"":String(value);}
function blankRow(columns){return Object.fromEntries(columns.map(column=>[column,"" ]));}

export function normalizeSpreadsheetRows(rows,columns,{minRows=DEFAULT_MIN_ROWS,maxRows=DEFAULT_MAX_ROWS}={}){
  const names=(columns||[]).map(String);
  const next=(rows||[]).slice(0,maxRows).map(row=>Object.fromEntries(names.map(name=>[name,text(row?.[name])])));
  let last=next.length-1;
  while(last>=0&&!names.some(name=>text(next[last]?.[name]).trim()))last-=1;
  const target=Math.min(maxRows,Math.max(minRows,last+2));
  next.length=Math.min(next.length,target);
  while(next.length<target)next.push(blankRow(names));
  return next;
}

export function spreadsheetTextFromRows(rows,columns,{includeHeader=true}={}){
  const names=(columns||[]).map(String);
  const filled=(rows||[]).filter(row=>names.some(name=>text(row?.[name]).trim()));
  if(!filled.length)return"";
  const body=filled.map(row=>names.map(name=>text(row?.[name]).trim()).join("\t"));
  return(includeHeader?[names.join("\t"),...body]:body).join("\n");
}

function headerIndexes(cells,columns,aliases={}){
  const normalize=value=>text(value).trim().toLowerCase().replace(/[\s-]+/g,"_");
  const names=(columns||[]).map(String),lookup={};
  names.forEach(name=>{lookup[normalize(name)]=name;});
  Object.entries(aliases||{}).forEach(([alias,name])=>{if(names.includes(name))lookup[normalize(alias)]=name;});
  const resolved=cells.map(cell=>lookup[normalize(cell)]||"");
  return names.every(name=>resolved.includes(name))?Object.fromEntries(names.map(name=>[name,resolved.indexOf(name)])):null;
}

export default function SpreadsheetPasteGrid({
  columns,
  rows,
  onChange,
  ariaLabel="spreadsheet input",
  aliases={},
  columnLabels={},
  placeholders={},
  colorColumn="",
  readOnlyColumns=[],
  pinnedRows=[],
  renderPinnedCell,
  showRowNumbers=true,
  disabled=false,
  cellTextColor="#000000",
  minRows=DEFAULT_MIN_ROWS,
  maxRows=DEFAULT_MAX_ROWS,
  maxHeight=365,
  borderRadius=7,
  minTableWidth=560,
}){
  const names=(columns||[]).map(String);
  const readOnly=new Set((readOnlyColumns||[]).map(String));
  const pinned=(pinnedRows||[]).slice(0,maxRows);
  const commit=next=>onChange?.(normalizeSpreadsheetRows(next,names,{minRows,maxRows}));
  const updateCell=(rowIndex,column,value)=>{if(!readOnly.has(column))commit(rows.map((row,index)=>index===rowIndex?{...row,[column]:value}:row));};
  const paste=(event,rowIndex,columnIndex)=>{
    if(disabled)return;
    const raw=event.clipboardData?.getData("text/plain")||"";
    if(!raw)return;
    event.preventDefault();
    const lines=raw.replace(/\r\n?/g,"\n").split("\n");
    while(lines.length&&!lines[lines.length-1].trim())lines.pop();
    if(!lines.length)return;
    const split=line=>line.includes("\t")?line.split("\t"):line.split(",");
    const header=headerIndexes(split(lines[0]),names,aliases);
    const matrix=(header?lines.slice(1):lines).map(line=>{
      const cells=split(line);
      return header?names.map(name=>cells[header[name]]??""):cells;
    });
    const startColumn=header?0:columnIndex,next=rows.map(row=>({...row}));
    matrix.slice(0,maxRows-rowIndex).forEach((cells,rowOffset)=>{
      const target=rowIndex+rowOffset;
      while(next.length<=target&&next.length<maxRows)next.push(blankRow(names));
      cells.slice(0,names.length-startColumn).forEach((value,cellOffset)=>{
        const name=names[startColumn+cellOffset];
        if(!readOnly.has(name))next[target][name]=text(value).trim();
      });
    });
    commit(next);
  };

  const containerRef = useRef(null);
  const [scrollTop, setScrollTop] = useState(0);
  const [viewportHeight, setViewportHeight] = useState(maxHeight);

  useEffect(() => {
    if (containerRef.current) {
      setViewportHeight(containerRef.current.clientHeight || maxHeight);
    }
  }, [maxHeight]);

  const onScroll = event => {
    setScrollTop(event.currentTarget.scrollTop);
  };

  const totalRows = (rows || []).length;
  const isVirtualized = totalRows > 30;
  const rawStart = isVirtualized ? Math.max(0, Math.floor(scrollTop / VIRTUAL_ROW_HEIGHT) - VIRTUAL_OVERSCAN) : 0;
  const safeStartIndex = Math.min(rawStart, Math.max(0, totalRows - 1));
  const rawEnd = isVirtualized ? Math.ceil((scrollTop + viewportHeight) / VIRTUAL_ROW_HEIGHT) + VIRTUAL_OVERSCAN : totalRows;
  const safeEndIndex = Math.min(totalRows, Math.max(safeStartIndex, rawEnd));

  const topHeight = isVirtualized ? safeStartIndex * VIRTUAL_ROW_HEIGHT : 0;
  const bottomHeight = isVirtualized ? Math.max(0, (totalRows - safeEndIndex) * VIRTUAL_ROW_HEIGHT) : 0;
  const visibleRows = isVirtualized ? rows.slice(safeStartIndex, safeEndIndex) : (rows || []);
  const colSpan = (showRowNumbers ? 1 : 0) + names.length;

  return <div ref={containerRef} onScroll={onScroll} style={{overflow:"auto",maxHeight,border:"1px solid var(--border)",borderRadius,background:"var(--bg-primary)"}}>
    <table aria-label={ariaLabel} style={{width:"100%",minWidth:minTableWidth,tableLayout:"fixed",borderCollapse:"separate",borderSpacing:0,fontSize:12}}>
      <colgroup>{showRowNumbers&&<col style={{width:42}}/>}{names.map(name=><col key={name} style={name===colorColumn?{width:"34%"}:undefined}/>)}</colgroup>
      <thead><tr>
        {showRowNumbers&&<th aria-label="행 번호" style={{position:"sticky",top:0,zIndex:2,padding:"8px 6px",textAlign:"center",background:"var(--bg-tertiary)",borderRight:"1px solid var(--border)",borderBottom:"1px solid var(--border)",color:cellTextColor,fontWeight:600}}>#</th>}
        {names.map(name=><th key={name} style={{position:"sticky",top:0,zIndex:2,padding:"8px 9px",textAlign:"left",background:"var(--bg-tertiary)",borderRight:"1px solid var(--border)",borderBottom:"1px solid var(--border)",fontFamily:"monospace",color:cellTextColor,fontWeight:600}}>{columnLabels[name]||name}</th>)}
      </tr></thead>
      <tbody>
      {pinned.map((row,rowIndex)=><tr key={`pinned-${row.__key||rowIndex}`}>
        {showRowNumbers&&<th scope="row" style={{padding:"7px 6px",textAlign:"center",fontWeight:600,color:"var(--accent)",background:"var(--surface-selected)",borderRight:"1px solid var(--border)",borderBottom:"1px solid var(--border)"}}>{rowIndex+1}</th>}
        {names.map(name=><td key={name} style={{padding:0,borderRight:"1px solid var(--border)",borderBottom:"1px solid var(--border)",background:"var(--surface-selected)"}}>
          {renderPinnedCell
            ? renderPinnedCell({row,rowIndex,column:name})
            : <div style={{padding:"7px 9px",color:cellTextColor,fontFamily:"monospace",fontSize:12,fontWeight:500}}>{text(row?.[name])}</div>}
        </td>)}
      </tr>)}
      {topHeight > 0 && <tr style={{ height: topHeight, pointerEvents: "none" }}><td colSpan={colSpan} style={{ padding: 0, border: 0, height: topHeight }} /></tr>}
      {visibleRows.map((row,index)=>{
        const actualIndex = isVirtualized ? safeStartIndex + index : index;
        return <tr key={actualIndex}>
          {showRowNumbers&&<th scope="row" style={{padding:"7px 6px",textAlign:"center",fontWeight:600,color:cellTextColor,background:"var(--bg-secondary)",borderRight:"1px solid var(--border)",borderBottom:"1px solid var(--border)"}}>{pinned.length+actualIndex+1}</th>}
          {names.map((name,columnIndex)=><td key={name} style={{position:"relative",padding:0,borderRight:"1px solid var(--border)",borderBottom:"1px solid var(--border)"}}>
            {name===colorColumn&&text(row[name]).trim()&&<span aria-hidden="true" style={{position:"absolute",left:8,top:"50%",transform:"translateY(-50%)",width:13,height:13,borderRadius:3,background:row[name],border:"1px solid #94a3b8",pointerEvents:"none"}}/>}
            <input aria-label={`${pinned.length+actualIndex+1}행 ${columnLabels[name]||name}`} value={row[name]||""} disabled={disabled} readOnly={readOnly.has(name)} onChange={event=>updateCell(actualIndex,name,event.target.value)} onPaste={event=>paste(event,actualIndex,columnIndex)} spellCheck={false} placeholder={actualIndex===0?text(placeholders[name]):""} style={{width:"100%",boxSizing:"border-box",border:0,borderRadius:0,outlineOffset:-2,background:readOnly.has(name)?"var(--bg-secondary)":"transparent",color:cellTextColor,fontWeight:500,padding:name===colorColumn&&text(row[name]).trim()?"7px 9px 7px 29px":"7px 9px",fontFamily:"monospace",fontSize:12}}/>
          </td>)}
        </tr>;
      })}
      {bottomHeight > 0 && <tr style={{ height: bottomHeight, pointerEvents: "none" }}><td colSpan={colSpan} style={{ padding: 0, border: 0, height: bottomHeight }} /></tr>}
      </tbody>
    </table>
  </div>;
}
