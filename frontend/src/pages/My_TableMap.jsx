import { useState, useEffect, useRef } from "react";
import Loading from "../components/Loading";
import S3StatusLight from "../components/S3StatusLight";
import { sf } from "../lib/api";
const API="/api/dbmap";

const NODE_COLORS={table:"#f97316",group:"#a855f7",db_ref:"#3b82f6"};
// Table type colors (overrides default table color based on table_type)
const TABLE_TYPE_COLORS={data:"#f97316",matching:"#10b981",rulebook:"#eab308"};
const TABLE_TYPE_ICONS={data:"📋",matching:"🔗",rulebook:"📖"};
const NODE_ICONS={table:"📋",group:"📚",db_ref:"🗄️"};

// Inject overlay styles at module load — hardcoded colors prevent any flash
if(typeof document!=="undefined"&&!document.getElementById("tm-styles")){
  const s=document.createElement("style");s.id="tm-styles";
  s.textContent=`
.tm-overlay{position:fixed;inset:0;background:rgba(0,0,0,0.8)!important;z-index:9999;display:flex;align-items:center;justify-content:center;padding:20px}
.tm-modal{background:#262626!important;border-radius:12px;padding:20px;border:1px solid #333!important;color:#e5e5e5!important;--bg-primary:#1a1a1a;--bg-secondary:#262626;--bg-tertiary:#333;--bg-hover:#2a2a2a;--border:#444;--text-primary:#e5e5e5;--text-secondary:#a3a3a3}
.tm-modal select option{background:#1a1a1a;color:#e5e5e5}
.tm-modal input::placeholder,.tm-modal textarea::placeholder{color:#777}
.tm-modal h1,.tm-modal h2,.tm-modal h3,.tm-modal h4{color:#e5e5e5}
`;
  document.head.appendChild(s);
}

// ─── Graph view with zoom/pan ───────────────────────────
function GraphView({config,groups,tables,onNodeClick,onNodeDblClick,onAddRelation,onSavePosition,onNodeRightClick,selectedNodeId,onEditRelation,lineageEdges,showLineage}){
  const[drag,setDrag]=useState(null);const[relStart,setRelStart]=useState(null);
  const[relDrag,setRelDrag]=useState(null); // {fromNode, mx, my} for relation drag
  const[hoverNode,setHoverNode]=useState(null);
  const[zoom,setZoom]=useState(1);const[pan,setPan]=useState({x:0,y:0});const[panning,setPanning]=useState(null);
  const svgRef=useRef();const containerRef=useRef();
  const NW=150,NH=50,PAD=16;

  // Zoom with mouse wheel
  const onWheel=(e)=>{e.preventDefault();const d=e.deltaY>0?-0.08:0.08;setZoom(z=>Math.max(0.2,Math.min(2,z+d)));};
  // Pan with middle-click or empty-area drag
  const onBgMouseDown=(e)=>{if(e.button===1||(e.button===0&&!e.shiftKey&&e.target===svgRef.current)){setPanning({startX:e.clientX-pan.x,startY:e.clientY-pan.y});}};
  const onMouseMove=(e)=>{
    if(panning){setPan({x:e.clientX-panning.startX,y:e.clientY-panning.startY});return;}
    const rect=svgRef.current.getBoundingClientRect();
    const mx=(e.clientX-rect.left-pan.x)/zoom, my=(e.clientY-rect.top-pan.y)/zoom;
    if(relDrag){setRelDrag({...relDrag,mx,my});return;}
    if(!drag)return;
    setDrag({...drag,x:mx-drag.offsetX,y:my-drag.offsetY});
  };
  const onMouseUp=(e)=>{
    if(panning){setPanning(null);return;}
    if(relDrag){
      // Check if released over a node
      if(hoverNode&&hoverNode.id!==relDrag.fromNode.id){onAddRelation(relDrag.fromNode,hoverNode);}
      setRelDrag(null);return;
    }
    if(drag){onSavePosition(drag.id,drag.x,drag.y);setDrag(null);}
  };
  const onNodeMouseDown=(e,node)=>{
    e.stopPropagation();
    const rect=svgRef.current.getBoundingClientRect();
    const mx=(e.clientX-rect.left-pan.x)/zoom, my=(e.clientY-rect.top-pan.y)/zoom;
    // Ctrl/Cmd+drag = relation (start line from node)
    if(e.ctrlKey||e.metaKey){setRelDrag({fromNode:node,mx,my});return;}
    // Shift+click = legacy relation mode (kept for compatibility)
    if(e.shiftKey){setRelStart(node);return;}
    // Default = move
    setDrag({id:node.id,offsetX:mx-(node.x||100),offsetY:my-(node.y||100),x:node.x||100,y:node.y||100,startX:node.x||100,startY:node.y||100});
  };
  const onNodeMouseEnter=(node)=>{if(relDrag)setHoverNode(node);};
  const onNodeMouseLeave=()=>{if(relDrag)setHoverNode(null);};
  const onNodeClickHandler=(e,node)=>{
    if(relStart){if(relStart.id!==node.id)onAddRelation(relStart,node);setRelStart(null);return;}
    // Single click = select
    const moved=drag&&(Math.abs(drag.x-drag.startX)>3||Math.abs(drag.y-drag.startY)>3);
    if(!moved)onNodeClick(node);
  };
  const onNodeDblClickHandler=(e,node)=>{
    e.stopPropagation();
    if(onNodeDblClick)onNodeDblClick(node);
  };
  const getNodePos=(n)=>drag&&drag.id===n.id?{x:drag.x,y:drag.y}:{x:n.x||100,y:n.y||100};
  const nodeById=(id)=>config.nodes.find(n=>n.id===id);

  // Build group→member table map (table data objects, not nodes)
  const groupMembers={};
  (groups||[]).forEach(g=>{groupMembers[g.id]=[];});
  (tables||[]).forEach(t=>{if(t.group_id&&groupMembers[t.group_id])groupMembers[t.group_id].push(t);});

  // Build set of table IDs that belong to a group (these are rendered INSIDE the group box, not as standalone)
  const groupedTableIds=new Set();
  (tables||[]).forEach(t=>{if(t.group_id)groupedTableIds.add(t.id);});

  const groupNodes=(config.nodes||[]).filter(n=>n.kind==="group");
  // Standalone = non-group nodes that are NOT grouped tables
  const standaloneNodes=(config.nodes||[]).filter(n=>n.kind!=="group"&&!(n.kind==="table"&&groupedTableIds.has(n.ref_id)));

  return(<div ref={containerRef} style={{position:"relative",width:"100%",height:"calc(100vh - 180px)",background:"radial-gradient(circle at 1px 1px, rgba(255,255,255,0.08) 1px, transparent 0) 0 0/20px 20px, linear-gradient(180deg, rgba(249,115,22,0.05), rgba(168,85,247,0.04)), var(--bg-primary)",borderRadius:8,border:"1px solid var(--border)",overflow:"hidden"}}>
    <div style={{position:"absolute",top:8,left:8,fontSize:12,color:"var(--text-primary)",zIndex:2,background:"var(--bg-card)",padding:"8px 12px",borderRadius:6,border:"1px solid var(--border)",lineHeight:1.7,boxShadow:"0 2px 8px rgba(0,0,0,0.3)"}}>
      <div style={{fontSize:11,fontWeight:800,color:"#ef4444",marginBottom:4,letterSpacing:"0.05em"}}>📘 GUIDE</div>
      <div><b style={{color:"var(--accent)"}}>더블클릭</b> → 테이블/그룹 편집</div>
      <div><b style={{color:"var(--accent)"}}>드래그</b> → 노드 이동</div>
      <div><b style={{color:"var(--accent)"}}>Ctrl + 드래그</b> 다른 노드로 → 관계 생성</div>
      <div><b style={{color:"var(--accent)"}}>휠 스크롤</b> → 확대/축소</div>
    </div>
    {relStart&&<div style={{position:"absolute",top:32,left:8,fontSize:11,color:"var(--accent)",zIndex:2,background:"var(--accent-glow)",padding:"4px 8px",borderRadius:4}}>
출발 <b>{relStart.name}</b> → 대상 클릭 (<span onClick={()=>setRelStart(null)} style={{cursor:"pointer",textDecoration:"underline"}}>취소</span>)
    </div>}
    {/* Zoom controls */}
    <div style={{position:"absolute",top:8,right:8,display:"flex",gap:4,zIndex:2}}>
      <span onClick={()=>setZoom(z=>Math.min(2,z+0.15))} style={{width:28,height:28,display:"flex",alignItems:"center",justifyContent:"center",borderRadius:4,background:"var(--bg-card)",border:"1px solid var(--border)",cursor:"pointer",fontSize:14,fontWeight:700,color:"var(--text-primary)"}}>+</span>
      <span onClick={()=>setZoom(1)} style={{minWidth:36,height:28,display:"flex",alignItems:"center",justifyContent:"center",borderRadius:4,background:"var(--bg-card)",border:"1px solid var(--border)",cursor:"pointer",fontSize:10,fontWeight:600,color:"var(--text-secondary)",fontFamily:"monospace"}}>{Math.round(zoom*100)}%</span>
      <span onClick={()=>setZoom(z=>Math.max(0.2,z-0.15))} style={{width:28,height:28,display:"flex",alignItems:"center",justifyContent:"center",borderRadius:4,background:"var(--bg-card)",border:"1px solid var(--border)",cursor:"pointer",fontSize:14,fontWeight:700,color:"var(--text-primary)"}}>-</span>
      <span onClick={()=>{setZoom(0.5);setPan({x:0,y:0});}} style={{height:28,display:"flex",alignItems:"center",justifyContent:"center",borderRadius:4,background:"var(--bg-card)",border:"1px solid var(--border)",cursor:"pointer",fontSize:10,padding:"0 8px",color:"var(--text-secondary)"}}>맞추기</span>
    </div>
    <svg ref={svgRef} width="100%" height="100%" onWheel={onWheel} onMouseDown={onBgMouseDown} onMouseMove={onMouseMove} onMouseUp={onMouseUp} onMouseLeave={onMouseUp} style={{cursor:panning?"grabbing":drag?"grabbing":"default"}}>
      <g transform={`translate(${pan.x},${pan.y}) scale(${zoom})`}>
      <defs>
        <marker id="arrow" viewBox="0 0 10 10" refX="9" refY="5" markerWidth="8" markerHeight="8" orient="auto">
          <path d="M 0 0 L 10 5 L 0 10 z" fill="var(--accent)" opacity="0.8"/></marker>
        <marker id="arrow-lineage" viewBox="0 0 10 10" refX="9" refY="5" markerWidth="7" markerHeight="7" orient="auto">
          <path d="M 0 0 L 10 5 L 0 10 z" fill="#06b6d4" opacity="0.85"/></marker>
      </defs>

      {/* Group bounding boxes with member tables rendered inside */}
      {groupNodes.map(gn=>{
        const gp=getNodePos(gn);const grp=(groups||[]).find(g=>g.id===gn.ref_id);
        const members=grp?groupMembers[grp.id]||[]:[];
        const HEADER=28,INNER_PAD=12,COL_GAP=8;
        // Compute group box size based on member count
        const cols=Math.min(members.length,3)||1;
        const rows_count=Math.ceil(members.length/cols)||1;
        const bw=Math.max(NW+INNER_PAD*2, cols*(NW+COL_GAP)-COL_GAP+INNER_PAD*2);
        const bh=HEADER+rows_count*(NH+COL_GAP)-COL_GAP+INNER_PAD*2;

        return(<g key={gn.id}>
          {/* Group container */}
          <rect x={gp.x} y={gp.y} width={bw} height={members.length?bh:70} rx={12}
            fill="#a855f726" stroke="#a855f7" strokeWidth={2.5} strokeDasharray="6,4" opacity={0.95}
            onMouseDown={e=>onNodeMouseDown(e,gn)} onClick={e=>onNodeClickHandler(e,gn)} onDoubleClick={e=>onNodeDblClickHandler(e,gn)}
            onMouseEnter={()=>onNodeMouseEnter(gn)} onMouseLeave={onNodeMouseLeave}
            style={{cursor:relDrag?"crosshair":"move"}}/>
          <text x={gp.x+INNER_PAD} y={gp.y+18} fill="#a855f7" fontSize={11} fontWeight={700}>📚 {gn.name}</text>
          <text x={gp.x+bw-INNER_PAD} y={gp.y+18} fill="#a855f766" fontSize={9} textAnchor="end">{members.length} 테이블</text>

          {/* Member tables rendered inside the group box */}
          {members.map((m,mi)=>{
            const col=mi%cols, row=Math.floor(mi/cols);
            const mx=gp.x+INNER_PAD+col*(NW+COL_GAP);
            const my=gp.y+HEADER+INNER_PAD+row*(NH+COL_GAP);
            // Build a pseudo-node for the member table (they don't have standalone nodes in config)
            const memberNode=config.nodes.find(n=>n.kind==="table"&&n.ref_id===m.id)||{id:"member_"+m.id,kind:"table",ref_id:m.id,name:m.name};
            const isSel=selectedNodeId===memberNode.id;
            const tType=m.table_type||"data";
            const tColor=TABLE_TYPE_COLORS[tType]||"#f97316";
            const tIcon=TABLE_TYPE_ICONS[tType]||"📋";
            return(<g key={m.id} transform={`translate(${mx},${my})`}
              onMouseDown={e=>{e.stopPropagation();}}
              onClick={e=>{e.stopPropagation();onNodeClick(memberNode);}}
              onDoubleClick={e=>{e.stopPropagation();if(onNodeDblClick)onNodeDblClick(memberNode);}}
              style={{cursor:"pointer"}}>
              <rect width={NW} height={NH} rx={6} fill={tColor+"55"} stroke={isSel?"#fbbf24":tColor} strokeWidth={isSel?2.5:1.5}/>
              <text x={10} y={20} fill={tColor} fontSize={12}>{tIcon}</text>
              <text x={30} y={20} fill="#fff" fontSize={10} fontWeight={700} style={{textShadow:"0 1px 2px rgba(0,0,0,0.5)"}}>{(m.name||"?").slice(0,14)}</text>
              <text x={30} y={36} fill="rgba(255,255,255,0.85)" fontSize={8}>{tType} · {m.rows?.length||0}r · {m.columns?.length||0}c</text>
            </g>);
          })}
          {members.length===0&&<text x={gp.x+bw/2} y={gp.y+48} fill="#a855f744" fontSize={10} textAnchor="middle">빈 그룹</text>}
        </g>);
      })}

      {/* Relations — lines connect from box edges */}
      {(config.relations||[]).map(r=>{
        const a=nodeById(r.from);const b=nodeById(r.to);if(!a||!b)return null;
        const getBox=(n)=>{
          const p=getNodePos(n);
          if(n.kind==="group"){
            const grp=(groups||[]).find(g=>g.id===n.ref_id);
            const mc=grp?(groupMembers[grp.id]||[]).length:0;
            const cols=Math.min(mc,3)||1;const rows=Math.ceil(mc/cols)||1;
            const bw=Math.max(NW+PAD*2,cols*(NW+8)-8+PAD*2);
            const bh=28+rows*(NH+8)-8+PAD*2;
            return{cx:p.x+bw/2,cy:p.y+bh/2,hw:bw/2,hh:bh/2};
          }
          return{cx:p.x+NW/2,cy:p.y+NH/2,hw:NW/2,hh:NH/2};
        };
        const ba=getBox(a),bb=getBox(b);
        // Direction vector
        const dx=bb.cx-ba.cx,dy=bb.cy-ba.cy,len=Math.sqrt(dx*dx+dy*dy)||1;
        // Exit point on box edge (simplified: use the side facing target)
        const ax1=ba.cx+ba.hw*(dx/len)*0.95,ay1=ba.cy+ba.hh*(dy/len)*0.95;
        const bx1=bb.cx-bb.hw*(dx/len)*0.95,by1=bb.cy-bb.hh*(dy/len)*0.95;
        const mx=(ax1+bx1)/2,my=(ay1+by1)/2;
        // v7.3: Split comma/whitespace separated columns into pairs for table-style label
        const fromCols=(r.from_col||"").split(/[,\s]+/).filter(Boolean);
        const toCols=(r.to_col||"").split(/[,\s]+/).filter(Boolean);
        const pairs=Math.max(fromCols.length,toCols.length);
        const shown=Math.min(pairs,5);
        const extraCount=pairs-shown;
        // Max width per cell based on longest text (6px per char at fontSize 9)
        const maxLeft=Math.max(...(fromCols.slice(0,shown).map(c=>c.length).concat([3])));
        const maxRight=Math.max(...(toCols.slice(0,shown).map(c=>c.length).concat([3])));
        const cellL=Math.max(50,maxLeft*6+8), cellR=Math.max(50,maxRight*6+8), arrowW=18;
        const boxW=cellL+arrowW+cellR;
        const rowH=14, headerH= pairs>1?14:0;
        const boxH=headerH+shown*rowH+(extraCount>0?rowH:0)+6;
        const boxX=mx-boxW/2, boxY=my-boxH/2;
        return(<g key={r.id}>
          <line x1={ax1} y1={ay1} x2={bx1} y2={by1} stroke="var(--accent)" strokeWidth={2} strokeOpacity={0.5} markerEnd="url(#arrow)"/>
          {pairs>0&&(<g>
            <rect x={boxX} y={boxY} width={boxW} height={boxH} rx={5}
                  fill="var(--bg-card,#1e1e1e)" stroke="var(--accent)" strokeOpacity={0.6} strokeWidth={1}/>
            {pairs>1&&(<>
              <text x={boxX+cellL/2} y={boxY+10} textAnchor="middle" fill="var(--accent)" fontSize={8} fontWeight={700} style={{fontFamily:"monospace"}}>{(a.name||"").slice(0,Math.floor(cellL/6))}</text>
              <text x={boxX+cellL+arrowW+cellR/2} y={boxY+10} textAnchor="middle" fill="var(--accent)" fontSize={8} fontWeight={700} style={{fontFamily:"monospace"}}>{(b.name||"").slice(0,Math.floor(cellR/6))}</text>
              <line x1={boxX+2} y1={boxY+headerH} x2={boxX+boxW-2} y2={boxY+headerH} stroke="var(--accent)" strokeOpacity={0.25}/>
            </>)}
            {Array.from({length:shown}).map((_,i)=>{
              const y=boxY+headerH+(i+1)*rowH-3;
              return(<g key={i}>
                <text x={boxX+cellL-4} y={y} textAnchor="end" fill="var(--text-primary)" fontSize={9} style={{fontFamily:"monospace"}}>{fromCols[i]||"-"}</text>
                <text x={boxX+cellL+arrowW/2} y={y} textAnchor="middle" fill="var(--accent)" fontSize={10}>↔</text>
                <text x={boxX+cellL+arrowW+4} y={y} textAnchor="start" fill="var(--text-primary)" fontSize={9} style={{fontFamily:"monospace"}}>{toCols[i]||"-"}</text>
              </g>);
            })}
            {extraCount>0&&<text x={boxX+boxW/2} y={boxY+boxH-4} textAnchor="middle" fill="var(--text-secondary)" fontSize={8}>+{extraCount} 개 더</text>}
          </g>)}
          {/* Edit pencil — pushed to the right of the label box */}
          <g transform={`translate(${boxX+boxW+4},${my-9})`} onClick={e=>{e.stopPropagation();if(onEditRelation)onEditRelation(r);}} style={{cursor:"pointer"}}>
            <circle cx={9} cy={9} r={9} fill="var(--bg-card,#2a2a2a)" stroke="var(--accent)" strokeWidth={1} opacity={0.9}/>
            <text x={9} y={13} textAnchor="middle" fill="var(--accent)" fontSize={10}>✏</text>
          </g>
        </g>);
      })}

      {/* v8.6.3 Lineage overlay — inferred dataflow arrows (dashed cyan).
          declared 는 위 relations 블록과 중복이므로 inferred 만 표시. */}
      {showLineage && (lineageEdges||[]).filter(e=>e.kind==="inferred").map((e,i)=>{
        const a=nodeById(e.from_id);const b=nodeById(e.to_id);if(!a||!b)return null;
        const getBox=(n)=>{
          const p=getNodePos(n);
          if(n.kind==="group"){
            const grp=(groups||[]).find(g=>g.id===n.ref_id);
            const mc=grp?(groupMembers[grp.id]||[]).length:0;
            const cols=Math.min(mc,3)||1;const rows=Math.ceil(mc/cols)||1;
            const bw=Math.max(NW+PAD*2,cols*(NW+8)-8+PAD*2);
            const bh=28+rows*(NH+8)-8+PAD*2;
            return{cx:p.x+bw/2,cy:p.y+bh/2,hw:bw/2,hh:bh/2};
          }
          return{cx:p.x+NW/2,cy:p.y+NH/2,hw:NW/2,hh:NH/2};
        };
        const ba=getBox(a),bb=getBox(b);
        const dx=bb.cx-ba.cx,dy=bb.cy-ba.cy,len=Math.sqrt(dx*dx+dy*dy)||1;
        const ax1=ba.cx+ba.hw*(dx/len)*0.95,ay1=ba.cy+ba.hh*(dy/len)*0.95;
        const bx1=bb.cx-bb.hw*(dx/len)*0.95,by1=bb.cy-bb.hh*(dy/len)*0.95;
        const mx=(ax1+bx1)/2,my=(ay1+by1)/2;
        return(<g key={"lin_"+i} style={{pointerEvents:"none"}}>
          <line x1={ax1} y1={ay1} x2={bx1} y2={by1} stroke="#06b6d4" strokeWidth={1.6} strokeOpacity={0.75} strokeDasharray="6,4" markerEnd="url(#arrow-lineage)"/>
          <rect x={mx-(e.reason.length*3.2)} y={my-7} width={e.reason.length*6.4} height={14} rx={3} fill="rgba(6,182,212,0.15)" stroke="#06b6d4" strokeOpacity={0.5}/>
          <text x={mx} y={my+3} textAnchor="middle" fill="#06b6d4" fontSize={9} fontWeight={700} style={{fontFamily:"monospace"}}>{e.reason}</text>
        </g>);
      })}

      {/* Standalone nodes (tables not in groups, db_refs) */}
      {standaloneNodes.map(n=>{const p=getNodePos(n);
        const isSel=selectedNodeId===n.id;
        const isDb=n.kind==="db_ref";
        // For tables, use table_type color; otherwise default color
        let color=NODE_COLORS[n.kind]||"#888", icon="📋", typeLabel="table";
        if(n.kind==="table"){
          const tbl=(tables||[]).find(t=>t.id===n.ref_id);
          const tType=tbl?.table_type||"data";
          color=TABLE_TYPE_COLORS[tType]||"#f97316";
          icon=TABLE_TYPE_ICONS[tType]||"📋";
          typeLabel=tType;
        }
        const sw=isSel?3:relStart?.id===n.id?3:1.5;const sc=isSel?"#fbbf24":relStart?.id===n.id?"#fbbf24":color;
        return(<g key={n.id} transform={`translate(${p.x},${p.y})`}
          onMouseDown={e=>onNodeMouseDown(e,n)} onClick={e=>onNodeClickHandler(e,n)}
          onDoubleClick={e=>onNodeDblClickHandler(e,n)}
          onMouseEnter={()=>onNodeMouseEnter(n)} onMouseLeave={onNodeMouseLeave}
          onContextMenu={e=>{e.preventDefault();if(onNodeRightClick)onNodeRightClick(e,n);}}
          style={{cursor:drag?.id===n.id?"grabbing":relDrag?"crosshair":"pointer"}}>
          {isDb?<>
            {/* DB cylinder — simple clean shape */}
            <path d={`M 0,12 L 0,${NH-4} Q 0,${NH+8} ${NW/2},${NH+8} Q ${NW},${NH+8} ${NW},${NH-4} L ${NW},12`} fill={color+"55"} stroke={sc} strokeWidth={sw+0.5}/>
            <ellipse cx={NW/2} cy={12} rx={NW/2} ry={12} fill={color+"80"} stroke={sc} strokeWidth={sw+0.5}/>
            <text x={NW/2} y={38} fill="#fff" fontSize={10} fontWeight={700} textAnchor="middle" style={{textShadow:"0 1px 2px rgba(0,0,0,0.5)"}}>{(n.name||"?").slice(0,18)}</text>
            <text x={NW/2} y={NH+2} fill={color} fontSize={8} textAnchor="middle" fontWeight={600}>{n.source_type||"데이터베이스"}</text>
          </>:<>
            {/* Regular table/node box */}
            <rect width={NW} height={NH} rx={8} fill={color+"55"} stroke={sc} strokeWidth={sw+0.5}/>
            <text x={12} y={20} fill={color} fontSize={13}>{icon}</text>
            <text x={34} y={20} fill="#fff" fontSize={11} fontWeight={700} style={{textShadow:"0 1px 2px rgba(0,0,0,0.5)"}}>{(n.name||"?").slice(0,16)}</text>
            <text x={34} y={36} fill="rgba(255,255,255,0.85)" fontSize={9}>{typeLabel}</text>
          </>}
        </g>);
      })}
      {/* Live relation preview line */}
      {relDrag && (()=>{
        const fromP=getNodePos(relDrag.fromNode);
        const fx=fromP.x+NW/2, fy=fromP.y+NH/2;
        return(<g pointerEvents="none">
          <line x1={fx} y1={fy} x2={relDrag.mx} y2={relDrag.my} stroke="var(--accent)" strokeWidth={2.5} strokeDasharray="6,4" opacity={0.8}/>
          <circle cx={relDrag.mx} cy={relDrag.my} r={hoverNode&&hoverNode.id!==relDrag.fromNode.id?8:4} fill="var(--accent)" opacity={hoverNode?0.9:0.6}/>
        </g>);
      })()}
      </g>
    </svg>
  </div>);
}

// ─── Table Editor ──────────────────────────────────────
function TableEditor({table,groups,onSave,onDelete,onClose,user}){
  // v8.7.2: 기본 columns 는 빈 배열 — 과거엔 [{name:"",type:"string"}] 이 들어가
  // "이름없음" 유령 컬럼이 먼저 생기는 UX 버그가 있었다.
  const[form,setForm]=useState({id:"",name:"",display_name:"",group_id:"",table_type:"data",columns:[],rows:[],description:"",aws_cmd:"",validation:{enabled:false,sort:{column:"",order:""},columns:{}},...(table&&Object.keys(table).length?table:{})});
  const[versions,setVersions]=useState([]);
  const[previewVer,setPreviewVer]=useState(null); // v8.4.5: 현재 미리보기 중인 버전 이름
  const[showValidation,setShowValidation]=useState(false);
  const[saveErrors,setSaveErrors]=useState([]); // v8.7.2: 저장 실패 시 서버/클라 검증 오류
  const isAdmin=user?.role==="admin";
  const u=(k,v)=>setForm(f=>({...f,[k]:v}));
  useEffect(()=>{if(form.id)sf(API+"/versions/"+form.id).then(d=>setVersions(d.versions||[])).catch(()=>{});},[form.id]);
  // If group_id, lock columns (inherit)
  const groupCols=form.group_id?(groups.find(g=>g.id===form.group_id)?.columns||[]):null;
  const effectiveCols=groupCols||form.columns;

  const addRow=()=>{const empty={};effectiveCols.forEach(c=>empty[c.name]="");u("rows",[...(form.rows||[]),empty]);};
  const delRow=(i)=>u("rows",form.rows.filter((_,j)=>j!==i));
  const updateCell=(i,col,val)=>{const rows=[...(form.rows||[])];rows[i]={...rows[i],[col]:val};u("rows",rows);};

  const addCol=()=>u("columns",[...(form.columns||[]),{name:"",type:"string"}]);
  const updateCol=(i,k,v)=>{const cols=[...(form.columns||[])];cols[i]={...cols[i],[k]:v};u("columns",cols);};
  const delCol=(i)=>u("columns",(form.columns||[]).filter((_,j)=>j!==i));

  // ── v8.7.2: Cell selection & copy ───────────────────────────────
  // selection = {r1,c1,r2,c2} (inclusive); anchor stored on mousedown.
  const[selection,setSelection]=useState(null);
  const dragAnchor=useRef(null);
  const inSel=(r,c)=>{
    if(!selection)return false;
    const r1=Math.min(selection.r1,selection.r2), r2=Math.max(selection.r1,selection.r2);
    const c1=Math.min(selection.c1,selection.c2), c2=Math.max(selection.c1,selection.c2);
    return r>=r1&&r<=r2&&c>=c1&&c<=c2;
  };
  const startSel=(r,c,shift)=>{
    if(shift&&selection){setSelection(s=>({...s,r2:r,c2:c}));}
    else{dragAnchor.current={r,c};setSelection({r1:r,c1:c,r2:r,c2:c});}
  };
  const extendSel=(r,c)=>{
    if(dragAnchor.current==null)return;
    const a=dragAnchor.current;
    setSelection({r1:a.r,c1:a.c,r2:r,c2:c});
  };
  useEffect(()=>{
    const up=()=>{dragAnchor.current=null;};
    const key=(e)=>{
      if(!selection)return;
      // Ctrl/Cmd+C while cells are selected
      if((e.ctrlKey||e.metaKey)&&(e.key==="c"||e.key==="C")){
        const r1=Math.min(selection.r1,selection.r2), r2=Math.max(selection.r1,selection.r2);
        const c1=Math.min(selection.c1,selection.c2), c2=Math.max(selection.c1,selection.c2);
        const lines=[];
        for(let r=r1;r<=r2;r++){
          const parts=[];
          for(let c=c1;c<=c2;c++){
            const col=effectiveCols[c]; if(!col){parts.push("");continue;}
            parts.push((form.rows?.[r]?.[col.name])??"");
          }
          lines.push(parts.join("\t"));
        }
        const tsv=lines.join("\n");
        try{navigator.clipboard.writeText(tsv);}catch{
          const ta=document.createElement("textarea");ta.value=tsv;document.body.appendChild(ta);ta.select();document.execCommand("copy");ta.remove();
        }
        // Small visual hint
        const sel=r2-r1+1,selC=c2-c1+1;
        const tip=document.createElement("div");
        tip.textContent=`✔ 복사됨 (${sel}행 × ${selC}열)`;
        tip.style.cssText="position:fixed;top:20px;left:50%;transform:translateX(-50%);background:#10b981;color:#fff;padding:8px 16px;border-radius:6px;z-index:99999;font-size:12px;font-weight:600;box-shadow:0 4px 12px rgba(0,0,0,0.4)";
        document.body.appendChild(tip);setTimeout(()=>tip.remove(),1400);
        e.preventDefault();
      }
    };
    window.addEventListener("mouseup",up);
    window.addEventListener("keydown",key);
    return()=>{window.removeEventListener("mouseup",up);window.removeEventListener("keydown",key);};
  },[selection,effectiveCols,form.rows]);

  // ── v8.7.2: Client-side validation before save ─────────────────
  const runValidation=()=>{
    const v=form.validation||{};
    if(!v.enabled)return [];
    const colsCfg=v.columns||{};
    const errs=[];
    const rx={};
    Object.entries(colsCfg).forEach(([cn,rule])=>{
      if(rule?.regex){try{rx[cn]=new RegExp(rule.regex);}catch(e){errs.push(`컬럼 '${cn}' 정규식 오류: ${e.message}`);}}
    });
    if(errs.length)return errs;
    (form.rows||[]).forEach((row,i)=>{
      const rn=i+1;
      Object.entries(colsCfg).forEach(([cn,rule])=>{
        const val=(row?.[cn]??"")+""; const sv=val.trim();
        if(rule.required&&sv===""){errs.push(`${rn}행 · '${cn}': 필수 값이 비어있습니다.`);return;}
        if(sv==="")return;
        const enumVals=(rule.enum||[]).filter(Boolean);
        if(enumVals.length&&!enumVals.map(String).includes(sv))errs.push(`${rn}행 · '${cn}': 허용되지 않은 값 '${sv}' (허용: ${enumVals.join(", ")}).`);
        if(rx[cn]&&!rx[cn].test(sv))errs.push(`${rn}행 · '${cn}': 정규식 '${rule.regex}' 불일치 ('${sv}').`);
      });
    });
    return errs.slice(0,50);
  };

  const doSave=()=>{
    setSaveErrors([]);
    if(!form.name.trim()){alert("이름을 입력하세요");return;}
    // Strip blank-name columns before send (UI ghost column guard)
    const cleanCols=(form.columns||[]).filter(c=>(c.name||"").trim());
    const errs=runValidation();
    if(errs.length){setSaveErrors(errs);return;}
    const payload={...form,columns:cleanCols,username:user?.username||""};
    Promise.resolve(onSave(payload)).catch((e)=>{
      // Server-side validation detail
      const msgs=e?.detail?.messages||e?.data?.detail?.messages||e?.messages;
      if(Array.isArray(msgs))setSaveErrors(msgs);
      else if(e?.message)setSaveErrors([e.message]);
    });
  };
  const loadVersion=(v)=>{
    sf(API+"/version-content?table_id="+form.id+"&version="+v)
      .then(d=>{
        // v8.4.5: 버전 스냅샷의 전체 필드 (name/description/columns/rows) 반영. audit 은 제외.
        setForm(f=>({
          ...f,
          name:d.name||f.name,
          display_name:d.display_name||"",
          description:d.description||"",
          columns:d.columns||f.columns,
          rows:d.rows||[],
          table_type:d.table_type||f.table_type,
          aws_cmd:d.aws_cmd||f.aws_cmd||"",
        }));
        setPreviewVer(v);
      })
      .catch(e=>alert(e?.message||"미리보기 로드 실패"));
  };
  const clearPreview=()=>{
    if(!form.id)return;
    sf(API+"/tables/"+form.id).then(d=>{setForm(d);setPreviewVer(null);}).catch(()=>setPreviewVer(null));
  };
  const rollbackTo=(v)=>{if(!confirm(`${v} 로 롤백하시겠습니까? (현재 상태는 rollback 직전 snapshot 으로 보존됩니다)`))return;
    sf(API+"/versions/rollback",{method:"POST",headers:{"Content-Type":"application/json"},body:JSON.stringify({table_id:form.id,version:v,username:user?.username||""})})
      .then(()=>{sf(API+"/tables/"+form.id).then(d=>setForm(d));sf(API+"/versions/"+form.id).then(d=>setVersions(d.versions||[]));}).catch(e=>alert(e.message));};

  const S={width:"100%",padding:"6px 10px",borderRadius:5,border:"1px solid var(--border)",background:"var(--bg-primary)",color:"var(--text-primary)",fontSize:12,outline:"none"};

  return(<div className="tm-overlay" onClick={onClose}>
    <div onClick={e=>e.stopPropagation()} className="tm-modal" style={{width:"90%",maxWidth:900,maxHeight:"90vh",overflow:"auto"}}>
      <div style={{display:"flex",justifyContent:"space-between",alignItems:"center",marginBottom:12}}>
        <div style={{fontSize:16,fontWeight:700,display:"flex",alignItems:"center",gap:10,flexWrap:"wrap"}}>
          <span>{form.id?"테이블 편집":"새 테이블"}</span>
          {form.name&&<span style={{fontSize:10,color:"#a3a3a3",fontWeight:400,fontFamily:"monospace"}}>→ Base/{(form.name.replace(/[^a-zA-Z0-9_-]/g,"_")||"table")}.csv</span>}
          {previewVer&&<span style={{fontSize:11,padding:"2px 10px",borderRadius:12,background:"rgba(59,130,246,0.15)",color:"#3b82f6",fontWeight:700,fontFamily:"monospace"}}>👁 미리보기: {previewVer}</span>}
          {previewVer&&<span onClick={clearPreview} style={{fontSize:10,cursor:"pointer",color:"var(--text-secondary)",textDecoration:"underline"}}>원본 복구</span>}
        </div>
        <span onClick={onClose} style={{cursor:"pointer",fontSize:18}}>✕</span>
      </div>
      <div style={{display:"flex",gap:8,marginBottom:8}}>
        <div style={{flex:2}}><div style={{fontSize:11,color:"var(--text-secondary)"}}>이름 <span style={{color:"#a3a3a3",fontWeight:400}}>(파일명 기준)</span></div><input value={form.name} onChange={e=>u("name",e.target.value)} style={S} placeholder="matching_step"/></div>
        <div style={{flex:2}}><div style={{fontSize:11,color:"var(--text-secondary)"}}>표시 라벨 <span style={{color:"#a3a3a3",fontWeight:400}}>(선택, 그래프에 표시)</span></div><input value={form.display_name||""} onChange={e=>u("display_name",e.target.value)} style={S} placeholder="예: 공정 매칭 테이블"/></div>
        <div style={{flex:1}}><div style={{fontSize:11,color:"var(--text-secondary)"}}>유형</div>
          <select value={form.table_type||"data"} onChange={e=>u("table_type",e.target.value)} style={S}>
            <option value="data">데이터</option>
            <option value="matching">매칭</option>
            <option value="rulebook">룰북</option>
          </select></div>
        <div style={{flex:2}}><div style={{fontSize:11,color:"var(--text-secondary)"}}>그룹 (선택)</div>
          <select value={form.group_id||""} onChange={e=>u("group_id",e.target.value)} style={S}>
            <option value="">-- 없음 (단독) --</option>
            {groups.map(g=><option key={g.id} value={g.id}>{g.name}</option>)}
          </select></div>
      </div>
      <div style={{marginBottom:8}}><div style={{fontSize:11,color:"var(--text-secondary)"}}>설명</div>
        <textarea value={form.description||""} onChange={e=>u("description",e.target.value)} rows={2} style={{...S,resize:"vertical"}}/></div>

      {/* Columns */}
      <div style={{marginBottom:8}}>
        <div style={{display:"flex",justifyContent:"space-between",alignItems:"center"}}>
          <div style={{fontSize:12,fontWeight:600}}>컬럼 {groupCols?"(그룹에서 상속)":""} <span style={{fontSize:9,color:"var(--text-secondary)",fontWeight:400}}>· Tab=다음 필드</span></div>
          {!groupCols&&<button onClick={addCol} style={{padding:"3px 10px",borderRadius:4,border:"none",background:"var(--accent)",color:"#fff",fontSize:10,cursor:"pointer"}}>+ 컬럼 추가</button>}
        </div>
        {/* v8.7.2: Tab navigation across column name/type/desc inputs */}
        {effectiveCols.map((c,i)=>{
          const colKD=(field)=>(e)=>{
            if(e.key!=="Tab")return;
            const order=["name","type","desc"];
            const idx=order.indexOf(field);
            let nf=e.shiftKey?idx-1:idx+1, nr=i;
            if(nf>=order.length){nf=0;nr=i+1;}
            else if(nf<0){nf=order.length-1;nr=i-1;}
            if(nr<0||nr>=effectiveCols.length)return;
            const sel=document.querySelector(`[data-tmcol="${nr}-${order[nf]}"]`);
            if(sel){e.preventDefault();sel.focus();sel.select?.();}
          };
          return(<div key={i} style={{display:"flex",gap:6,marginTop:4}}>
            <input value={c.name} data-tmcol={`${i}-name`} onChange={e=>updateCol(i,"name",e.target.value)} onKeyDown={colKD("name")} disabled={!!groupCols} placeholder="컬럼명" style={{...S,flex:2}}/>
            <select value={c.type} data-tmcol={`${i}-type`} onChange={e=>updateCol(i,"type",e.target.value)} onKeyDown={colKD("type")} disabled={!!groupCols} style={{...S,flex:1}}>
              <option value="string">string</option><option value="int">int</option><option value="float">float</option><option value="bool">bool</option>
            </select>
            <input value={c.desc||""} data-tmcol={`${i}-desc`} onChange={e=>updateCol(i,"desc",e.target.value)} onKeyDown={colKD("desc")} disabled={!!groupCols} placeholder="설명" style={{...S,flex:2}}/>
            {!groupCols&&<span onClick={()=>delCol(i)} style={{padding:"4px 8px",color:"#ef4444",cursor:"pointer"}}>✕</span>}
          </div>);
        })}
        {effectiveCols.length===0&&!groupCols&&<div style={{marginTop:6,padding:10,fontSize:11,color:"var(--text-secondary)",textAlign:"center",background:"var(--bg-tertiary)",borderRadius:4,border:"1px dashed var(--border)"}}>컬럼이 없습니다. <b>+ 컬럼 추가</b> 를 클릭하세요.</div>}
      </div>

      {/* v8.7.2: Validation / Sort rules */}
      {!groupCols&&<div style={{marginBottom:8,border:"1px solid var(--border)",borderRadius:6,background:"var(--bg-card)"}}>
        <div onClick={()=>setShowValidation(s=>!s)} style={{padding:"6px 10px",cursor:"pointer",display:"flex",alignItems:"center",gap:6,fontSize:12,fontWeight:600,userSelect:"none"}}>
          <span style={{fontSize:10,color:"var(--text-secondary)"}}>{showValidation?"▼":"▶"}</span>
          <span>🛡 검증 & 정렬 규칙</span>
          {form.validation?.enabled&&<span style={{fontSize:9,padding:"1px 6px",borderRadius:10,background:"rgba(16,185,129,0.15)",color:"#10b981",fontWeight:700}}>ENABLED</span>}
          <label style={{marginLeft:"auto",display:"flex",alignItems:"center",gap:4,fontSize:10,color:"var(--text-secondary)",fontWeight:400}} onClick={e=>e.stopPropagation()}>
            <input type="checkbox" checked={!!form.validation?.enabled} onChange={e=>u("validation",{...(form.validation||{}),enabled:e.target.checked})}/>
            켜기
          </label>
        </div>
        {showValidation&&<div style={{padding:"4px 10px 10px",borderTop:"1px solid var(--border)"}}>
          {/* Sort */}
          <div style={{display:"flex",gap:6,alignItems:"center",marginTop:8}}>
            <div style={{fontSize:10,color:"var(--text-secondary)",minWidth:70}}>정렬 기준</div>
            <select value={form.validation?.sort?.column||""} onChange={e=>u("validation",{...(form.validation||{}),sort:{...(form.validation?.sort||{}),column:e.target.value}})} style={{...S,flex:1}}>
              <option value="">-- 정렬 없음 --</option>
              {effectiveCols.filter(c=>c.name).map(c=><option key={c.name} value={c.name}>{c.name}</option>)}
            </select>
            <select value={form.validation?.sort?.order||""} onChange={e=>u("validation",{...(form.validation||{}),sort:{...(form.validation?.sort||{}),order:e.target.value}})} style={{...S,flex:1}}>
              <option value="">-- 방향 --</option>
              <option value="asc">오름차순 (숫자/사전)</option>
              <option value="desc">내림차순 (숫자/사전)</option>
              <option value="natural_asc">자연정렬 오름차순 (A1, A2, A10)</option>
              <option value="natural_desc">자연정렬 내림차순</option>
            </select>
          </div>
          {/* Per-column rules */}
          <div style={{fontSize:10,color:"var(--text-secondary)",marginTop:10,marginBottom:4}}>컬럼별 제약 <span style={{fontSize:9}}>· 필수 / enum (콤마 구분) / 정규식</span></div>
          <div style={{maxHeight:180,overflow:"auto",border:"1px solid var(--border)",borderRadius:4}}>
            <table style={{width:"100%",borderCollapse:"collapse",fontSize:11}}>
              <thead><tr>
                {["컬럼","필수","허용값 (enum)","정규식"].map(h=><th key={h} style={{textAlign:"left",padding:"4px 6px",background:"var(--bg-tertiary)",borderBottom:"1px solid var(--border)",fontSize:10}}>{h}</th>)}
              </tr></thead>
              <tbody>
                {effectiveCols.filter(c=>c.name).map(c=>{
                  const rule=form.validation?.columns?.[c.name]||{};
                  const setRule=(k,v)=>u("validation",{...(form.validation||{}),columns:{...(form.validation?.columns||{}),[c.name]:{...rule,[k]:v}}});
                  return(<tr key={c.name}>
                    <td style={{padding:"3px 6px",borderBottom:"1px solid var(--border)",fontFamily:"monospace",fontWeight:600}}>{c.name}</td>
                    <td style={{padding:"3px 6px",borderBottom:"1px solid var(--border)",textAlign:"center"}}>
                      <input type="checkbox" checked={!!rule.required} onChange={e=>setRule("required",e.target.checked)}/>
                    </td>
                    <td style={{padding:"3px 6px",borderBottom:"1px solid var(--border)"}}>
                      <input value={(rule.enum||[]).join(", ")} onChange={e=>setRule("enum",e.target.value.split(",").map(s=>s.trim()).filter(Boolean))} placeholder="예: PASS, FAIL, HOLD" style={{...S,padding:"3px 6px",fontSize:10}}/>
                    </td>
                    <td style={{padding:"3px 6px",borderBottom:"1px solid var(--border)"}}>
                      <input value={rule.regex||""} onChange={e=>setRule("regex",e.target.value)} placeholder="예: ^[A-Z]{2}\\d+$" style={{...S,padding:"3px 6px",fontSize:10,fontFamily:"monospace"}}/>
                    </td>
                  </tr>);
                })}
                {effectiveCols.filter(c=>c.name).length===0&&<tr><td colSpan={4} style={{padding:12,textAlign:"center",color:"var(--text-secondary)",fontSize:10}}>컬럼을 먼저 정의하세요.</td></tr>}
              </tbody>
            </table>
          </div>
          <div style={{fontSize:10,color:"var(--text-secondary)",marginTop:6,lineHeight:1.5}}>
            저장 시 <b style={{color:"var(--accent)"}}>검증 → 정렬</b> 순서로 적용됩니다. 검증 실패 시 저장 차단 + 오류 메시지 노출.
          </div>
        </div>}
      </div>}
      {/* v8.7.2: Save errors */}
      {saveErrors.length>0&&<div style={{marginBottom:8,padding:"8px 10px",background:"rgba(239,68,68,0.08)",border:"1px solid #ef4444",borderRadius:6,fontSize:11,color:"#ef4444",maxHeight:140,overflow:"auto"}}>
        <div style={{fontWeight:700,marginBottom:4}}>⚠ 검증 실패 — 저장되지 않았습니다 ({saveErrors.length}건)</div>
        {saveErrors.slice(0,20).map((m,i)=><div key={i} style={{fontFamily:"monospace",fontSize:10}}>• {m}</div>)}
        {saveErrors.length>20&&<div style={{fontSize:10}}>... 외 {saveErrors.length-20}건</div>}
      </div>}

      {/* Rows */}
      <div style={{marginBottom:8}}>
        <div style={{display:"flex",justifyContent:"space-between",alignItems:"center",marginBottom:6}}>
          <div style={{fontSize:12,fontWeight:600}}>데이터 ({form.rows?.length||0} 행) <span style={{fontSize:9,color:"var(--text-secondary)",fontWeight:400}}>· Tab=다음 컬럼, Enter=다음 행</span></div>
          <div style={{display:"flex",gap:6,alignItems:"center"}}>
            <span style={{fontSize:9,color:"var(--text-secondary)"}}>엑셀 붙여넣기 지원</span>
            <button onClick={addRow} style={{padding:"3px 10px",borderRadius:4,border:"none",background:"var(--accent)",color:"#fff",fontSize:10,cursor:"pointer"}}>+ 행 추가</button>
          </div>
        </div>
        <div style={{maxHeight:320,overflow:"auto",border:"1px solid var(--border)",borderRadius:6,background:"#fff"}}
          onPaste={(e)=>{
            const text=e.clipboardData?.getData("text/plain");
            if(!text||!text.includes("\t"))return;
            e.preventDefault();
            const lines=text.trim().split("\n").filter(l=>l.trim());
            if(!lines.length)return;
            const colNames=effectiveCols.map(c=>c.name);
            const newRows=lines.map(line=>{
              const parts=line.split("\t");
              const row={};
              colNames.forEach((cn,ci)=>{row[cn]=(parts[ci]||"").trim();});
              return row;
            });
            u("rows",[...(form.rows||[]),...newRows]);
          }}>
          <table style={{width:"100%",borderCollapse:"separate",borderSpacing:0,fontSize:12,fontFamily:"'Segoe UI',Arial,sans-serif",tableLayout:"fixed"}}>
            <thead>
              <tr>
                <th style={{position:"sticky",top:0,left:0,zIndex:3,width:44,minWidth:44,padding:"6px 4px",background:"#e5e7eb",color:"#374151",fontSize:10,fontWeight:700,border:"1px solid #9ca3af",textAlign:"center"}}>#</th>
                {effectiveCols.map(c=><th key={c.name} style={{position:"sticky",top:0,zIndex:2,minWidth:120,padding:"6px 10px",background:"#e5e7eb",color:"#111827",fontSize:11,fontWeight:700,border:"1px solid #9ca3af",textAlign:"left",whiteSpace:"nowrap",overflow:"hidden",textOverflow:"ellipsis"}}>{c.name||<span style={{color:"#9ca3af"}}>(이름 없음)</span>}</th>)}
                <th style={{position:"sticky",top:0,zIndex:2,width:32,minWidth:32,background:"#e5e7eb",border:"1px solid #9ca3af"}}></th>
              </tr>
            </thead>
            <tbody>
              {(form.rows||[]).map((r,i)=>(
                <tr key={i}>
                  <td style={{position:"sticky",left:0,zIndex:1,width:44,minWidth:44,padding:"0 4px",background:"#f3f4f6",color:"#6b7280",fontSize:10,fontWeight:600,border:"1px solid #d1d5db",textAlign:"center",cursor:"pointer",userSelect:"none"}}
                    onMouseDown={(e)=>{
                      // Row-number click selects the full row; drag extends.
                      e.preventDefault();
                      startSel(i,0,e.shiftKey);
                      setSelection(s=>({r1:e.shiftKey&&s?s.r1:i,c1:0,r2:i,c2:effectiveCols.length-1}));
                      dragAnchor.current={r:i,c:0,rowMode:true};
                    }}
                    onMouseEnter={()=>{if(dragAnchor.current?.rowMode){setSelection(s=>({...(s||{r1:dragAnchor.current.r,c1:0}),r2:i,c2:effectiveCols.length-1}));}}}
                  >{i+1}</td>
                  {effectiveCols.map((c,ci)=>{
                    const selected=inSel(i,ci);
                    return(<td key={c.name||ci}
                      onMouseDown={(e)=>{
                        if(e.shiftKey){e.preventDefault();startSel(i,ci,true);}
                        else{startSel(i,ci,false);}
                      }}
                      onMouseEnter={()=>{if(dragAnchor.current&&!dragAnchor.current.rowMode)extendSel(i,ci);}}
                      style={{padding:0,border:"1px solid #d1d5db",background:selected?"#dbeafe":(i%2===0?"#fff":"#f9fafb"),boxShadow:selected?"inset 0 0 0 1px #3b82f6":"none",position:"relative"}}>
                      <input
                        value={r[c.name]||""}
                        data-r={i} data-c={ci}
                        onChange={e=>updateCell(i,c.name,e.target.value)}
                        onKeyDown={e=>{
                          const total=effectiveCols.length;
                          if(e.key==="Tab"){
                            e.preventDefault();
                            const nc=e.shiftKey?ci-1:ci+1;
                            let nr=i, nci=nc;
                            if(nc>=total){nr=i+1;nci=0;}
                            else if(nc<0){nr=i-1;nci=total-1;}
                            if(nr<0)return;
                            if(nr>=(form.rows||[]).length){addRow();}
                            setTimeout(()=>{
                              const nxt=document.querySelector(`input[data-r="${nr}"][data-c="${nci}"]`);
                              if(nxt){nxt.focus();nxt.select?.();}
                            },nr>=(form.rows||[]).length?10:0);
                          } else if(e.key==="Enter"){
                            e.preventDefault();
                            if(i+1>=(form.rows||[]).length){addRow();}
                            setTimeout(()=>{
                              const nxt=document.querySelector(`input[data-r="${i+1}"][data-c="${ci}"]`);
                              if(nxt){nxt.focus();nxt.select?.();}
                            },10);
                          }
                        }}
                        onFocus={e=>{e.target.style.outline="2px solid #f97316";e.target.style.outlineOffset="-2px";e.target.style.background="#fff7ed";setSelection({r1:i,c1:ci,r2:i,c2:ci});}}
                        onBlur={e=>{e.target.style.outline="none";e.target.style.background="transparent";}}
                        style={{width:"100%",padding:"5px 8px",border:"none",background:"transparent",color:"#111827",fontSize:12,fontWeight:500,outline:"none",fontFamily:"'Consolas','Courier New',monospace",boxSizing:"border-box"}}
                      />
                    </td>);
                  })}
                  <td style={{width:32,minWidth:32,padding:"2px 4px",textAlign:"center",border:"1px solid #d1d5db",background:i%2===0?"#fff":"#f9fafb"}}>
                    <span onClick={()=>delRow(i)} title="행 삭제" style={{cursor:"pointer",color:"#ef4444",fontSize:12,fontWeight:700}}>✕</span>
                  </td>
                </tr>
              ))}
              {/* v8.7.2: 인라인 + 행 추가 버튼 */}
              {effectiveCols.length>0&&<tr>
                <td colSpan={effectiveCols.length+2} style={{padding:0,border:"1px dashed #d1d5db",background:"#f9fafb"}}>
                  <div onClick={addRow} title="행 추가" style={{cursor:"pointer",padding:"6px",textAlign:"center",color:"#6b7280",fontSize:11,fontWeight:600,userSelect:"none",transition:"background 0.15s"}}
                    onMouseEnter={e=>{e.currentTarget.style.background="#e0f2fe";e.currentTarget.style.color="#3b82f6";}}
                    onMouseLeave={e=>{e.currentTarget.style.background="transparent";e.currentTarget.style.color="#6b7280";}}
                  >＋ 행 추가</div>
                </td>
              </tr>}
              {(form.rows||[]).length===0&&effectiveCols.length===0&&(
                <tr><td colSpan={2} style={{padding:"24px",textAlign:"center",color:"#9ca3af",fontSize:11,background:"#fff",border:"1px solid #d1d5db"}}>컬럼을 먼저 정의한 뒤 행을 추가하세요.</td></tr>
              )}
            </tbody>
          </table>
        </div>
      </div>

      {/* AWS */}
      <div style={{marginBottom:8}}><div style={{fontSize:11,color:"var(--text-secondary)"}}>AWS S3 동기화 명령 ({"{{file}}"} 는 임시 CSV 경로)</div>
        <input value={form.aws_cmd||""} onChange={e=>u("aws_cmd",e.target.value)} style={{...S,fontFamily:"monospace",fontSize:10}}
          placeholder="aws s3 cp {{file}} s3://bucket/path/file.csv --endpoint-url https://..."/>
      </div>

      {/* Versions */}
      {versions.length>0&&<div style={{marginBottom:8}}>
        <div style={{fontSize:11,color:"var(--text-secondary)",marginBottom:4}}>버전 이력 ({versions.length}/30)</div>
        <div style={{display:"flex",flexDirection:"column",gap:2,maxHeight:160,overflow:"auto",border:"1px solid var(--border)",borderRadius:4,padding:4}}>{versions.map(v=>(
          <div key={v.name} style={{display:"flex",alignItems:"center",gap:6,padding:"3px 6px",fontSize:10,background:"var(--bg-card)",borderRadius:3}}>
            <span style={{fontFamily:"monospace",fontWeight:700,color:"var(--accent)",minWidth:40}}>{v.name}</span>
            <span style={{fontFamily:"monospace",color:"var(--text-secondary)"}}>{(v.updated||"").replace("T"," ").slice(0,16)}</span>
            <span style={{fontFamily:"monospace",color:v.action==="pre-rollback"?"#a855f7":"#64748b",fontSize:9}}>[{v.action||"edit"}]</span>
            {v.user&&<span style={{fontFamily:"monospace",color:"#10b981",fontSize:9}}>by {v.user}</span>}
            <span style={{color:"var(--text-secondary)",fontSize:9}}>{v.rows}r × {v.cols}c</span>
            <span onClick={()=>loadVersion(v.name)} style={{marginLeft:"auto",padding:"1px 8px",borderRadius:3,background:"var(--bg-hover)",cursor:"pointer",fontSize:10}}>미리보기</span>
            <span onClick={()=>rollbackTo(v.name)} style={{padding:"1px 8px",borderRadius:3,background:"#ef444422",color:"#ef4444",cursor:"pointer",fontSize:10,fontWeight:600}}>롤백</span>
          </div>))}</div>
      </div>}

      <div style={{display:"flex",gap:8,marginTop:12}}>
        <button onClick={doSave} style={{padding:"8px 20px",borderRadius:6,border:"none",background:"var(--accent)",color:"#fff",fontWeight:600,cursor:"pointer"}}>저장</button>
        {form.id&&isAdmin&&<button onClick={()=>{if(confirm("삭제할까요? (데이터는 아카이브됩니다)"))onDelete(form.id);}} style={{padding:"8px 16px",borderRadius:6,border:"1px solid #ef4444",background:"transparent",color:"#ef4444",cursor:"pointer"}}>삭제</button>}
        <button onClick={onClose} style={{padding:"8px 16px",borderRadius:6,border:"1px solid var(--border)",background:"transparent",color:"var(--text-secondary)",cursor:"pointer"}}>닫기</button>
      </div>
    </div></div>);
}

// ─── Group Editor ──────────────────────────────────────
function GroupEditor({group,onSave,onClose}){
  const[form,setForm]=useState({id:"",name:"",columns:[{name:"",type:"string"}],description:"",...(group&&Object.keys(group).length?group:{})});
  const u=(k,v)=>setForm({...form,[k]:v});
  const addCol=()=>u("columns",[...form.columns,{name:"",type:"string"}]);
  const updateCol=(i,k,v)=>{const cols=[...form.columns];cols[i]={...cols[i],[k]:v};u("columns",cols);};
  const delCol=(i)=>u("columns",form.columns.filter((_,j)=>j!==i));
  const S={width:"100%",padding:"6px 10px",borderRadius:5,border:"1px solid var(--border)",background:"var(--bg-primary)",color:"var(--text-primary)",fontSize:12,outline:"none"};
  return(<div className="tm-overlay" onClick={onClose}>
    <div onClick={e=>e.stopPropagation()} className="tm-modal" style={{width:"90%",maxWidth:600}}>
      <div style={{display:"flex",justifyContent:"space-between",alignItems:"center",marginBottom:12}}>
        <div style={{fontSize:16,fontWeight:700}}>{form.id?"그룹 편집":"새 테이블 그룹"}</div>
        <span onClick={onClose} style={{cursor:"pointer",fontSize:18}}>✕</span>
      </div>
      <div style={{marginBottom:8}}><div style={{fontSize:11,color:"var(--text-secondary)"}}>그룹명 (예: TABLE_ET)</div>
        <input value={form.name} onChange={e=>u("name",e.target.value)} style={S} placeholder="예: TABLE_ET"/></div>
      <div style={{marginBottom:8}}><div style={{fontSize:11,color:"var(--text-secondary)"}}>설명</div>
        <textarea value={form.description||""} onChange={e=>u("description",e.target.value)} rows={2} style={{...S,resize:"vertical"}}/></div>
      <div style={{marginBottom:8}}>
        <div style={{display:"flex",justifyContent:"space-between",alignItems:"center"}}>
          <div style={{fontSize:12,fontWeight:600}}>공유 컬럼 (모든 멤버 테이블이 상속)</div>
          <button onClick={addCol} style={{padding:"3px 10px",borderRadius:4,border:"none",background:"var(--accent)",color:"#fff",fontSize:10,cursor:"pointer"}}>+ 컬럼 추가</button>
        </div>
        {form.columns.map((c,i)=>(<div key={i} style={{display:"flex",gap:6,marginTop:4}}>
          <input value={c.name} onChange={e=>updateCol(i,"name",e.target.value)} placeholder="컬럼명" style={{...S,flex:2}}/>
          <select value={c.type} onChange={e=>updateCol(i,"type",e.target.value)} style={{...S,flex:1}}>
            <option value="string">string</option><option value="int">int</option><option value="float">float</option><option value="bool">bool</option>
          </select>
          <input value={c.desc||""} onChange={e=>updateCol(i,"desc",e.target.value)} placeholder="설명" style={{...S,flex:2}}/>
          <span onClick={()=>delCol(i)} style={{padding:"4px 8px",color:"#ef4444",cursor:"pointer"}}>✕</span>
        </div>))}
      </div>
      <div style={{display:"flex",gap:8,marginTop:12}}>
        <button onClick={()=>onSave(form)} style={{padding:"8px 20px",borderRadius:6,border:"none",background:"var(--accent)",color:"#fff",fontWeight:600,cursor:"pointer"}}>저장</button>
        <button onClick={onClose} style={{padding:"8px 16px",borderRadius:6,border:"1px solid var(--border)",background:"transparent",color:"var(--text-secondary)",cursor:"pointer"}}>취소</button>
      </div>
    </div></div>);
}

// ─── Main component ─────────────────────────────────────
export default function My_TableMap({user}){
  const[config,setConfig]=useState({nodes:[],relations:[]});
  const[tables,setTables]=useState([]);const[groups,setGroups]=useState([]);
  const[dbSources,setDbSources]=useState([]);
  const[editingTable,setEditingTable]=useState(null);const[editingGroup,setEditingGroup]=useState(null);
  const[pickingDb,setPickingDb]=useState(false);
  const[showImport,setShowImport]=useState(false);
  const[importSrcs,setImportSrcs]=useState([]);
  const[importForm,setImportForm]=useState({source:"",name:"",display_name:"",rows_limit:1000});
  useEffect(()=>{if(showImport)sf("/api/dashboard/products").then(d=>setImportSrcs(d.products||[])).catch(()=>{});},[showImport]);
  const doImport=()=>{
    const src=importSrcs.find(s=>s.label===importForm.source);if(!src){alert("소스를 선택하세요");return;}
    const name=(importForm.name||"").trim()||(src.file?src.file.split(".")[0]:src.product||"imported");
    sf("/api/dbmap/tables/import",{method:"POST",headers:{"Content-Type":"application/json"},body:JSON.stringify({
      source_type:src.source_type,file:src.file||"",root:src.root||"",product:src.product||"",
      name,display_name:importForm.display_name,rows_limit:importForm.rows_limit||1000,username:user?.username||"",
    })}).then(r=>{alert(`임포트 완료 — ${r.id} (CSV: ${r.csv_path?.split(/[\\\/]/).pop()||""})`);setShowImport(false);setImportForm({source:"",name:"",display_name:"",rows_limit:1000});load();}).catch(e=>alert(e.message));
  };
  const[view,setView]=useState("graph");
  const[editingRelation,setEditingRelation]=useState(null);
  const isAdmin=user?.role==="admin";
  // v8.6.3: lineage overlay (ET/INLINE/EDS → ML_TABLE)
  const[showLineage,setShowLineage]=useState(false);
  const[lineageData,setLineageData]=useState({edges:[],stats:{}});
  const loadLineage=()=>sf(API+"/lineage").then(d=>setLineageData(d||{edges:[],stats:{}})).catch(()=>setLineageData({edges:[],stats:{}}));
  useEffect(()=>{if(showLineage)loadLineage();},[showLineage]);

  const loadAll=()=>{
    sf(API+"/config").then(setConfig).catch(()=>{});
    sf(API+"/tables").then(d=>setTables(d.tables||[])).catch(()=>{});
    sf(API+"/groups").then(d=>setGroups(d.groups||[])).catch(()=>{});
    sf(API+"/db-sources").then(d=>setDbSources(d.sources||[])).catch(()=>{});
    if(showLineage)loadLineage();
  };
  useEffect(loadAll,[]);

  const saveTable=(payload)=>sf(API+"/tables/save",{method:"POST",headers:{"Content-Type":"application/json"},body:JSON.stringify(payload)}).then(r=>{const msgs=[];if(r.csv_path&&!r.csv_path.startsWith("CSV write"))msgs.push("📄 "+r.csv_path.split("/").pop()+" saved to DB root (see File Browser → Root Parquets)");else if(r.csv_path)msgs.push("⚠ "+r.csv_path);if(r.aws_result)msgs.push("AWS: "+r.aws_result);if(msgs.length)alert(msgs.join("\n"));setEditingTable(null);loadAll();}).catch(e=>{
    // v8.7.2: bubble up validation errors to TableEditor instead of alerting.
    const msg=String(e?.message||"저장 실패");
    if(msg.startsWith("VALIDATION_FAILED")){
      const messages=msg.split("\n").slice(1).filter(Boolean);
      const err=new Error("validation failed");err.messages=messages;throw err;
    }
    alert(msg);throw e;
  });
  const deleteTable=(id)=>sf(API+"/tables/delete?table_id="+id,{method:"POST"}).then(()=>{setEditingTable(null);loadAll();}).catch(e=>alert(e.message));
  const saveGroup=(payload)=>sf(API+"/groups/save",{method:"POST",headers:{"Content-Type":"application/json"},body:JSON.stringify(payload)}).then(()=>{setEditingGroup(null);loadAll();}).catch(e=>alert(e.message));
  const deleteGroup=(id)=>{if(!confirm("그룹을 삭제할까요? (아카이브됨)"))return;sf(API+"/groups/delete?group_id="+id,{method:"POST"}).then(loadAll);};
  const addDbRef=(src)=>sf(API+"/db-ref/add",{method:"POST",headers:{"Content-Type":"application/json"},body:JSON.stringify(src)}).then(()=>{setPickingDb(false);loadAll();});
  const deleteDbRef=(nid)=>{if(!confirm("맵에서 DB 참조 제거? (실제 DB 는 영향 없음)"))return;sf(API+"/db-ref/delete?node_id="+nid,{method:"POST"}).then(loadAll);};
  const savePosition=(id,x,y)=>sf(API+"/node/position",{method:"POST",headers:{"Content-Type":"application/json"},body:JSON.stringify({node_id:id,x,y})}).then(loadAll);
  const[selectedNode,setSelectedNode]=useState(null);
  const[dbInfo,setDbInfo]=useState(null); // DB ref detail modal
  const[dbDesc,setDbDesc]=useState("");
  const onNodeClick=(node)=>{
    // Single click = select only (highlight)
    setSelectedNode(node);
  };
  const onNodeDblClick=(node)=>{
    // Double click = open editor
    if(node.kind==="table"){sf(API+"/tables/"+node.ref_id).then(d=>setEditingTable(d)).catch(()=>{});}
    else if(node.kind==="group"){sf(API+"/groups/"+node.ref_id).then(d=>setEditingGroup(d)).catch(()=>{});}
    else if(node.kind==="db_ref"){
      setSelectedNode(node);
      sf(API+"/db-ref/info?node_id="+node.id).then(d=>{setDbInfo(d);setDbDesc(d.description||"");}).catch(()=>{});
    }
  };
  const saveDbDesc=()=>{if(!dbInfo)return;sf(API+"/db-ref/description",{method:"POST",headers:{"Content-Type":"application/json"},body:JSON.stringify({node_id:dbInfo.node_id,description:dbDesc})}).then(()=>{setDbInfo(null);setSelectedNode(null);loadAll();}).catch(e=>alert(e.message));};
  // Delete key removes DB ref from map (not actual DB)
  useEffect(()=>{
    const h=(e)=>{if(e.key==="Delete"&&selectedNode?.kind==="db_ref"&&isAdmin){
      if(confirm(`맵에서 "${selectedNode.name}" 제거? (DB 데이터는 삭제되지 않습니다)`)){deleteDbRef(selectedNode.id);setSelectedNode(null);}
    }};
    window.addEventListener("keydown",h);return()=>window.removeEventListener("keydown",h);
  },[selectedNode,isAdmin]);
  const[editRel,setEditRel]=useState(null);const[relForm,setRelForm]=useState({from_col:"",to_col:"",description:""});
  const onAddRelation=(from,to)=>{
    setEditRel({id:"",from_id:from.id,to_id:to.id,from_name:from.name,to_name:to.name});
    setRelForm({from_col:"",to_col:"",description:""});
  };
  const onEditRelation=(r)=>{
    const fromN=config.nodes.find(n=>n.id===r.from);const toN=config.nodes.find(n=>n.id===r.to);
    setEditRel({id:r.id,from_id:r.from,to_id:r.to,from_name:fromN?.name||"?",to_name:toN?.name||"?"});
    setRelForm({from_col:r.from_col||"",to_col:r.to_col||"",description:r.description||""});
  };
  const saveRelation=()=>{
    sf(API+"/relations/save",{method:"POST",headers:{"Content-Type":"application/json"},body:JSON.stringify({id:editRel.id||"",from_id:editRel.from_id,to_id:editRel.to_id,...relForm})}).then(()=>{setEditRel(null);loadAll();}).catch(e=>alert(e.message));
  };
  const delRelation=(rid)=>sf(API+"/relations/delete?relation_id="+rid,{method:"POST"}).then(loadAll);

  return(<div style={{padding:"20px 28px",background:"var(--bg-primary)",minHeight:"calc(100vh - 48px)",color:"var(--text-primary)"}}>
    <div style={{display:"flex",justifyContent:"space-between",alignItems:"center",marginBottom:12,flexWrap:"wrap",gap:8}}>
      <div style={{fontSize:16,fontWeight:700,fontFamily:"monospace",color:"var(--accent)",display:"flex",alignItems:"center",gap:10}}>
        <span>{">"} table map</span>
        <S3StatusLight compact />
      </div>
      <div style={{display:"flex",gap:4,alignItems:"center"}}>
        {[["graph","그래프"],["manage","관리"]].map(([k,l])=>(
          <span key={k} onClick={()=>setView(k)} style={{padding:"4px 12px",borderRadius:4,fontSize:11,cursor:"pointer",fontWeight:view===k?600:400,background:view===k?"var(--accent-glow)":"transparent",color:view===k?"var(--accent)":"var(--text-secondary)"}}>{l}</span>))}
        {view==="graph"&&<span onClick={()=>setShowLineage(s=>!s)} title="ET/INLINE/EDS → ML_TABLE 데이터 흐름 추론 (cyan dashed)" style={{padding:"4px 12px",borderRadius:4,fontSize:11,cursor:"pointer",fontWeight:showLineage?600:400,background:showLineage?"rgba(6,182,212,0.18)":"transparent",color:showLineage?"#06b6d4":"var(--text-secondary)",border:showLineage?"1px solid #06b6d4":"1px solid transparent",marginLeft:6}}>🔄 계보{showLineage&&lineageData.stats?` (${lineageData.stats.inferred||0})`:""}</span>}
      </div>
      {isAdmin&&<div style={{display:"flex",gap:6}}>
        <button onClick={()=>setEditingTable({})} style={{padding:"4px 12px",borderRadius:4,border:"none",background:"var(--accent)",color:"#fff",fontSize:11,cursor:"pointer"}}>+ 테이블</button>
        <button onClick={()=>setShowImport(true)} style={{padding:"4px 12px",borderRadius:4,border:"none",background:"#10b981",color:"#fff",fontSize:11,cursor:"pointer"}} title="기존 Base/DB 데이터를 TableMap 에 불러오기">↓ 임포트</button>
        <button onClick={()=>setEditingGroup({})} style={{padding:"4px 12px",borderRadius:4,border:"none",background:"#a855f7",color:"#fff",fontSize:11,cursor:"pointer"}}>+ 그룹</button>
        <button onClick={()=>setPickingDb(true)} style={{padding:"4px 12px",borderRadius:4,border:"none",background:"#3b82f6",color:"#fff",fontSize:11,cursor:"pointer"}}>+ DB 참조</button>
        <button onClick={()=>{const name=prompt("더미 DB 이름 (예: WIP/PRODUCT_A):");if(name){addDbRef({kind:"db_ref",source_type:"dummy",name:name,root:name.split("/")[0]||name,product:name.split("/")[1]||""});}}} style={{padding:"4px 12px",borderRadius:4,border:"1px solid #3b82f6",background:"transparent",color:"#3b82f6",fontSize:11,cursor:"pointer"}}>+ 더미 DB</button>
      </div>}
    </div>

    {view==="graph"&&<GraphView config={config} groups={groups} tables={tables} onNodeClick={onNodeClick} onNodeDblClick={onNodeDblClick} onAddRelation={onAddRelation} onSavePosition={savePosition}
      selectedNodeId={selectedNode?.id}
      onEditRelation={onEditRelation}
      lineageEdges={lineageData.edges} showLineage={showLineage}
      onNodeRightClick={(e,node)=>{if(node.kind==="db_ref"&&isAdmin){if(confirm(`DB 참조 "${node.name}" 를 제거할까요?`)){deleteDbRef(node.id);setSelectedNode(null);}}}}/>}
    {view==="graph"&&showLineage&&<div style={{marginTop:8,padding:"6px 10px",background:"rgba(6,182,212,0.08)",border:"1px solid rgba(6,182,212,0.3)",borderRadius:6,fontSize:11,color:"var(--text-secondary)",fontFamily:"monospace"}}>
      🔄 계보 — declared {lineageData.stats?.declared||0} · inferred {lineageData.stats?.inferred||0} · ML 타겟 {lineageData.stats?.ml_targets||0} · 소스 {lineageData.stats?.sources||0}. 추론된 흐름은 cyan 점선으로 표시 (ET/INLINE/EDS/KNOB/MASK/FAB/VM 노드 → ML_TABLE_PROD* 노드).
    </div>}
    {/* DB ref detail modal */}
    {dbInfo&&<div className="tm-overlay" onClick={()=>{setDbInfo(null);setSelectedNode(null);}}>
      <div onClick={e=>e.stopPropagation()} className="tm-modal" style={{width:"90%",maxWidth:600,maxHeight:"85vh",overflow:"auto"}}>
        <div style={{display:"flex",justifyContent:"space-between",alignItems:"center",marginBottom:14}}>
          <div style={{fontSize:16,fontWeight:700,color:"#3b82f6"}}>🗄️ {dbInfo.name}</div>
          <span onClick={()=>{setDbInfo(null);setSelectedNode(null);}} style={{cursor:"pointer",fontSize:18}}>✕</span>
        </div>
        {/* Info grid */}
        <div style={{display:"grid",gridTemplateColumns:"110px 1fr",gap:"6px 12px",fontSize:12,marginBottom:14}}>
          <span style={{color:"var(--text-secondary)",fontWeight:600}}>구조</span>
          <span style={{fontFamily:"monospace",color:"var(--accent)"}}>{dbInfo.structure}</span>
          <span style={{color:"var(--text-secondary)",fontWeight:600}}>소스 유형</span>
          <span>{dbInfo.source_type}</span>
          {dbInfo.root&&<><span style={{color:"var(--text-secondary)",fontWeight:600}}>Root</span><span style={{fontFamily:"monospace"}}>{dbInfo.root}</span></>}
          {dbInfo.product&&<><span style={{color:"var(--text-secondary)",fontWeight:600}}>Product</span><span style={{fontFamily:"monospace"}}>{dbInfo.product}</span></>}
          <span style={{color:"var(--text-secondary)",fontWeight:600}}>파일</span>
          <span>{dbInfo.file_count} 개</span>
        </div>
        {/* Columns */}
        {dbInfo.columns?.length>0&&<div style={{marginBottom:14}}>
          <div style={{fontSize:12,fontWeight:600,marginBottom:6}}>컬럼 ({dbInfo.columns.length})</div>
          <div style={{maxHeight:180,overflow:"auto",border:"1px solid var(--border)",borderRadius:6}}>
            <table style={{width:"100%",borderCollapse:"collapse",fontSize:11}}>
              <thead><tr><th style={{textAlign:"left",padding:"4px 10px",background:"var(--bg-tertiary)",borderBottom:"1px solid var(--border)",fontSize:10}}>컬럼</th><th style={{textAlign:"left",padding:"4px 10px",background:"var(--bg-tertiary)",borderBottom:"1px solid var(--border)",fontSize:10}}>유형</th></tr></thead>
              <tbody>{dbInfo.columns.map(c=><tr key={c}><td style={{padding:"3px 10px",borderBottom:"1px solid var(--border)",fontFamily:"monospace"}}>{c}</td><td style={{padding:"3px 10px",borderBottom:"1px solid var(--border)",color:"var(--text-secondary)",fontSize:10}}>{dbInfo.dtypes?.[c]||""}</td></tr>)}</tbody>
            </table>
          </div>
        </div>}
        {/* Description */}
        <div style={{marginBottom:14}}>
          <div style={{fontSize:12,fontWeight:600,marginBottom:4}}>설명</div>
          <textarea value={dbDesc} onChange={e=>setDbDesc(e.target.value)} rows={3}
            style={{width:"100%",padding:"8px 12px",borderRadius:6,border:"1px solid var(--border)",background:"var(--bg-primary)",color:"var(--text-primary)",fontSize:12,outline:"none",resize:"vertical",fontFamily:"monospace"}}
            placeholder="이 데이터베이스에 대한 메모 추가..."/>
        </div>
        <div style={{display:"flex",gap:8}}>
          <button onClick={saveDbDesc} style={{padding:"8px 20px",borderRadius:6,border:"none",background:"var(--accent)",color:"#fff",fontWeight:600,cursor:"pointer"}}>설명 저장</button>
          <button onClick={()=>{setDbInfo(null);setSelectedNode(null);}} style={{padding:"8px 16px",borderRadius:6,border:"1px solid var(--border)",background:"transparent",color:"var(--text-secondary)",cursor:"pointer"}}>닫기</button>
          {isAdmin&&<button onClick={()=>{if(confirm("맵에서 제거할까요? (DB 데이터는 영향 없음)")){sf(API+"/db-ref/delete?node_id="+dbInfo.node_id,{method:"POST"}).then(()=>{setDbInfo(null);setSelectedNode(null);loadAll();});}}} style={{marginLeft:"auto",padding:"8px 16px",borderRadius:6,border:"1px solid #ef4444",background:"transparent",color:"#ef4444",cursor:"pointer",fontSize:11}}>맵에서 해제</button>}
        </div>
      </div>
    </div>}

    {view==="manage"&&<div style={{display:"grid",gridTemplateColumns:"repeat(auto-fill,minmax(280px,1fr))",gap:12}}>
      {groups.map(g=>(<div key={g.id} style={{background:"var(--bg-secondary)",borderRadius:8,border:"1px solid #a855f7",padding:12}}>
        <div style={{fontSize:11,color:"#a855f7",fontWeight:700,marginBottom:4}}>📚 그룹</div>
        <div style={{fontSize:13,fontWeight:600,marginBottom:4}}>{g.name}</div>
        <div style={{fontSize:10,color:"var(--text-secondary)"}}>{g.tables?.length||0} 테이블 | {g.updated?.slice(0,10)}</div>
        <div style={{display:"flex",gap:4,marginTop:6}}>
          <span onClick={()=>sf(API+"/groups/"+g.id).then(d=>setEditingGroup(d)).catch(()=>{})} style={{color:"var(--accent)",cursor:"pointer",fontSize:11}}>편집</span>
          {isAdmin&&<span onClick={()=>deleteGroup(g.id)} style={{color:"#ef4444",cursor:"pointer",fontSize:11}}>삭제</span>}
        </div>
      </div>))}
      {tables.map(t=>(<div key={t.id} style={{background:"var(--bg-secondary)",borderRadius:8,border:"1px solid var(--accent)",padding:12}}>
        <div style={{fontSize:11,color:"var(--accent)",fontWeight:700,marginBottom:4}}>📋 테이블{t.group_id?" (그룹 내)":""}</div>
        <div style={{fontSize:13,fontWeight:600,marginBottom:4}}>{t.name}</div>
        <div style={{fontSize:10,color:"var(--text-secondary)"}}>{t.updated?.slice(0,10)} · <span style={{fontFamily:"monospace",color:"#f97316"}}>📄 {(t.name||t.id).replace(/[^a-zA-Z0-9_-]/g,"_")}.csv</span></div>
        <div style={{display:"flex",gap:4,marginTop:6}}>
          <span onClick={()=>sf(API+"/tables/"+t.id).then(d=>setEditingTable(d)).catch(()=>{})} style={{color:"var(--accent)",cursor:"pointer",fontSize:11}}>편집</span>
          {isAdmin&&<span onClick={()=>{if(confirm("삭제할까요? (아카이브됨)"))deleteTable(t.id);}} style={{color:"#ef4444",cursor:"pointer",fontSize:11}}>삭제</span>}
        </div>
      </div>))}
    </div>}

    {view==="relations"&&<div style={{background:"var(--bg-secondary)",borderRadius:8,border:"1px solid var(--border)",overflow:"auto"}}>
      <table style={{width:"100%",borderCollapse:"collapse",fontSize:12}}>
        <thead><tr>{["소스","타겟","소스 컬럼","타겟 컬럼","설명","작업"].map(h=><th key={h} style={{textAlign:"left",padding:"8px 12px",background:"var(--bg-tertiary)",color:"var(--text-secondary)",fontSize:11,borderBottom:"1px solid var(--border)"}}>{h}</th>)}</tr></thead>
        <tbody>{(config.relations||[]).map(r=>{const a=config.nodes.find(n=>n.id===r.from);const b=config.nodes.find(n=>n.id===r.to);return(
          <tr key={r.id}>
            <td style={{padding:"6px 12px",borderBottom:"1px solid var(--border)"}}>{a?.name||"?"}</td>
            <td style={{padding:"6px 12px",borderBottom:"1px solid var(--border)"}}>{b?.name||"?"}</td>
            <td style={{padding:"6px 12px",borderBottom:"1px solid var(--border)",fontFamily:"monospace",fontSize:10}}>{r.from_col||"-"}</td>
            <td style={{padding:"6px 12px",borderBottom:"1px solid var(--border)",fontFamily:"monospace",fontSize:10}}>{r.to_col||"-"}</td>
            <td style={{padding:"6px 12px",borderBottom:"1px solid var(--border)",fontSize:11,color:"var(--text-secondary)"}}>{r.description||""}</td>
            <td style={{padding:"6px 12px",borderBottom:"1px solid var(--border)"}}>{isAdmin&&<span onClick={()=>delRelation(r.id)} style={{color:"#ef4444",cursor:"pointer",fontSize:11}}>삭제</span>}</td>
          </tr>);})}</tbody>
      </table>
      {(config.relations||[]).length===0&&<div style={{padding:40,textAlign:"center",color:"var(--text-secondary)"}}>관계가 없습니다. 그래프 뷰에서 Shift+클릭으로 노드 A 를 선택한 뒤 노드 B 를 클릭하세요.</div>}
    </div>}

    {/* Relation editor modal */}
    {editRel&&<div className="tm-overlay" onClick={()=>setEditRel(null)}>
      <div onClick={e=>e.stopPropagation()} className="tm-modal" style={{width:420}}>
        <div style={{display:"flex",justifyContent:"space-between",alignItems:"center",marginBottom:14}}>
          <div style={{fontSize:15,fontWeight:700}}>{editRel.id?"관계 편집":"새 관계"}</div>
          <span onClick={()=>setEditRel(null)} style={{cursor:"pointer",fontSize:18}}>✕</span>
        </div>
        <div style={{fontSize:12,marginBottom:12,color:"var(--text-secondary)"}}><strong style={{color:"var(--accent)"}}>{editRel.from_name}</strong> → <strong style={{color:"var(--accent)"}}>{editRel.to_name}</strong></div>
        <div style={{marginBottom:8}}><div style={{fontSize:11,color:"var(--text-secondary)",marginBottom:3}}>소스 컬럼 <span style={{fontSize:9,color:"var(--text-secondary)"}}>(다중은 쉼표로 구분)</span></div>
          <input value={relForm.from_col} onChange={e=>setRelForm({...relForm,from_col:e.target.value})} placeholder="예: ROOT_LOT_ID, WAFER_ID" style={{width:"100%",padding:"8px 12px",borderRadius:5,border:"1px solid var(--border)",background:"var(--bg-primary)",color:"var(--text-primary)",fontSize:12,outline:"none",fontFamily:"monospace"}}/></div>
        <div style={{marginBottom:8}}><div style={{fontSize:11,color:"var(--text-secondary)",marginBottom:3}}>타겟 컬럼 <span style={{fontSize:9,color:"var(--text-secondary)"}}>(소스와 동일한 순서)</span></div>
          <input value={relForm.to_col} onChange={e=>setRelForm({...relForm,to_col:e.target.value})} placeholder="예: ROOT_LOT_ID, WAFER_ID" style={{width:"100%",padding:"8px 12px",borderRadius:5,border:"1px solid var(--border)",background:"var(--bg-primary)",color:"var(--text-primary)",fontSize:12,outline:"none",fontFamily:"monospace"}}/></div>
        <div style={{marginBottom:12}}><div style={{fontSize:11,color:"var(--text-secondary)",marginBottom:3}}>설명</div>
          <input value={relForm.description} onChange={e=>setRelForm({...relForm,description:e.target.value})} placeholder="예: Lot 이력 추적" style={{width:"100%",padding:"8px 12px",borderRadius:5,border:"1px solid var(--border)",background:"var(--bg-primary)",color:"var(--text-primary)",fontSize:12,outline:"none"}}/></div>
        <div style={{display:"flex",gap:8}}>
          <button onClick={saveRelation} style={{flex:1,padding:"8px",borderRadius:6,border:"none",background:"var(--accent)",color:"#fff",fontWeight:600,cursor:"pointer"}}>저장</button>
          {editRel.id&&<button onClick={()=>{delRelation(editRel.id);setEditRel(null);}} style={{padding:"8px 16px",borderRadius:6,border:"1px solid #ef4444",background:"transparent",color:"#ef4444",cursor:"pointer"}}>삭제</button>}
          <button onClick={()=>setEditRel(null)} style={{padding:"8px 16px",borderRadius:6,border:"1px solid var(--border)",background:"transparent",color:"var(--text-secondary)",cursor:"pointer"}}>취소</button>
        </div>
      </div>
    </div>}
    {editingTable&&<TableEditor table={editingTable} groups={groups} onSave={saveTable} onDelete={deleteTable} onClose={()=>setEditingTable(null)} user={user}/>}
    {editingGroup&&<GroupEditor group={editingGroup} onSave={saveGroup} onClose={()=>setEditingGroup(null)}/>}
    {showImport&&<div className="tm-overlay" onClick={()=>setShowImport(false)}>
      <div onClick={e=>e.stopPropagation()} className="tm-modal" style={{width:"90%",maxWidth:560}}>
        <div style={{display:"flex",justifyContent:"space-between",marginBottom:12}}>
          <div style={{fontSize:16,fontWeight:700}}>기존 데이터 임포트</div>
          <span onClick={()=>setShowImport(false)} style={{cursor:"pointer",fontSize:18}}>✕</span>
        </div>
        <div style={{fontSize:11,color:"var(--text-secondary)",marginBottom:12}}>Base/DB 의 parquet/csv 를 TableMap 테이블로 가져옵니다. 스키마 + 최대 rows 건.</div>
        <div style={{marginBottom:8}}>
          <div style={{fontSize:11,color:"var(--text-secondary)",marginBottom:4}}>소스</div>
          <select value={importForm.source} onChange={e=>setImportForm({...importForm,source:e.target.value})} style={{width:"100%",padding:"6px 10px",borderRadius:5,border:"1px solid var(--border)",background:"var(--bg-primary)",color:"var(--text-primary)",fontSize:12,fontFamily:"monospace"}}>
            <option value="">-- 선택 --</option>
            {importSrcs.map(s=><option key={s.label} value={s.label}>{s.label}</option>)}
          </select>
        </div>
        <div style={{display:"flex",gap:8,marginBottom:8}}>
          <div style={{flex:1}}><div style={{fontSize:11,color:"var(--text-secondary)"}}>테이블 이름 (선택)</div><input value={importForm.name} onChange={e=>setImportForm({...importForm,name:e.target.value})} placeholder="자동(파일명)" style={{width:"100%",padding:"6px 10px",borderRadius:5,border:"1px solid var(--border)",background:"var(--bg-primary)",color:"var(--text-primary)",fontSize:12}}/></div>
          <div style={{flex:1}}><div style={{fontSize:11,color:"var(--text-secondary)"}}>표시 라벨 (선택)</div><input value={importForm.display_name} onChange={e=>setImportForm({...importForm,display_name:e.target.value})} placeholder="그래프 라벨" style={{width:"100%",padding:"6px 10px",borderRadius:5,border:"1px solid var(--border)",background:"var(--bg-primary)",color:"var(--text-primary)",fontSize:12}}/></div>
        </div>
        <div style={{marginBottom:12}}>
          <div style={{fontSize:11,color:"var(--text-secondary)"}}>최대 행 수 (기본 1000)</div>
          <input type="number" value={importForm.rows_limit} onChange={e=>setImportForm({...importForm,rows_limit:parseInt(e.target.value)||1000})} style={{width:120,padding:"6px 10px",borderRadius:5,border:"1px solid var(--border)",background:"var(--bg-primary)",color:"var(--text-primary)",fontSize:12}}/>
        </div>
        <div style={{display:"flex",gap:8}}>
          <button onClick={doImport} style={{flex:1,padding:10,borderRadius:6,border:"none",background:"#10b981",color:"#fff",fontWeight:600,cursor:"pointer"}}>임포트</button>
          <button onClick={()=>setShowImport(false)} style={{padding:"10px 20px",borderRadius:6,border:"1px solid var(--border)",background:"transparent",color:"var(--text-secondary)",cursor:"pointer"}}>취소</button>
        </div>
      </div>
    </div>}
    {pickingDb&&<div className="tm-overlay" onClick={()=>setPickingDb(false)}>
      <div onClick={e=>e.stopPropagation()} className="tm-modal" style={{width:"80%",maxWidth:600,maxHeight:"80vh",overflow:"auto"}}>
        <div style={{display:"flex",justifyContent:"space-between",marginBottom:12}}>
          <div style={{fontSize:16,fontWeight:700}}>DB 참조 고정</div>
          <span onClick={()=>setPickingDb(false)} style={{cursor:"pointer",fontSize:18}}>✕</span>
        </div>
        <div style={{fontSize:11,color:"var(--text-secondary)",marginBottom:8}}>맵 노드로 추가할 DB 소스를 선택하세요 (참조만, 실제 데이터는 변경되지 않습니다)</div>
        {dbSources.map(s=><div key={s.label} onClick={()=>addDbRef(s)} style={{padding:"8px 12px",background:"var(--bg-card)",borderRadius:6,marginBottom:4,cursor:"pointer",fontSize:12,border:"1px solid var(--border)"}}>
          {s.label} <span style={{fontSize:10,color:"var(--accent)"}}>[{s.source_type}]</span>
        </div>)}
      </div></div>}
  </div>);
}
