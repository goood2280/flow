import dagre from "dagre";
import { useState, useEffect, useRef } from "react";
// v8.8.2: S3StatusLight 제거 — S3 상태는 File Browser 에서만 관리.
import { sf } from "../lib/api";
import { Pill } from "../components/UXKit";
const API="/api/dbmap";

const NODE_COLORS={table:"#f97316",group:"#a855f7",db_ref:"#3b82f6"};
// Table type colors (overrides default table color based on table_type)
const TABLE_TYPE_COLORS={data:"#f97316",matching:"#10b981",rulebook:"#eab308"};

function splitRelationCols(value){
  const text=String(value||"").trim();
  if(!text)return[];
  const parts=text.includes(",")||text.includes("\n")
    ? text.split(/[,\n]+/)
    : text.split(/\s+/);
  return parts.map(s=>s.trim()).filter(Boolean);
}

// Inject overlay styles at module load — hardcoded colors prevent any flash
if(typeof document!=="undefined"&&!document.getElementById("tm-styles")){
  const s=document.createElement("style");s.id="tm-styles";
  s.textContent=`
.tm-overlay{position:fixed;inset:0;background:rgba(0,0,0,0.8)!important;z-index:9999;display:flex;align-items:center;justify-content:center;padding:20px}
.tm-modal{background:var(--bg-secondary,var(--bg-primary,#fff))!important;border-radius:12px;padding:20px;border:1px solid var(--border,#d1d5db)!important;color:var(--text-primary,#111827)!important}
.tm-modal select option{background:var(--bg-primary,#fff);color:var(--text-primary,#111827)}
.tm-modal input::placeholder,.tm-modal textarea::placeholder{color:var(--text-secondary,#6b7280)}
.tm-modal h1,.tm-modal h2,.tm-modal h3,.tm-modal h4{color:var(--text-primary,#111827)}
`;
  document.head.appendChild(s);
}

// ─── Graph view with zoom/pan ───────────────────────────
function GraphView({config,groups,tables,onNodeClick,onNodeDblClick,onAddRelation,onSavePosition,onSaveRelationPosition,onNodeRightClick,selectedNodeId,selectedRelationId,onEditRelation,lineageEdges,showLineage,onDropIntoGroup,onMemberContext,canManage,onSetNodeColor}){
  const[drag,setDrag]=useState(null);const[relStart,setRelStart]=useState(null);
  const[relDrag,setRelDrag]=useState(null); // {fromNode, mx, my} for relation drag
  const[relLabelDrag,setRelLabelDrag]=useState(null);
  const[relationOffsets,setRelationOffsets]=useState({});
  const[hoverNode,setHoverNode]=useState(null);
  const[zoom,setZoom]=useState(1);const[pan,setPan]=useState({x:0,y:0});const[panning,setPanning]=useState(null);
  const svgRef=useRef();const containerRef=useRef();
  const relDragMoved=useRef(false);
  const NW=150,NH=50,PAD=16;

  const clampZoom=(v)=>Math.max(0.2,Math.min(2,v));
  const graphPoint=(e)=>{
    const rect=svgRef.current.getBoundingClientRect();
    return {x:(e.clientX-rect.left-pan.x)/zoom,y:(e.clientY-rect.top-pan.y)/zoom,rect};
  };
  const onBgMouseDown=(e)=>{
    const isPanSurface=e.target===svgRef.current||e.target?.dataset?.zoomBg==="1";
    if((e.button===0||e.button===1)&&!e.shiftKey&&isPanSurface){
      e.preventDefault();
      setPanning({startX:e.clientX-pan.x,startY:e.clientY-pan.y});
    }
  };
  const onWheel=(e)=>{
    e.preventDefault();
    const {x,y,rect}=graphPoint(e);
    const next=clampZoom(zoom*(e.deltaY<0?1.12:0.88));
    setZoom(next);
    setPan({x:e.clientX-rect.left-x*next,y:e.clientY-rect.top-y*next});
  };
  const onMouseMove=(e)=>{
    if(panning){setPan({x:e.clientX-panning.startX,y:e.clientY-panning.startY});return;}
    const {x:mx,y:my}=graphPoint(e);
    if(relLabelDrag){
      const dx=relLabelDrag.startDx+(mx-relLabelDrag.startX);
      const dy=relLabelDrag.startDy+(my-relLabelDrag.startY);
      if(Math.abs(mx-relLabelDrag.startX)>3||Math.abs(my-relLabelDrag.startY)>3)relDragMoved.current=true;
      setRelationOffsets(cur=>({...cur,[relLabelDrag.id]:{dx,dy}}));
      return;
    }
    if(relDrag){setRelDrag({...relDrag,mx,my});return;}
    if(!drag)return;
    setDrag({...drag,x:mx-drag.offsetX,y:my-drag.offsetY});
  };
  const onMouseUp=(e)=>{
    if(panning){setPanning(null);return;}
    if(relLabelDrag){
      const {x,y}=graphPoint(e);
      const off={dx:relLabelDrag.startDx+(x-relLabelDrag.startX),dy:relLabelDrag.startDy+(y-relLabelDrag.startY)};
      setRelationOffsets(cur=>({...cur,[relLabelDrag.id]:off}));
      if(relDragMoved.current&&onSaveRelationPosition)onSaveRelationPosition(relLabelDrag.id,off.dx,off.dy);
      setRelLabelDrag(null);return;
    }
    if(relDrag){
      // Check if released over a node
      if(hoverNode&&hoverNode.id!==relDrag.fromNode.id){onAddRelation(relDrag.fromNode,hoverNode);}
      setRelDrag(null);return;
    }
    if(drag){
      // v8.8.13: drop-into-group 판정 — 드래그 중인 node 가 table 이고 그룹에 없으면,
      //   drop 위치가 그룹 박스 내부일 때 onDropIntoGroup 호출 (공유 컬럼 흡수 편입).
      const dragged=config.nodes.find(n=>n.id===drag.id);
      let handled=false;
      if(dragged && dragged.kind==="table" && onDropIntoGroup){
        const draggedTable=(tables||[]).find(t=>t.id===dragged.ref_id);
        const alreadyInGroup=!!(draggedTable && draggedTable.group_id);
        if(!alreadyInGroup){
          // 드롭된 중심점 좌표
          const cx=drag.x+NW/2, cy=drag.y+NH/2;
          for(const gn of (config.nodes||[])){
            if(gn.kind!=="group") continue;
            const gp={x:gn.x||100,y:gn.y||100};
            const members=(tables||[]).filter(t=>t.group_id===gn.ref_id);
            const HEADER=28,INNER_PAD=12,COL_GAP=8;
            const cols=Math.min(members.length,3)||1;
            const rows_count=Math.ceil(members.length/cols)||1;
            const bw=Math.max(NW+INNER_PAD*2, cols*(NW+COL_GAP)-COL_GAP+INNER_PAD*2);
            const bh=HEADER+rows_count*(NH+COL_GAP)-COL_GAP+INNER_PAD*2;
            const inside=cx>=gp.x && cx<=gp.x+bw && cy>=gp.y && cy<=gp.y+(members.length?bh:70);
            if(inside){
              onDropIntoGroup(dragged, gn, draggedTable);
              handled=true;
              break;
            }
          }
        }
      }
      if(!handled) onSavePosition(drag.id,drag.x,drag.y);
      setDrag(null);
    }
  };
  const onNodeMouseDown=(e,node)=>{
    e.stopPropagation();
    const {x:mx,y:my}=graphPoint(e);
    const pos=getNodePos(node);
    // Ctrl/Cmd+drag = relation (start line from node)
    if(e.ctrlKey||e.metaKey){setRelDrag({fromNode:node,mx,my});return;}
    // Shift+click = legacy relation mode (kept for compatibility)
    if(e.shiftKey){setRelStart(node);return;}
    // Default = move
    setDrag({id:node.id,offsetX:mx-pos.x,offsetY:my-pos.y,x:pos.x,y:pos.y,startX:pos.x,startY:pos.y});
  };
  const onRelationMouseDown=(e,r,baseX,baseY)=>{
    e.stopPropagation();
    const {x,y}=graphPoint(e);
    const off=relationOffsets[r.id]||{dx:Number(r.label_dx||0),dy:Number(r.label_dy||0)};
    relDragMoved.current=false;
    setRelLabelDrag({id:r.id,startX:x,startY:y,startDx:off.dx,startDy:off.dy,baseX,baseY});
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

  const visibleGraphNodes=[...groupNodes,...standaloneNodes];
  const nodeType=(n)=>{
    if(n.kind==="group"||n.type==="group") return "group";
    if(n.kind==="db_ref"||n.kind==="db"||n.type==="db") return "db";
    return "table";
  };
  const nodeSize=(n)=>{
    if(n.kind==="group"){
      const grp=(groups||[]).find(g=>g.id===n.ref_id);
      const memberCount=grp?(groupMembers[grp.id]||[]).length:0;
      const cols=Math.min(memberCount,3)||1;
      const rows=Math.ceil(memberCount/cols)||1;
      const w=Math.max(NW+PAD*2,cols*(NW+8)-8+PAD*2);
      const h=memberCount?28+rows*(NH+8)-8+PAD*2:70;
      return {w,h};
    }
    return {w:NW,h:NH};
  };
  const autoNodeMap=(()=>{
    const g=new dagre.graphlib.Graph();
    g.setGraph({rankdir:"TB",nodesep:80,ranksep:120,marginx:40,marginy:40});
    g.setDefaultEdgeLabel(()=>({}));
    visibleGraphNodes.forEach(n=>{
      const s=nodeSize(n);
      g.setNode(n.id,{width:s.w,height:s.h});
    });
    const visibleIds=new Set(visibleGraphNodes.map(n=>n.id));
    (config.relations||[]).forEach(r=>{
      if(visibleIds.has(r.from)&&visibleIds.has(r.to)) g.setEdge(r.from,r.to);
    });
    try{dagre.layout(g);}catch(e){console.warn("[tablemap] dagre layout failed",e);}

    const orderedByType={group:[],table:[],db:[]};
    visibleGraphNodes.forEach(n=>{
      const s=nodeSize(n);
      const dn=g.node(n.id)||{};
      orderedByType[nodeType(n)].push({node:n,size:s,cx:Number.isFinite(dn.x)?dn.x:0});
    });
    Object.values(orderedByType).forEach(arr=>{
      arr.sort((a,b)=>a.cx-b.cx||String(a.node.name||"").localeCompare(String(b.node.name||"")));
    });

    const packRow=(items,y)=>{
      let x=40;
      const out={};
      items.forEach(({node,size})=>{
        out[node.id]={x,y};
        x+=size.w+80;
      });
      return out;
    };
    const groupH=Math.max(0,...orderedByType.group.map(x=>x.size.h));
    const tableH=Math.max(NH,...orderedByType.table.map(x=>x.size.h));
    const groupY=56;
    const tableY=orderedByType.group.length?groupY+groupH+120:56;
    const dbY=tableY+tableH+120;
    return {
      ...packRow(orderedByType.group,groupY),
      ...packRow(orderedByType.table,tableY),
      ...packRow(orderedByType.db,dbY),
    };
  })();
  const savedPos=(n)=>{
    const x=Number(n?.x),y=Number(n?.y);
    return Number.isFinite(x)&&Number.isFinite(y)?{x,y}:null;
  };
  const getNodePos=(n)=>drag&&drag.id===n.id?{x:drag.x,y:drag.y}:(autoNodeMap[n.id]||savedPos(n)||{x:100,y:100});
  const safeColor=(value,fallback)=>{
    const s=String(value||"").trim();
    return /^#[0-9a-fA-F]{6}$/.test(s)?s:fallback;
  };
  const resolveNodeColor=(node,fallback,refObj)=>safeColor(node?.color||refObj?.color,fallback);
  const textOnColor=(value)=>{
    const hex=safeColor(value,"#1f2937").slice(1);
    const r=parseInt(hex.slice(0,2),16),g=parseInt(hex.slice(2,4),16),b=parseInt(hex.slice(4,6),16);
    const lum=(0.2126*r+0.7152*g+0.0722*b)/255;
    return lum>0.58?"#111827":"#f9fafb";
  };
  const ColorPicker=({node,color,x,y})=>{
    if(!canManage||!onSetNodeColor||!node)return null;
    return(<foreignObject x={x} y={y} width={22} height={22}>
      <input xmlns="http://www.w3.org/1999/xhtml" type="color" value={safeColor(color,"#f97316")}
        title="노드 색상 지정"
        onMouseDown={e=>e.stopPropagation()}
        onClick={e=>e.stopPropagation()}
        onChange={e=>onSetNodeColor(node,e.target.value)}
        style={{width:20,height:20,padding:0,border:"1px solid rgba(255,255,255,0.35)",borderRadius:4,background:"transparent",cursor:"pointer",boxSizing:"border-box"}}/>
    </foreignObject>);
  };

  return(<div ref={containerRef} style={{position:"relative",width:"100%",height:"calc(100vh - 220px)",minHeight:620,background:"radial-gradient(circle at 1px 1px, rgba(148,163,184,0.16) 1px, transparent 0) 0 0/20px 20px, var(--bg-primary)",borderRadius:10,border:"1px solid var(--border)",overflow:"hidden"}}>
    <div style={{position:"absolute",top:8,left:8,fontSize:14,color:"var(--text-primary)",zIndex:2,background:"var(--bg-card)",padding:"8px 12px",borderRadius:6,border:"1px solid var(--border)",lineHeight:1.7,boxShadow:"0 2px 8px rgba(0,0,0,0.3)"}}>
      <div style={{fontSize:14,fontWeight:800,color:"#ef4444",marginBottom:4,letterSpacing:"0.05em"}}>GUIDE</div>
      <div><b style={{color:"var(--accent)"}}>더블클릭</b> → 테이블/그룹 편집</div>
      <div><b style={{color:"var(--accent)"}}>노드 드래그</b> → 위치 이동</div>
      <div><b style={{color:"var(--accent)"}}>배경 드래그</b> → 맵 이동</div>
      <div><b style={{color:"var(--accent)"}}>마우스 휠</b> → 확대/축소</div>
      <div><b style={{color:"var(--accent)"}}>Ctrl + 드래그</b> 다른 노드로 → 관계 생성</div>
      <div><b style={{color:"var(--accent)"}}>Relation 노드 클릭</b> → 연결 컬럼 테이블</div>
    </div>
    {relStart&&<div style={{position:"absolute",top:32,left:8,fontSize:14,color:"var(--accent)",zIndex:2,background:"var(--accent-glow)",padding:"4px 8px",borderRadius:4}}>
출발 <b>{relStart.name}</b> → 대상 클릭 (<span onClick={()=>setRelStart(null)} style={{cursor:"pointer",textDecoration:"underline"}}>취소</span>)
    </div>}
    {/* Zoom controls */}
    <div style={{position:"absolute",top:8,right:8,display:"flex",gap:4,zIndex:2}}>
      <span onClick={()=>setZoom(z=>clampZoom(z+0.15))} style={{width:28,height:28,display:"flex",alignItems:"center",justifyContent:"center",borderRadius:4,background:"var(--bg-card)",border:"1px solid var(--border)",cursor:"pointer",fontSize:14,fontWeight:700,color:"var(--text-primary)"}}>+</span>
      <span onClick={()=>setZoom(1)} style={{minWidth:36,height:28,display:"flex",alignItems:"center",justifyContent:"center",borderRadius:4,background:"var(--bg-card)",border:"1px solid var(--border)",cursor:"pointer",fontSize:14,fontWeight:600,color:"var(--text-secondary)",fontFamily:"monospace"}}>{Math.round(zoom*100)}%</span>
      <span onClick={()=>setZoom(z=>clampZoom(z-0.15))} style={{width:28,height:28,display:"flex",alignItems:"center",justifyContent:"center",borderRadius:4,background:"var(--bg-card)",border:"1px solid var(--border)",cursor:"pointer",fontSize:14,fontWeight:700,color:"var(--text-primary)"}}>-</span>
      <span onClick={()=>{setZoom(0.5);setPan({x:0,y:0});}} style={{height:28,display:"flex",alignItems:"center",justifyContent:"center",borderRadius:4,background:"var(--bg-card)",border:"1px solid var(--border)",cursor:"pointer",fontSize:14,padding:"0 8px",color:"var(--text-secondary)"}}>맞추기</span>
    </div>
    <div title="현재 확대 비율"
      style={{position:"absolute",right:12,top:52,width:42,height:118,zIndex:2,borderRadius:6,border:"1px solid var(--border)",background:"var(--bg-card)",display:"flex",flexDirection:"column",alignItems:"center",justifyContent:"center",gap:6,boxShadow:"0 2px 8px rgba(0,0,0,0.25)"}}>
      <span style={{fontSize:14,color:"var(--text-secondary)",fontWeight:700}}>WHEEL</span>
      <div style={{width:4,height:58,borderRadius:3,background:"linear-gradient(180deg,var(--accent),rgba(148,163,184,0.55))",position:"relative"}}>
        <span style={{position:"absolute",left:-7,top:`${Math.max(2,Math.min(50,58-((zoom-0.2)/(2-0.2))*58))}px`,width:18,height:6,borderRadius:3,background:"#fff",border:"1px solid var(--accent)"}}/>
      </div>
      <span style={{fontSize:14,color:"var(--text-secondary)",fontFamily:"monospace"}}>{Math.round(zoom*100)}%</span>
    </div>
    <svg ref={svgRef} width="100%" height="100%" onMouseDown={onBgMouseDown} onMouseMove={onMouseMove} onMouseUp={onMouseUp} onMouseLeave={onMouseUp} onWheel={onWheel} style={{cursor:panning?"grabbing":drag||relLabelDrag?"grabbing":"grab"}}>
      <g transform={`translate(${pan.x},${pan.y}) scale(${zoom})`}>
      <defs>
        <marker id="arrow" viewBox="0 0 10 10" refX="9" refY="5" markerWidth="8" markerHeight="8" orient="auto">
          <path d="M 0 0 L 10 5 L 0 10 z" fill="var(--accent)" opacity="0.8"/></marker>
        <marker id="arrow-lineage" viewBox="0 0 10 10" refX="9" refY="5" markerWidth="7" markerHeight="7" orient="auto">
          <path d="M 0 0 L 10 5 L 0 10 z" fill="#06b6d4" opacity="0.85"/></marker>
      </defs>

      <rect data-zoom-bg="1" x={0} y={0} width={1320} height={1320} fill="transparent" />

      {/* Group bounding boxes with member tables rendered inside */}
      {groupNodes.map(gn=>{
        const gp=getNodePos(gn);const grp=(groups||[]).find(g=>g.id===gn.ref_id);
        const groupColor=resolveNodeColor(gn,"#a855f7",grp);
        const members=grp?groupMembers[grp.id]||[]:[];
        const groupProducts=[...(Array.isArray(gn.products)?gn.products:[])].filter(p=>p&&String(p).toLowerCase()!=="common");
        const HEADER=28,INNER_PAD=12,COL_GAP=8;
        // Compute group box size based on member count
        const cols=Math.min(members.length,3)||1;
        const rows_count=Math.ceil(members.length/cols)||1;
        const bw=Math.max(NW+INNER_PAD*2, cols*(NW+COL_GAP)-COL_GAP+INNER_PAD*2);
        const bh=HEADER+rows_count*(NH+COL_GAP)-COL_GAP+INNER_PAD*2;

        return(<g key={gn.id}
          onMouseDown={e=>onNodeMouseDown(e,gn)} onClick={e=>onNodeClickHandler(e,gn)} onDoubleClick={e=>onNodeDblClickHandler(e,gn)}
          onMouseEnter={()=>onNodeMouseEnter(gn)} onMouseLeave={onNodeMouseLeave}
          style={{cursor:relDrag?"crosshair":"move"}}>
          {/* Group container */}
          <rect x={gp.x} y={gp.y} width={bw} height={members.length?bh:70} rx={12}
            fill={groupColor+"26"} stroke={groupColor} strokeWidth={2.5} strokeDasharray="6,4" opacity={0.95}
            />
          <text x={gp.x+INNER_PAD} y={gp.y+18} fill={groupColor} fontSize={11} fontWeight={700}>📚 {gn.name}</text>
          <text x={gp.x+bw-INNER_PAD-(canManage?26:0)} y={gp.y+18} fill={groupColor+"aa"} fontSize={9} textAnchor="end">{members.length} 테이블</text>
          <ColorPicker node={gn} color={groupColor} x={gp.x+bw-24} y={gp.y+4}/>
          {groupProducts.length>0&&(
            <g transform={`translate(${gp.x+bw-72},${gp.y+24})`}>
              <rect x={0} y={0} width={60} height={16} rx={8} fill={groupColor+"22"} stroke={groupColor+"66"}/>
              <text x={30} y={11} textAnchor="middle" fill={groupColor} fontSize={8} fontWeight={700} style={{fontFamily:"monospace"}}>{groupProducts[0]}</text>
            </g>
          )}

          {/* Member tables rendered inside the group box */}
          {members.map((m,mi)=>{
            const col=mi%cols, row=Math.floor(mi/cols);
            const mx=gp.x+INNER_PAD+col*(NW+COL_GAP);
            const my=gp.y+HEADER+INNER_PAD+row*(NH+COL_GAP);
            // Build a pseudo-node for the member table (they don't have standalone nodes in config)
            const memberNode=config.nodes.find(n=>n.kind==="table"&&n.ref_id===m.id)||{id:"member_"+m.id,kind:"table",ref_id:m.id,name:m.name};
            const isSel=selectedNodeId===memberNode.id;
            const tType=m.table_type||"data";
            const tColor=resolveNodeColor(memberNode,TABLE_TYPE_COLORS[tType]||"#f97316",m);
            return(<g key={m.id} transform={`translate(${mx},${my})`}
              onMouseDown={e=>{e.stopPropagation();}}
              onClick={e=>{e.stopPropagation();onNodeClick(memberNode);}}
              onDoubleClick={e=>{e.stopPropagation();if(onNodeDblClick)onNodeDblClick(memberNode);}}
              onContextMenu={e=>{e.preventDefault();e.stopPropagation();if(onMemberContext)onMemberContext(memberNode,m);}}
              style={{cursor:"pointer"}}>
              <rect width={NW} height={NH} rx={6} fill="var(--bg-card,var(--bg-secondary,#fff))" stroke={isSel?"#fbbf24":tColor} strokeWidth={isSel?2.3:1.3}/>
              <rect x={0} y={0} width={5} height={NH} rx={3} fill={tColor}/>
              <text x={14} y={20} fill="var(--text-primary,#111827)" fontSize={10.5} fontWeight={800}>{(m.display_name||m.name||"?").slice(0,17)}</text>
              <text x={14} y={36} fill="var(--text-secondary,#6b7280)" fontSize={8.5} fontWeight={700}>{tType} · {m.rows?.length||0}r · {m.columns?.length||0}c</text>
              <ColorPicker node={memberNode} color={tColor} x={NW-24} y={NH-23}/>
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
        const off=relationOffsets[r.id]||{dx:Number(r.label_dx||0),dy:Number(r.label_dy||0)};
        const labelMx=mx+off.dx,labelMy=my+off.dy;
        const fromCols=splitRelationCols(r.from_col);
        const toCols=splitRelationCols(r.to_col);
        const pairs=Math.max(fromCols.length,toCols.length);
        const pairRows=Array.from({length:pairs||1},(_,i)=>({from:fromCols[i]||"",to:toCols[i]||""}));
        const compactDesc=(r.description||"").trim();
        const pairCount=pairs||0;
        const relW=102,relH=34;
        const relX=-relW/2,relY=-relH/2;
        const isRelSel=selectedRelationId===r.id;
        return(<g key={r.id}>
          <line x1={ax1} y1={ay1} x2={bx1} y2={by1} stroke="var(--accent)" strokeWidth={1.8} strokeOpacity={0.55} markerEnd="url(#arrow)"/>
          <circle cx={labelMx} cy={labelMy} r={relH/2+5} fill="var(--bg-primary)" opacity={0.9}/>
          <g transform={`translate(${labelMx},${labelMy})`}
             onMouseDown={e=>onRelationMouseDown(e,r,mx,my)}
             onClick={e=>{e.stopPropagation();if(relDragMoved.current){relDragMoved.current=false;return;}if(onEditRelation)onEditRelation(r);}}
             style={{cursor:"grab"}}>
            <title>{`${a.name||r.from} -> ${b.name||r.to}\n${pairRows.map(p=>`${p.from||"-"} -> ${p.to||"-"}`).join("\n")}${compactDesc?`\n${compactDesc}`:""}`}</title>
            <rect x={relX-3} y={relY-3} width={relW+6} height={relH+6} rx={relH/2+3} fill="var(--accent)" opacity={isRelSel?0.2:0.08}/>
            <rect x={relX} y={relY} width={relW} height={relH} rx={relH/2} fill="var(--bg-card,var(--bg-secondary,#fff))" stroke={isRelSel?"#fbbf24":"var(--accent)"} strokeOpacity={0.9} strokeWidth={isRelSel?2.2:1.4}/>
            <text x={relX+16} y={relY+21} fill="var(--accent)" fontSize={10} fontWeight={900} style={{fontFamily:"monospace"}}>REL</text>
            <line x1={relX+43} y1={relY+8} x2={relX+43} y2={relY+26} stroke="var(--border)" strokeWidth={1}/>
            <text x={relX+55} y={relY+21} fill="var(--text-primary)" fontSize={10} fontWeight={900} style={{fontFamily:"monospace"}}>{pairCount}</text>
            <text x={relX+74} y={relY+21} fill="var(--text-secondary)" fontSize={8.5} fontWeight={800}>cols</text>
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
        const nodeProducts=[...(Array.isArray(n.products)?n.products:[]).concat(n.product?[n.product]:[])].filter(p=>p&&String(p).toLowerCase()!=="common");
        // For tables, use table_type color; otherwise default color
        let color=NODE_COLORS[n.kind]||"#888", typeLabel="table";
        if(n.kind==="table"){
          const tbl=(tables||[]).find(t=>t.id===n.ref_id);
          const tType=tbl?.table_type||"data";
          color=resolveNodeColor(n,TABLE_TYPE_COLORS[tType]||"#f97316",tbl);
          typeLabel=tType;
        }else{
          color=resolveNodeColor(n,color,null);
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
            <text x={NW/2} y={38} fill={textOnColor(color)} fontSize={10} fontWeight={700} textAnchor="middle" style={{textShadow:"0 1px 2px rgba(0,0,0,0.35)"}}>{(n.name||"?").slice(0,18)}</text>
            <text x={NW/2} y={NH+2} fill={color} fontSize={8} textAnchor="middle" fontWeight={600}>{n.source_type||"데이터베이스"}</text>
            <ColorPicker node={n} color={color} x={8} y={2}/>
            {nodeProducts[0]&&<>
              <rect x={NW-66} y={4} width={58} height={14} rx={7} fill="rgba(59,130,246,0.16)" stroke="rgba(59,130,246,0.35)"/>
              <text x={NW-37} y={14} textAnchor="middle" fill="var(--text-primary,#111827)" fontSize={8} fontWeight={700} style={{fontFamily:"monospace"}}>{nodeProducts[0]}</text>
            </>}
          </>:<>
            {/* Regular table/node box */}
            <rect width={NW} height={NH} rx={8} fill="var(--bg-card,var(--bg-secondary,#fff))" stroke={sc} strokeWidth={sw+0.5}/>
            <rect x={0} y={0} width={6} height={NH} rx={4} fill={color}/>
            <text x={16} y={20} fill="var(--text-primary,#111827)" fontSize={11} fontWeight={800}>{(n.name||"?").slice(0,17)}</text>
            <text x={16} y={36} fill="var(--text-secondary,#6b7280)" fontSize={9} fontWeight={700}>{typeLabel}</text>
            <ColorPicker node={n} color={color} x={NW-24} y={NH-23}/>
            {nodeProducts[0]&&<>
              <rect x={NW-62} y={6} width={54} height={14} rx={7} fill={color+"22"} stroke={color+"88"}/>
              <text x={NW-35} y={16} textAnchor="middle" fill="var(--text-primary,#111827)" fontSize={8} fontWeight={700} style={{fontFamily:"monospace"}}>{nodeProducts[0]}</text>
            </>}
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
        tip.style.cssText="position:fixed;top:20px;left:50%;transform:translateX(-50%);background:#10b981;color:#fff;padding:8px 16px;border-radius:6px;z-index:99999;font-size:14px;font-weight:600;box-shadow:0 4px 12px rgba(0,0,0,0.4)";
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

  const S={width:"100%",padding:"6px 10px",borderRadius:5,border:"1px solid var(--border)",background:"var(--bg-primary)",color:"var(--text-primary)",fontSize:14,outline:"none"};

  return(<div className="tm-overlay" onClick={onClose}>
    <div onClick={e=>e.stopPropagation()} className="tm-modal" style={{width:"90%",maxWidth:900,maxHeight:"90vh",overflow:"auto"}}>
      <div style={{display:"flex",justifyContent:"space-between",alignItems:"center",marginBottom:12}}>
        <div style={{fontSize:16,fontWeight:700,display:"flex",alignItems:"center",gap:10,flexWrap:"wrap"}}>
          <span>{form.id?"테이블 편집":"새 테이블"}</span>
          {form.name&&<span style={{fontSize:14,color:"#a3a3a3",fontWeight:400,fontFamily:"monospace"}}>→ Base/{(form.name.replace(/[^a-zA-Z0-9_-]/g,"_")||"table")}.csv</span>}
          {previewVer&&<span style={{fontSize:14,padding:"2px 10px",borderRadius:12,background:"rgba(59,130,246,0.15)",color:"#3b82f6",fontWeight:700,fontFamily:"monospace"}}>👁 미리보기: {previewVer}</span>}
          {previewVer&&<span onClick={clearPreview} style={{fontSize:14,cursor:"pointer",color:"var(--text-secondary)",textDecoration:"underline"}}>원본 복구</span>}
        </div>
        <span onClick={onClose} style={{cursor:"pointer",fontSize:18}}>✕</span>
      </div>
      <div style={{display:"flex",gap:8,marginBottom:8}}>
        <div style={{flex:2}}><div style={{fontSize:14,color:"var(--text-secondary)"}}>이름 <span style={{color:"#a3a3a3",fontWeight:400}}>(파일명 기준)</span></div><input value={form.name} onChange={e=>u("name",e.target.value)} style={S} placeholder="matching_step"/></div>
        <div style={{flex:2}}><div style={{fontSize:14,color:"var(--text-secondary)"}}>표시 라벨 <span style={{color:"#a3a3a3",fontWeight:400}}>(선택, 그래프에 표시)</span></div><input value={form.display_name||""} onChange={e=>u("display_name",e.target.value)} style={S} placeholder="예: 공정 매칭 테이블"/></div>
        <div style={{flex:1}}><div style={{fontSize:14,color:"var(--text-secondary)"}}>유형</div>
          <select value={form.table_type||"data"} onChange={e=>u("table_type",e.target.value)} style={S}>
            <option value="data">데이터</option>
            <option value="matching">매칭</option>
            <option value="rulebook">룰북</option>
          </select></div>
        <div style={{flex:2}}><div style={{fontSize:14,color:"var(--text-secondary)"}}>그룹 (선택)</div>
          <select value={form.group_id||""} onChange={e=>u("group_id",e.target.value)} style={S}>
            <option value="">-- 없음 (단독) --</option>
            {groups.map(g=><option key={g.id} value={g.id}>{g.name}</option>)}
          </select></div>
      </div>
      <div style={{marginBottom:8}}><div style={{fontSize:14,color:"var(--text-secondary)"}}>설명</div>
        <textarea value={form.description||""} onChange={e=>u("description",e.target.value)} rows={2} style={{...S,resize:"vertical"}}/></div>

      {/* Columns */}
      <div style={{marginBottom:8}}>
        <div style={{display:"flex",justifyContent:"space-between",alignItems:"center"}}>
          <div style={{fontSize:14,fontWeight:600}}>컬럼 {groupCols?"(그룹에서 상속)":""} <span style={{fontSize:14,color:"var(--text-secondary)",fontWeight:400}}>· Tab=다음 필드</span></div>
          {!groupCols&&<button onClick={addCol} style={{padding:"3px 10px",borderRadius:4,border:"none",background:"var(--accent)",color:"#fff",fontSize:14,cursor:"pointer"}}>+ 컬럼 추가</button>}
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
        {effectiveCols.length===0&&!groupCols&&<div style={{marginTop:6,padding:10,fontSize:14,color:"var(--text-secondary)",textAlign:"center",background:"var(--bg-tertiary)",borderRadius:4,border:"1px dashed var(--border)"}}>컬럼이 없습니다. <b>+ 컬럼 추가</b> 를 클릭하세요.</div>}
      </div>

      {/* v8.7.2: Validation / Sort rules */}
      {!groupCols&&<div style={{marginBottom:8,border:"1px solid var(--border)",borderRadius:6,background:"var(--bg-card)"}}>
        <div onClick={()=>setShowValidation(s=>!s)} style={{padding:"6px 10px",cursor:"pointer",display:"flex",alignItems:"center",gap:6,fontSize:14,fontWeight:600,userSelect:"none"}}>
          <span style={{fontSize:14,color:"var(--text-secondary)"}}>{showValidation?"▼":"▶"}</span>
          <span>🛡 검증 & 정렬 규칙</span>
          {form.validation?.enabled&&<span style={{fontSize:14,padding:"1px 6px",borderRadius:10,background:"rgba(16,185,129,0.15)",color:"#10b981",fontWeight:700}}>ENABLED</span>}
          <label style={{marginLeft:"auto",display:"flex",alignItems:"center",gap:4,fontSize:14,color:"var(--text-secondary)",fontWeight:400}} onClick={e=>e.stopPropagation()}>
            <input type="checkbox" checked={!!form.validation?.enabled} onChange={e=>u("validation",{...(form.validation||{}),enabled:e.target.checked})}/>
            켜기
          </label>
        </div>
        {showValidation&&<div style={{padding:"4px 10px 10px",borderTop:"1px solid var(--border)"}}>
          {/* Sort */}
          <div style={{display:"flex",gap:6,alignItems:"center",marginTop:8}}>
            <div style={{fontSize:14,color:"var(--text-secondary)",minWidth:70}}>정렬 기준</div>
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
          <div style={{fontSize:14,color:"var(--text-secondary)",marginTop:10,marginBottom:4}}>컬럼별 제약 <span style={{fontSize:14}}>· 필수 / enum (콤마 구분) / 정규식</span></div>
          <div style={{maxHeight:180,overflow:"auto",border:"1px solid var(--border)",borderRadius:4}}>
            <table style={{width:"100%",borderCollapse:"collapse",fontSize:14}}>
              <thead><tr>
                {["컬럼","필수","허용값 (enum)","정규식"].map(h=><th key={h} style={{textAlign:"left",padding:"4px 6px",background:"var(--bg-tertiary)",borderBottom:"1px solid var(--border)",fontSize:14}}>{h}</th>)}
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
                      <input value={(rule.enum||[]).join(", ")} onChange={e=>setRule("enum",e.target.value.split(",").map(s=>s.trim()).filter(Boolean))} placeholder="예: PASS, FAIL, HOLD" style={{...S,padding:"3px 6px",fontSize:14}}/>
                    </td>
                    <td style={{padding:"3px 6px",borderBottom:"1px solid var(--border)"}}>
                      <input value={rule.regex||""} onChange={e=>setRule("regex",e.target.value)} placeholder="예: ^[A-Z]{2}\\d+$" style={{...S,padding:"3px 6px",fontSize:14,fontFamily:"monospace"}}/>
                    </td>
                  </tr>);
                })}
                {effectiveCols.filter(c=>c.name).length===0&&<tr><td colSpan={4} style={{padding:12,textAlign:"center",color:"var(--text-secondary)",fontSize:14}}>컬럼을 먼저 정의하세요.</td></tr>}
              </tbody>
            </table>
          </div>
          <div style={{fontSize:14,color:"var(--text-secondary)",marginTop:6,lineHeight:1.5}}>
            저장 시 <b style={{color:"var(--accent)"}}>검증 → 정렬</b> 순서로 적용됩니다. 검증 실패 시 저장 차단 + 오류 메시지 노출.
          </div>
        </div>}
      </div>}
      {/* v8.7.2: Save errors */}
      {saveErrors.length>0&&<div style={{marginBottom:8,padding:"8px 10px",background:"rgba(239,68,68,0.08)",border:"1px solid #ef4444",borderRadius:6,fontSize:14,color:"#ef4444",maxHeight:140,overflow:"auto"}}>
        <div style={{fontWeight:700,marginBottom:4}}>⚠ 검증 실패 — 저장되지 않았습니다 ({saveErrors.length}건)</div>
        {saveErrors.slice(0,20).map((m,i)=><div key={i} style={{fontFamily:"monospace",fontSize:14}}>• {m}</div>)}
        {saveErrors.length>20&&<div style={{fontSize:14}}>... 외 {saveErrors.length-20}건</div>}
      </div>}

      {/* Rows */}
      <div style={{marginBottom:8}}>
        <div style={{display:"flex",justifyContent:"space-between",alignItems:"center",marginBottom:6}}>
          <div style={{fontSize:14,fontWeight:600}}>데이터 ({form.rows?.length||0} 행) <span style={{fontSize:14,color:"var(--text-secondary)",fontWeight:400}}>· Tab=다음 컬럼, Enter=다음 행</span></div>
          <div style={{display:"flex",gap:6,alignItems:"center"}}>
            <span style={{fontSize:14,color:"var(--text-secondary)"}}>엑셀 붙여넣기 지원</span>
            <button onClick={addRow} style={{padding:"3px 10px",borderRadius:4,border:"none",background:"var(--accent)",color:"#fff",fontSize:14,cursor:"pointer"}}>+ 행 추가</button>
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
          <table style={{width:"100%",borderCollapse:"separate",borderSpacing:0,fontSize:14,fontFamily:"'Segoe UI',Arial,sans-serif",tableLayout:"fixed"}}>
            <thead>
              <tr>
                <th style={{position:"sticky",top:0,left:0,zIndex:3,width:44,minWidth:44,padding:"6px 4px",background:"#e5e7eb",color:"#374151",fontSize:14,fontWeight:700,border:"1px solid #9ca3af",textAlign:"center"}}>#</th>
                {effectiveCols.map(c=><th key={c.name} style={{position:"sticky",top:0,zIndex:2,minWidth:120,padding:"6px 10px",background:"#e5e7eb",color:"#111827",fontSize:14,fontWeight:700,border:"1px solid #9ca3af",textAlign:"left",whiteSpace:"nowrap",overflow:"hidden",textOverflow:"ellipsis"}}>{c.name||<span style={{color:"#9ca3af"}}>(이름 없음)</span>}</th>)}
                <th style={{position:"sticky",top:0,zIndex:2,width:32,minWidth:32,background:"#e5e7eb",border:"1px solid #9ca3af"}}></th>
              </tr>
            </thead>
            <tbody>
              {(form.rows||[]).map((r,i)=>(
                <tr key={i}>
                  <td style={{position:"sticky",left:0,zIndex:1,width:44,minWidth:44,padding:"0 4px",background:"#f3f4f6",color:"#6b7280",fontSize:14,fontWeight:600,border:"1px solid #d1d5db",textAlign:"center",cursor:"pointer",userSelect:"none"}}
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
                        style={{width:"100%",padding:"5px 8px",border:"none",background:"transparent",color:"#111827",fontSize:14,fontWeight:500,outline:"none",fontFamily:"'Consolas','Courier New',monospace",boxSizing:"border-box"}}
                      />
                    </td>);
                  })}
                  <td style={{width:32,minWidth:32,padding:"2px 4px",textAlign:"center",border:"1px solid #d1d5db",background:i%2===0?"#fff":"#f9fafb"}}>
                    <span onClick={()=>delRow(i)} title="행 삭제" style={{cursor:"pointer",color:"#ef4444",fontSize:14,fontWeight:700}}>✕</span>
                  </td>
                </tr>
              ))}
              {/* v8.7.2: 인라인 + 행 추가 버튼 */}
              {effectiveCols.length>0&&<tr>
                <td colSpan={effectiveCols.length+2} style={{padding:0,border:"1px dashed #d1d5db",background:"#f9fafb"}}>
                  <div onClick={addRow} title="행 추가" style={{cursor:"pointer",padding:"6px",textAlign:"center",color:"#6b7280",fontSize:14,fontWeight:600,userSelect:"none",transition:"background 0.15s"}}
                    onMouseEnter={e=>{e.currentTarget.style.background="#e0f2fe";e.currentTarget.style.color="#3b82f6";}}
                    onMouseLeave={e=>{e.currentTarget.style.background="transparent";e.currentTarget.style.color="#6b7280";}}
                  >＋ 행 추가</div>
                </td>
              </tr>}
              {(form.rows||[]).length===0&&effectiveCols.length===0&&(
                <tr><td colSpan={2} style={{padding:"24px",textAlign:"center",color:"#9ca3af",fontSize:14,background:"#fff",border:"1px solid #d1d5db"}}>컬럼을 먼저 정의한 뒤 행을 추가하세요.</td></tr>
              )}
            </tbody>
          </table>
        </div>
      </div>

      {/* v8.8.2: AWS S3 동기화 명령 UI 제거 — S3 sync 는 File Browser 에서만 관리. */}

      {/* Versions */}
      {versions.length>0&&<div style={{marginBottom:8}}>
        <div style={{fontSize:14,color:"var(--text-secondary)",marginBottom:4}}>버전 이력 ({versions.length}/30)</div>
        <div style={{display:"flex",flexDirection:"column",gap:2,maxHeight:160,overflow:"auto",border:"1px solid var(--border)",borderRadius:4,padding:4}}>{versions.map(v=>(
          <div key={v.name} style={{display:"flex",alignItems:"center",gap:6,padding:"3px 6px",fontSize:14,background:"var(--bg-card)",borderRadius:3}}>
            <span style={{fontFamily:"monospace",fontWeight:700,color:"var(--accent)",minWidth:40}}>{v.name}</span>
            <span style={{fontFamily:"monospace",color:"var(--text-secondary)"}}>{(v.updated||"").replace("T"," ").slice(0,16)}</span>
            <span style={{fontFamily:"monospace",color:v.action==="pre-rollback"?"#a855f7":"#64748b",fontSize:14}}>[{v.action||"edit"}]</span>
            {v.user&&<span style={{fontFamily:"monospace",color:"#10b981",fontSize:14}}>by {v.user}</span>}
            <span style={{color:"var(--text-secondary)",fontSize:14}}>{v.rows}r × {v.cols}c</span>
            <span onClick={()=>loadVersion(v.name)} style={{marginLeft:"auto",padding:"1px 8px",borderRadius:3,background:"var(--bg-hover)",cursor:"pointer",fontSize:14}}>미리보기</span>
            <span onClick={()=>rollbackTo(v.name)} style={{padding:"1px 8px",borderRadius:3,background:"#ef444422",color:"#ef4444",cursor:"pointer",fontSize:14,fontWeight:600}}>롤백</span>
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
  const S={width:"100%",padding:"6px 10px",borderRadius:5,border:"1px solid var(--border)",background:"var(--bg-primary)",color:"var(--text-primary)",fontSize:14,outline:"none"};
  return(<div className="tm-overlay" onClick={onClose}>
    <div onClick={e=>e.stopPropagation()} className="tm-modal" style={{width:"90%",maxWidth:600}}>
      <div style={{display:"flex",justifyContent:"space-between",alignItems:"center",marginBottom:12}}>
        <div style={{fontSize:16,fontWeight:700}}>{form.id?"그룹 편집":"새 테이블 그룹"}</div>
        <span onClick={onClose} style={{cursor:"pointer",fontSize:18}}>✕</span>
      </div>
      <div style={{marginBottom:8}}><div style={{fontSize:14,color:"var(--text-secondary)"}}>그룹명 (예: TABLE_ET)</div>
        <input value={form.name} onChange={e=>u("name",e.target.value)} style={S} placeholder="예: TABLE_ET"/></div>
      <div style={{marginBottom:8}}><div style={{fontSize:14,color:"var(--text-secondary)"}}>설명</div>
        <textarea value={form.description||""} onChange={e=>u("description",e.target.value)} rows={2} style={{...S,resize:"vertical"}}/></div>
      <div style={{marginBottom:8}}>
        <div style={{display:"flex",justifyContent:"space-between",alignItems:"center"}}>
          <div style={{fontSize:14,fontWeight:600}}>공유 컬럼 (모든 멤버 테이블이 상속)</div>
          <button onClick={addCol} style={{padding:"3px 10px",borderRadius:4,border:"none",background:"var(--accent)",color:"#fff",fontSize:14,cursor:"pointer"}}>+ 컬럼 추가</button>
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

// ─── Product YAML Configs ─────────────────────────────────
function ProductConfigPanel({canManage,onChanged}){
  const[list,setList]=useState([]);
  const[sel,setSel]=useState("");
  const[text,setText]=useState("");
  const[loading,setLoading]=useState(false);
  const[msg,setMsg]=useState("");
  const loadList=()=>sf(API+"/product-configs").then(d=>setList(d.configs||[])).catch(e=>setMsg(e.message||"목록 로드 실패"));
  useEffect(()=>{loadList();},[]);
  const newProduct=()=>{
    if(!canManage)return;
    const product=prompt("추가할 제품명을 입력하세요. 예: PRODC");
    const name=String(product||"").trim();
    if(!name)return;
    setSel(name);
    setText([
      `product: ${name}`,
      "process_id: ''",
      "description: ''",
      "owner: ''",
      "canonical_knobs: []",
      "canonical_inline_items: []",
      "et_key_items: []",
      "yld_metric: YIELD",
      "perf_metric: ''",
      "target_spec: {}",
      "probe_card_watch:",
      "  enabled: false",
      "  notify_admin: true",
      "  items: []",
      "wafer_layout: {}",
      "",
    ].join("\n"));
    setMsg("새 제품 블록 작성 중");
  };
  const pick=(product)=>{
    setSel(product);setLoading(true);setMsg("");
    sf(API+"/product-config?product="+encodeURIComponent(product))
      .then(d=>{setText(d.text||"");setLoading(false);})
      .catch(e=>{setMsg(e.message||"YAML 로드 실패");setLoading(false);});
  };
  const save=()=>{
    if(!sel)return;
    setMsg("저장 중...");
    sf(API+"/product-config/save",{method:"POST",headers:{"Content-Type":"application/json"},body:JSON.stringify({product:sel,text})})
      .then(d=>{setMsg(d.errors?.length?"저장됨, 확인 필요: "+d.errors.join(", "):"저장됨");loadList();setTimeout(()=>setMsg(""),2400);})
      .catch(e=>setMsg(e.message||"저장 실패"));
  };
  const deleteSelected=()=>{
    if(!sel||!canManage)return;
    if(!confirm(`${sel} 제품 YAML 블록을 삭제할까요?`))return;
    setMsg("삭제 중...");
    sf(API+"/product-config/delete",{method:"POST",headers:{"Content-Type":"application/json"},body:JSON.stringify({product:sel})})
      .then(()=>{setSel("");setText("");setMsg("삭제됨");loadList();if(onChanged)onChanged();setTimeout(()=>setMsg(""),2200);})
      .catch(e=>setMsg(e.message||"삭제 실패"));
  };
  return(<div style={{display:"grid",gridTemplateColumns:"300px 1fr",gap:14}}>
    <div style={{background:"var(--bg-secondary)",border:"1px solid var(--border)",borderRadius:8,padding:10,maxHeight:"calc(100vh - 260px)",overflow:"auto"}}>
      <div style={{display:"flex",justifyContent:"space-between",alignItems:"center",gap:8,marginBottom:8}}>
        <div style={{fontSize:14,fontWeight:800,color:"var(--accent)",fontFamily:"monospace"}}>Product YAML · products.yaml ({list.length})</div>
        <button disabled={!canManage} onClick={newProduct} title="products.yaml 안에 새 제품 블록 추가" style={{padding:"4px 8px",borderRadius:4,border:"1px solid var(--border)",background:"var(--bg-card)",color:canManage?"var(--text-primary)":"var(--text-secondary)",fontSize:14,cursor:canManage?"pointer":"not-allowed"}}>+ 제품</button>
      </div>
      {list.length===0&&<div style={{padding:18,textAlign:"center",fontSize:14,color:"var(--text-secondary)"}}>product_config YAML 이 없습니다.</div>}
      {list.map(p=>(
        <div key={p.product} onClick={()=>pick(p.product)} style={{padding:"8px 10px",borderRadius:6,cursor:"pointer",marginBottom:5,background:sel===p.product?"var(--accent-glow)":"var(--bg-card)",border:"1px solid "+(sel===p.product?"var(--accent)":"var(--border)")}}>
          <div style={{display:"flex",justifyContent:"space-between",gap:8,alignItems:"center"}}>
            <span style={{fontSize:14,fontWeight:800,fontFamily:"monospace"}}>{p.product}</span>
            <span style={{fontSize:14,color:"var(--text-secondary)",fontFamily:"monospace"}}>{p.file}</span>
          </div>
          <div style={{fontSize:14,color:"var(--text-secondary)",marginTop:3,whiteSpace:"nowrap",overflow:"hidden",textOverflow:"ellipsis"}}>
            proc_id: {p.process_id||"-"} · owner: {p.owner||"-"}
          </div>
          <div style={{fontSize:14,color:"var(--text-secondary)",marginTop:2}}>KNOB {p.knob_count} · ET {p.et_key_count} · spec {p.has_spec?"Y":"-"}</div>
        </div>
      ))}
    </div>
    <div style={{background:"var(--bg-secondary)",border:"1px solid var(--border)",borderRadius:8,padding:14,minHeight:420}}>
      {!sel&&<div style={{padding:50,textAlign:"center",fontSize:14,color:"var(--text-secondary)"}}>왼쪽에서 제품 YAML 을 선택하세요.</div>}
      {sel&&<>
        <div style={{display:"flex",alignItems:"center",gap:10,marginBottom:10}}>
          <span style={{fontSize:14,fontWeight:800,fontFamily:"monospace",color:"var(--accent)"}}>products.yaml · {sel}</span>
          {loading&&<span style={{fontSize:14,color:"var(--text-secondary)"}}>로딩 중...</span>}
          {msg&&<span style={{fontSize:14,fontFamily:"monospace",color:msg.includes("실패")||msg.includes("parse")||msg.includes("확인")?"#ef4444":"#10b981"}}>{msg}</span>}
          <button disabled={!canManage||loading} onClick={deleteSelected} title={canManage?"제품 YAML 삭제":"tablemap 관리자 권한 필요"} style={{marginLeft:"auto",padding:"6px 12px",borderRadius:5,border:"1px solid #ef4444",background:"transparent",color:"#ef4444",fontSize:14,fontWeight:700,cursor:canManage?"pointer":"not-allowed"}}>삭제</button>
          <button disabled={!canManage||loading} onClick={save} title={canManage?"YAML 저장":"tablemap 관리자 권한 필요"} style={{padding:"6px 14px",borderRadius:5,border:"none",background:canManage?"var(--accent)":"#64748b",color:"#fff",fontSize:14,fontWeight:700,cursor:canManage?"pointer":"not-allowed"}}>저장</button>
        </div>
        <textarea value={text} onChange={e=>setText(e.target.value)} spellCheck={false}
          style={{width:"100%",minHeight:"calc(100vh - 330px)",maxHeight:"calc(100vh - 260px)",resize:"vertical",boxSizing:"border-box",padding:12,borderRadius:6,border:"1px solid var(--border)",background:"var(--bg-primary)",color:"var(--text-primary)",fontFamily:"Consolas, monospace",fontSize:14,lineHeight:1.5,outline:"none"}}
        />
        <div style={{fontSize:14,color:"var(--text-secondary)",marginTop:8,lineHeight:1.6}}>
          제품별로 수정하지만 저장 파일은 하나입니다: <code>product_config/products.yaml</code>. 저장 시 해당 제품 블록만 갱신되고 다른 제품 블록은 유지됩니다.
        </div>
      </>}
    </div>
  </div>);
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
  const[productFilter,setProductFilter]=useState("ALL");
  const[productConfigs,setProductConfigs]=useState([]);
  const[hiddenProductPages,setHiddenProductPages]=useState([]);
  const[selectedProductConfig,setSelectedProductConfig]=useState(null);
  const[productConfigLoading,setProductConfigLoading]=useState(false);
  useEffect(()=>{if(showImport)sf(API+"/db-sources").then(d=>setImportSrcs(d.sources||[])).catch(()=>{});},[showImport]);
  const doImport=()=>{
    const src=importSrcs.find(s=>s.label===importForm.source);if(!src){alert("소스를 선택하세요");return;}
    const name=(importForm.name||"").trim()||(src.file?src.file.split(".")[0]:src.product||"imported");
    sf("/api/dbmap/tables/import",{method:"POST",headers:{"Content-Type":"application/json"},body:JSON.stringify({
      source_type:src.source_type,file:src.file||"",root:src.root||"",product:src.product||"",
      name,display_name:importForm.display_name,rows_limit:importForm.rows_limit||1000,username:user?.username||"",
    })}).then(r=>{alert(`임포트 완료 — ${r.id} (CSV: ${r.csv_path?.split(/[\\\/]/).pop()||""})`);setShowImport(false);setImportForm({source:"",name:"",display_name:"",rows_limit:1000});loadAll();}).catch(e=>alert(e.message));
  };
  const[view,setView]=useState("graph");
  const[editingRelation,setEditingRelation]=useState(null);
  const pageAdmins = Array.isArray(user?.page_admins) ? user.page_admins : [];
  const canManage = user?.role==="admin" || pageAdmins.includes("tablemap");
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
    sf(API+"/product-configs").then(d=>setProductConfigs(d.configs||[])).catch(()=>{});
    sf(API+"/product-pages").then(d=>setHiddenProductPages(d.hidden_product_pages||[])).catch(()=>setHiddenProductPages([]));
    if(showLineage)loadLineage();
  };
  useEffect(()=>{loadAll();},[]);
  useEffect(()=>{
    if(productFilter==="ALL"){
      setSelectedProductConfig(null);
      setProductConfigLoading(false);
      return;
    }
    setProductConfigLoading(true);
    sf(API+"/product-config?product="+encodeURIComponent(productFilter))
      .then(d=>{setSelectedProductConfig(d.config||{});setProductConfigLoading(false);})
      .catch(()=>{setSelectedProductConfig(null);setProductConfigLoading(false);});
  },[productFilter]);

  const normProduct=(v)=>String(v||"").trim();
  const objProducts=(o)=>[]
    .concat(Array.isArray(o?.products)?o.products:[])
    .concat(o?.product?[o.product]:[])
    .map(normProduct)
    .filter(Boolean);
  const hasProduct=(o,p)=>objProducts(o).some(x=>x.toLowerCase()===String(p).toLowerCase());
  const hasCommon=(o)=>objProducts(o).some(x=>x.toLowerCase()==="common");
  const tableById=new Map((tables||[]).map(t=>[t.id,t]));
  const groupById=new Map((groups||[]).map(g=>[g.id,g]));
  const nodeByIdForFilter=new Map((config.nodes||[]).map(n=>[n.id,n]));
  const productsForNode=(n)=>{
    const extra=[];
    if(n?.kind==="table") extra.push(...objProducts(tableById.get(n.ref_id)));
    if(n?.kind==="group") extra.push(...objProducts(groupById.get(n.ref_id)));
    return [...objProducts(n),...extra].filter(Boolean);
  };
  const nodeHasProduct=(n,p)=>productsForNode(n).some(x=>x.toLowerCase()===String(p).toLowerCase());
  const nodeHasCommon=(n)=>productsForNode(n).some(x=>x.toLowerCase()==="common");
  const splitRelationPairs=(fromStr,toStr)=>{
    const fc=splitRelationCols(fromStr);
    const tc=splitRelationCols(toStr);
    const n=Math.max(fc.length,tc.length);
    const out=[];
    for(let i=0;i<n;i++) out.push({from_col:fc[i]||"",to_col:tc[i]||""});
    return out;
  };
  const productOptions=[...new Set([
    ...(config.nodes||[]).flatMap(n=>productsForNode(n)),
    ...(config.relations||[]).flatMap(r=>objProducts(r)),
    ...(tables||[]).flatMap(t=>objProducts(t)),
    ...(groups||[]).flatMap(g=>objProducts(g)),
    ...(dbSources||[]).flatMap(s=>objProducts(s)),
    ...(productConfigs||[]).map(c=>c.product),
  ])].filter(p=>p&&p.toLowerCase()!=="common"&&!hiddenProductPages.some(h=>String(h).toLowerCase()===String(p).toLowerCase())).sort((a,b)=>a.localeCompare(b,"ko"));
  const hideProductPage=(product)=>{
    if(!canManage||!product||product==="ALL")return;
    if(!confirm(`${product} 제품 페이지를 TableMap에서 지울까요?\n\n원본 DB/YAML/테이블 데이터는 삭제하지 않고, 제품 선택 목록에서만 숨깁니다.`))return;
    sf(API+"/product-pages/delete",{method:"POST",headers:{"Content-Type":"application/json"},body:JSON.stringify({product})})
      .then(d=>{
        const hidden=d.hidden_product_pages||[];
        setHiddenProductPages(hidden);
        if(String(productFilter).toLowerCase()===String(product).toLowerCase())setProductFilter("ALL");
      })
      .catch(e=>alert(e.message||"제품 페이지 삭제 실패"));
  };
  const unhideProductPage=(product)=>{
    if(!canManage||!product)return;
    sf(API+"/product-pages/unhide",{method:"POST",headers:{"Content-Type":"application/json"},body:JSON.stringify({product})})
      .then(d=>setHiddenProductPages(d.hidden_product_pages||[]))
      .catch(e=>alert(e.message||"제품 페이지 복원 실패"));
  };

  const buildProductGraph=()=>{
    const nodes=config.nodes||[];
    const relations=config.relations||[];
    if(productFilter==="ALL"){
      return {
        nodes,
        relations,
        nodeIds:new Set(nodes.map(n=>n.id)),
        relationIds:new Set(relations.map(r=>r.id)),
        seedIds:new Set(),
      };
    }
    const nodeIds=new Set();
    const relationIds=new Set();
    const seedIds=new Set();
    nodes.forEach(n=>{
      if(nodeHasProduct(n,productFilter)){
        nodeIds.add(n.id);
        seedIds.add(n.id);
      }
    });
    relations.forEach(r=>{
      const direct=hasProduct(r,productFilter);
      if(direct){
        if(r.from) nodeIds.add(r.from);
        if(r.to) nodeIds.add(r.to);
        if(r.from) seedIds.add(r.from);
        relationIds.add(r.id);
      }
    });
    let changed=true;
    while(changed){
      changed=false;
      relations.forEach(r=>{
        const relUsable=hasProduct(r,productFilter)||hasCommon(r)||objProducts(r).length===0;
        if(!relUsable) return;
        if(nodeIds.has(r.from)&&!nodeIds.has(r.to)){
          nodeIds.add(r.to); relationIds.add(r.id); changed=true;
        }else if(nodeIds.has(r.from)&&nodeIds.has(r.to)){
          relationIds.add(r.id);
        }
      });
    }
    // Keep common contract groups only when they are reached by an actual product path.
    const filteredNodes=nodes.filter(n=>nodeIds.has(n.id));
    const filteredRelations=relations.filter(r=>relationIds.has(r.id)&&nodeIds.has(r.from)&&nodeIds.has(r.to));
    return {nodes:filteredNodes,relations:filteredRelations,nodeIds,relationIds,seedIds};
  };
  const productGraph=buildProductGraph();
  const filteredConfig={...config,nodes:productGraph.nodes,relations:productGraph.relations};
  const visibleGroupIds=new Set(productGraph.nodes.filter(n=>n.kind==="group").map(n=>n.ref_id));
  const visibleTableIds=new Set(productGraph.nodes.filter(n=>n.kind==="table").map(n=>n.ref_id));
  const visibleGroups=groups.filter(g=>productFilter==="ALL"||visibleGroupIds.has(g.id)||hasProduct(g,productFilter));
  const visibleTables=tables.filter(t=>{
    if(productFilter==="ALL") return true;
    return visibleTableIds.has(t.id)||visibleGroupIds.has(t.group_id)||hasProduct(t,productFilter);
  });
  const visibleNodes=filteredConfig.nodes||[];
  const visibleRelations=filteredConfig.relations||[];
  const connectionSummary={
    db:(visibleNodes||[]).filter(n=>n.kind==="db_ref").length,
    groups:visibleGroups.length,
    tables:visibleTables.length,
    relations:visibleRelations.length,
  };
  const selectedProductMeta=(productConfigs||[]).find(p=>String(p.product).toLowerCase()===String(productFilter).toLowerCase());
  const cfg=selectedProductConfig||{};
  const cfgListCount=(k)=>Array.isArray(cfg[k])?cfg[k].length:0;
  const cfgObjCount=(k)=>cfg[k]&&typeof cfg[k]==="object"&&!Array.isArray(cfg[k])?Object.keys(cfg[k]).length:0;
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
  // v8.8.2: 맵에서만 제거 — 원본(table json/csv) 보존. table/group/db_ref 공용.
  const unlinkNodeFromMap=(nid)=>sf(API+"/nodes/unlink?node_id="+encodeURIComponent(nid),{method:"POST"}).then(loadAll);
  const savePosition=(id,x,y)=>sf(API+"/node/position",{method:"POST",headers:{"Content-Type":"application/json"},body:JSON.stringify({node_id:id,x,y})}).then(loadAll);
  const saveRelationPosition=(id,label_dx,label_dy)=>sf(API+"/relations/label-position",{method:"POST",headers:{"Content-Type":"application/json"},body:JSON.stringify({relation_id:id,label_dx,label_dy})}).then(loadAll);
  const saveNodeColor=(node,color)=>{
    if(!canManage||!node)return;
    sf(API+"/node/color",{method:"POST",headers:{"Content-Type":"application/json"},body:JSON.stringify({node_id:node.id,ref_id:node.ref_id||"",color})})
      .then(loadAll)
      .catch(e=>alert(e.message||"색상 저장 실패"));
  };
  const[selectedNode,setSelectedNode]=useState(null);
  const[dbInfo,setDbInfo]=useState(null); // DB ref detail modal
  const[dbDesc,setDbDesc]=useState("");
  const onNodeClick=(node)=>{
    // Single click = select only (highlight)
    setSelectedNode(node);
  };
  const onNodeDblClick=(node)=>{
    // Double click = open editor (table/db_ref) OR create member table (group).
    if(node.kind==="table"){sf(API+"/tables/"+node.ref_id).then(d=>setEditingTable(d)).catch(()=>{});}
    else if(node.kind==="group"){
      // v8.8.13: 그룹 더블클릭 → 공유 컬럼 상속한 새 멤버 테이블 자동 생성.
      //          /tables/save 가 group_id 세팅 시 group.columns 를 자동 상속하므로 빈 컬럼으로 POST.
      //          편집은 그룹 헤더의 ✎ 아이콘(선택 후 사이드 패널)으로 진입.
      const gid=node.ref_id;
      sf(API+"/groups/"+gid).then(g=>{
        const base=(g.name||"GROUP")+"_T";
        // 기존 멤버 수 기반 이름 접미 숫자.
        const existing=(tables||[]).filter(t=>t.group_id===gid);
        let idx=existing.length+1;
        const used=new Set(existing.map(t=>t.name||""));
        while(used.has(base+idx)) idx++;
        const newName=base+idx;
        sf(API+"/tables/save",{method:"POST",headers:{"Content-Type":"application/json"},
          body:JSON.stringify({id:"",name:newName,group_id:gid,columns:[],rows:[],table_type:"data",username:user?.username||""})
        }).then(()=>{loadAll();}).catch(e=>alert("멤버 테이블 생성 실패: "+(e.message||e)));
      }).catch(e=>alert("그룹 조회 실패: "+(e.message||e)));
    }
    else if(node.kind==="db_ref"){
      setSelectedNode(node);
      sf(API+"/db-ref/info?node_id="+node.id).then(d=>{setDbInfo(d);setDbDesc(d.description||"");}).catch(()=>{});
    }
  };
  // v8.8.13: 그룹 편집 명시적 진입점 — 사이드 패널에서 호출.
  const openGroupEditor=(refId)=>{sf(API+"/groups/"+refId).then(d=>setEditingGroup(d)).catch(()=>{});};
  const saveDbDesc=()=>{if(!dbInfo)return;sf(API+"/db-ref/description",{method:"POST",headers:{"Content-Type":"application/json"},body:JSON.stringify({node_id:dbInfo.node_id,description:dbDesc})}).then(()=>{setDbInfo(null);setSelectedNode(null);loadAll();}).catch(e=>alert(e.message));};
  // Delete key removes DB ref from map (not actual DB)
  useEffect(()=>{
    const h=(e)=>{
      if(e.key!=="Delete") return;
      // 텍스트 입력 중일 땐 무시.
      const t=e.target;
      if(t && (t.tagName==="INPUT"||t.tagName==="TEXTAREA"||t.isContentEditable)) return;
      if(selectedNode?.kind==="db_ref"&&isAdmin){
        if(confirm(`맵에서 "${selectedNode.name}" 제거? (DB 데이터는 삭제되지 않습니다)`)){deleteDbRef(selectedNode.id);setSelectedNode(null);}
      }
      // v8.8.13: table (그룹 멤버 포함) 선택 후 Delete 로 삭제.
      else if(selectedNode?.kind==="table"&&isAdmin){
        if(confirm(`테이블 "${selectedNode.name}" 을(를) 삭제할까요? (아카이브됨)`)){
          deleteTable(selectedNode.ref_id); setSelectedNode(null);
        }
      }
    };
    window.addEventListener("keydown",h);return()=>window.removeEventListener("keydown",h);
  },[selectedNode,isAdmin]);
  // v8.8.13: 일반 table 을 group box 로 drop → 동명 컬럼(case-insensitive) 기준 흡수 편입.
  const onDropIntoGroup=async (tableNode, groupNode, draggedTable)=>{
    try{
      const [tbl, grp]=await Promise.all([
        sf(API+"/tables/"+tableNode.ref_id),
        sf(API+"/groups/"+groupNode.ref_id),
      ]);
      const groupCols=(grp.columns||[]).map(c=>(c.name||"").trim()).filter(Boolean);
      const groupColsLower=new Set(groupCols.map(s=>s.toLowerCase()));
      const tblCols=(tbl.columns||[]).map(c=>(c.name||"").trim()).filter(Boolean);
      const matches=tblCols.filter(c=>groupColsLower.has(c.toLowerCase()));
      const msg=matches.length>0
        ? `그룹 "${grp.name||groupNode.name}" 에 편입합니다.\n\n일치 컬럼(${matches.length}/${groupCols.length}): ${matches.slice(0,8).join(", ")}${matches.length>8?"…":""}\n\n나머지 컬럼은 그룹 스키마로 재정의됩니다. 계속할까요?`
        : `그룹 "${grp.name||groupNode.name}" 에 동명 컬럼이 없습니다.\n편입 시 모든 컬럼이 그룹 공유 컬럼(${groupCols.length}개)으로 재정의됩니다.\n계속할까요?`;
      if(!confirm(msg)) return;
      // rows 에서 case-insensitive 동명 컬럼 매핑 — 그룹 컬럼명으로 키 정규화.
      const colMap={}; // lower → group official name
      groupCols.forEach(c=>{colMap[c.toLowerCase()]=c;});
      const normRows=(tbl.rows||[]).map(row=>{
        const out={};
        for(const k of Object.keys(row||{})){
          const gname=colMap[(k||"").toLowerCase()];
          if(gname) out[gname]=row[k];
        }
        return out;
      });
      await sf(API+"/tables/save",{method:"POST",headers:{"Content-Type":"application/json"},
        body:JSON.stringify({
          id: tbl.id, name: tbl.name, display_name: tbl.display_name||"",
          group_id: groupNode.ref_id,
          columns: [], // BE 가 group_id 감지 시 group.columns 로 덮어씀
          rows: normRows,
          table_type: tbl.table_type||"data",
          description: tbl.description||"",
          username: user?.username||"",
        })
      });
      loadAll();
    }catch(e){ alert("그룹 편입 실패: "+(e.message||e)); }
  };
  const onMemberContext=(memberNode, tbl)=>{
    if(!isAdmin) return;
    if(!confirm(`그룹 멤버 테이블 "${tbl.name}" 을(를) 삭제할까요? (아카이브됨)`)) return;
    deleteTable(memberNode.ref_id);
  };
  // v8.8.13: relation 편집을 pairs 배열 기반 편집 표로 재설계.
  //   relForm.pairs = [{from_col, to_col}, ...]. 저장 시 기존 BE 호환(from_col/to_col 콤마 문자열) 로 직렬화.
  const[editRel,setEditRel]=useState(null);
  const[relationMode,setRelationMode]=useState("view");
  const[relForm,setRelForm]=useState({pairs:[],description:""});
  const[autoMatchInfo,setAutoMatchInfo]=useState(null); // {matched, fromTotal, toTotal}
  // v8.7.5: 노드 kind 에 따른 컬럼 목록 fetch. case-insensitive 교집합으로 자동 매칭.
  const _fetchNodeColumns=async(node)=>{
    if(!node)return[];
    try{
      if(node.kind==="table"){
        const d=await sf(API+"/tables/"+node.ref_id);
        return(d.columns||[]).map(c=>c.name||"").filter(Boolean);
      }
      if(node.kind==="group"){
        const d=await sf(API+"/groups/"+node.ref_id);
        return(d.columns||[]).map(c=>c.name||"").filter(Boolean);
      }
      if(node.kind==="db_ref"){
        const d=await sf(API+"/db-ref/info?node_id="+node.id);
        return d.columns||[];
      }
    }catch(_){}
    return[];
  };
  // v8.8.13: 자동 매칭 — case-insensitive 로 pairs 배열 채움. 기존 pairs 와 dedup.
  const autoMatchRelation=async(rel=editRel)=>{
    if(!rel)return;
    const fromN=config.nodes.find(n=>n.id===rel.from_id);
    const toN=config.nodes.find(n=>n.id===rel.to_id);
    const [fc,tc]=await Promise.all([_fetchNodeColumns(fromN),_fetchNodeColumns(toN)]);
    const toMap=new Map();(tc||[]).forEach(c=>{if(c)toMap.set(String(c).toLowerCase(),c);});
    const newPairs=[];
    (fc||[]).forEach(c=>{
      const key=String(c||"").toLowerCase();
      if(key&&toMap.has(key)) newPairs.push({from_col:c,to_col:toMap.get(key)});
    });
    setRelForm(f=>{
      const existing=Array.isArray(f.pairs)?f.pairs:[];
      const exKey=new Set(existing.map(p=>(p.from_col||"").toLowerCase()+"|"+(p.to_col||"").toLowerCase()));
      const merged=[...existing];
      for(const p of newPairs){
        const k=(p.from_col||"").toLowerCase()+"|"+(p.to_col||"").toLowerCase();
        if(!exKey.has(k)){merged.push(p);exKey.add(k);}
      }
      return {...f,pairs:merged};
    });
    setAutoMatchInfo({matched:newPairs.length,fromTotal:fc.length,toTotal:tc.length});
  };
  // v8.8.13: legacy from_col/to_col 문자열(콤마 구분) 을 pairs 배열로 변환.
  const _parsePairs=(fromStr,toStr)=>{
    return splitRelationPairs(fromStr,toStr);
  };
  const onAddRelation=(from,to)=>{
    const next={id:"",from_id:from.id,to_id:to.id,from_name:from.name,to_name:to.name};
    setEditRel(next);
    setRelationMode("edit");
    setRelForm({pairs:[],description:""});
    setAutoMatchInfo(null);
    setTimeout(()=>{autoMatchRelation(next);},50);
  };
  const onEditRelation=(r,mode="view")=>{
    const fromN=config.nodes.find(n=>n.id===r.from);const toN=config.nodes.find(n=>n.id===r.to);
    setEditRel({id:r.id,from_id:r.from,to_id:r.to,from_name:fromN?.name||"?",to_name:toN?.name||"?"});
    setRelationMode(mode);
    setRelForm({pairs:_parsePairs(r.from_col,r.to_col),description:r.description||""});
    setAutoMatchInfo(null);
  };
  const saveRelation=()=>{
    const pairs=(relForm.pairs||[]).filter(p=>(p.from_col||"").trim()&&(p.to_col||"").trim());
    const from_col=pairs.map(p=>p.from_col.trim()).join(", ");
    const to_col=pairs.map(p=>p.to_col.trim()).join(", ");
    sf(API+"/relations/save",{method:"POST",headers:{"Content-Type":"application/json"},
      body:JSON.stringify({id:editRel.id||"",from_id:editRel.from_id,to_id:editRel.to_id,from_col,to_col,description:relForm.description||""})
    }).then(()=>{setEditRel(null);loadAll();}).catch(e=>alert(e.message));
  };
  const delRelation=(rid)=>sf(API+"/relations/delete?relation_id="+rid,{method:"POST"}).then(loadAll);
  const closeRelationModal=()=>{setEditRel(null);setAutoMatchInfo(null);};
  const relationPairs=relForm.pairs||[];
  const relationEditing=canManage&&relationMode==="edit";
  const relationPairStatus=(p)=>{
    const from=String(p?.from_col||"").trim();
    const to=String(p?.to_col||"").trim();
    if(!from||!to)return{text:"incomplete",color:"#ef4444"};
    if(from.toLowerCase()===to.toLowerCase())return{text:"same name",color:"#22c55e"};
    return{text:"mapped",color:"var(--text-secondary)"};
  };

  return(<div style={{padding:"20px 28px",background:"var(--bg-primary)",minHeight:"calc(100vh - 52px)",color:"var(--text-primary)"}}>
    <div style={{display:"flex",justifyContent:"space-between",alignItems:"center",marginBottom:12,flexWrap:"wrap",gap:8}}>
      <div style={{fontSize:16,fontWeight:700,fontFamily:"monospace",color:"var(--accent)",display:"flex",alignItems:"center",gap:10}}>
        <span>테이블 맵</span>
      </div>
      <div style={{display:"flex",gap:4,alignItems:"center"}}>
        {[["graph","그래프"],["manage","관리"],["configs","YAML"]].map(([k,l])=>(
          <span key={k} onClick={()=>setView(k)} style={{padding:"4px 12px",borderRadius:4,fontSize:14,cursor:"pointer",fontWeight:view===k?600:400,background:view===k?"var(--accent-glow)":"transparent",color:view===k?"var(--accent)":"var(--text-secondary)"}}>{l}</span>))}
        {/* v8.8.13: 계보 탭 제거 — relation 편집 시 컬럼 매칭 표로 대체. */}
      </div>
      {canManage&&<div style={{display:"flex",gap:6}}>
        <button onClick={()=>setEditingTable({})} style={{padding:"4px 12px",borderRadius:4,border:"none",background:"var(--accent)",color:"#fff",fontSize:14,cursor:"pointer"}}>+ 테이블</button>
        <button onClick={()=>setShowImport(true)} style={{padding:"4px 12px",borderRadius:4,border:"none",background:"#10b981",color:"#fff",fontSize:14,cursor:"pointer"}} title="기존 Base/DB 데이터를 TableMap 에 불러오기">↓ 임포트</button>
        <button onClick={()=>setEditingGroup({})} style={{padding:"4px 12px",borderRadius:4,border:"none",background:"#a855f7",color:"#fff",fontSize:14,cursor:"pointer"}}>+ 그룹</button>
        <button onClick={()=>setPickingDb(true)} style={{padding:"4px 12px",borderRadius:4,border:"none",background:"#3b82f6",color:"#fff",fontSize:14,cursor:"pointer"}}>+ DB 참조</button>
        <button onClick={()=>{const name=prompt("더미 DB 이름 (예: WIP/PRODUCT_A):");if(name){addDbRef({kind:"db_ref",source_type:"dummy",name:name,root:name.split("/")[0]||name,product:name.split("/")[1]||""});}}} style={{padding:"4px 12px",borderRadius:4,border:"1px solid #3b82f6",background:"transparent",color:"#3b82f6",fontSize:14,cursor:"pointer"}}>+ 더미 DB</button>
      </div>}
    </div>

    <div style={{background:"var(--bg-secondary)",border:"1px solid var(--border)",borderRadius:6,padding:14,marginBottom:14,display:"grid",gridTemplateColumns:"1fr",gap:12,alignItems:"start"}}>
      <div>
        <div style={{display:"flex",alignItems:"center",gap:8,marginBottom:8,flexWrap:"wrap"}}>
          <div style={{fontSize:14,fontWeight:800,color:"var(--accent)",fontFamily:"monospace"}}>Product Connection</div>
          <Pill tone={productFilter==="ALL"?"neutral":"accent"}>{productFilter==="ALL"?"ALL":productFilter}</Pill>
          <Pill tone="info">DB {connectionSummary.db}</Pill>
          <Pill tone="neutral">tables {connectionSummary.tables}</Pill>
        </div>
        <div style={{display:"flex",gap:6,flexWrap:"wrap"}}>
          {["ALL",...productOptions].map((opt)=>(
            <span key={opt} style={{display:"inline-flex",alignItems:"center",border:"1px solid "+(productFilter===opt?"var(--accent)":"var(--border)"),borderRadius:4,background:productFilter===opt?"var(--accent-glow)":"var(--bg-card)",overflow:"hidden"}}>
              <button onClick={()=>setProductFilter(opt)} style={{padding:"6px 9px",border:"none",background:"transparent",color:productFilter===opt?"var(--accent)":"var(--text-primary)",cursor:"pointer",fontSize:14,fontWeight:700,fontFamily:"monospace"}}>
                {opt}
              </button>
              {canManage&&opt!=="ALL"&&(
                <button onClick={(e)=>{e.stopPropagation();hideProductPage(opt);}} title="제품 페이지 숨김" style={{padding:"6px 7px",border:"none",borderLeft:"1px solid var(--border)",background:"transparent",color:"#ef4444",cursor:"pointer",fontSize:14,fontWeight:800}}>
                  ×
                </button>
              )}
            </span>
          ))}
        </div>
        {canManage&&hiddenProductPages.length>0&&(
          <div style={{display:"flex",gap:6,flexWrap:"wrap",alignItems:"center",marginTop:8,fontSize:14,color:"var(--text-secondary)"}}>
            <span>숨김:</span>
            {hiddenProductPages.map(p=>(
              <button key={p} onClick={()=>unhideProductPage(p)} title="제품 페이지 복원" style={{padding:"3px 7px",borderRadius:4,border:"1px dashed var(--border)",background:"transparent",color:"var(--text-secondary)",cursor:"pointer",fontSize:14,fontFamily:"monospace"}}>
                {p} 복원
              </button>
            ))}
          </div>
        )}
      </div>
      {productFilter!=="ALL"&&<div style={{gridColumn:"1 / -1",borderTop:"1px solid var(--border)",paddingTop:12,display:"grid",gridTemplateColumns:"minmax(220px, 0.8fr) minmax(360px, 1.6fr)",gap:12,alignItems:"stretch"}}>
        <div style={{background:"var(--bg-card)",border:"1px solid var(--border)",borderRadius:6,padding:12}}>
          <div style={{display:"flex",alignItems:"center",justifyContent:"space-between",gap:8,marginBottom:8}}>
            <div style={{fontSize:14,fontWeight:800,color:"var(--accent)",fontFamily:"monospace"}}>products.yaml · {productFilter}</div>
            <Pill tone={selectedProductMeta?"info":"neutral"}>{selectedProductMeta?"saved":"template"}</Pill>
          </div>
          {productConfigLoading?<div style={{fontSize:14,color:"var(--text-secondary)"}}>YAML 기본 정보 로딩 중...</div>:<>
            <div style={{display:"grid",gridTemplateColumns:"90px 1fr",gap:"5px 10px",fontSize:14,lineHeight:1.5}}>
              <span style={{color:"var(--text-secondary)",fontWeight:700}}>process_id</span><span style={{fontFamily:"monospace"}}>{cfg.process_id||"-"}</span>
              <span style={{color:"var(--text-secondary)",fontWeight:700}}>owner</span><span>{cfg.owner||"-"}</span>
              <span style={{color:"var(--text-secondary)",fontWeight:700}}>perf_metric</span><span style={{fontFamily:"monospace"}}>{cfg.perf_metric||"-"}</span>
              <span style={{color:"var(--text-secondary)",fontWeight:700}}>yld_metric</span><span style={{fontFamily:"monospace"}}>{cfg.yld_metric||"-"}</span>
            </div>
            {cfg.description&&<div style={{fontSize:14,color:"var(--text-secondary)",marginTop:8,lineHeight:1.5}}>{cfg.description}</div>}
          </>}
        </div>
        <div style={{background:"var(--bg-card)",border:"1px solid var(--border)",borderRadius:6,padding:12}}>
          <div style={{fontSize:14,fontWeight:800,color:"var(--text-primary)",marginBottom:8}}>해당 제품 Table Map 정보</div>
          <div style={{display:"grid",gridTemplateColumns:"repeat(4,minmax(92px,1fr))",gap:8,marginBottom:8}}>
            {[["DB",connectionSummary.db],["Groups",connectionSummary.groups],["Tables",connectionSummary.tables],["Relations",connectionSummary.relations]].map(([k,v])=>(
              <div key={k} style={{border:"1px solid var(--border)",borderRadius:5,padding:"7px 8px",background:"var(--bg-secondary)"}}>
                <div style={{fontSize:14,color:"var(--text-secondary)",fontWeight:800,textTransform:"uppercase"}}>{k}</div>
                <div style={{fontSize:16,fontWeight:800,fontFamily:"monospace",color:"var(--accent)"}}>{v}</div>
              </div>
            ))}
          </div>
          <div style={{display:"flex",gap:6,flexWrap:"wrap",fontSize:14,color:"var(--text-secondary)"}}>
            <span style={{padding:"3px 7px",borderRadius:4,background:"var(--bg-secondary)",border:"1px solid var(--border)"}}>canonical_knobs {cfgListCount("canonical_knobs")}</span>
            <span style={{padding:"3px 7px",borderRadius:4,background:"var(--bg-secondary)",border:"1px solid var(--border)"}}>inline_items {cfgListCount("canonical_inline_items")}</span>
            <span style={{padding:"3px 7px",borderRadius:4,background:"var(--bg-secondary)",border:"1px solid var(--border)"}}>et_key_items {cfgListCount("et_key_items")}</span>
            <span style={{padding:"3px 7px",borderRadius:4,background:"var(--bg-secondary)",border:"1px solid var(--border)"}}>target_spec {cfgObjCount("target_spec")}</span>
          </div>
        </div>
      </div>}
    </div>

    {view==="graph"&&<GraphView config={filteredConfig} groups={visibleGroups} tables={visibleTables} onNodeClick={onNodeClick} onNodeDblClick={onNodeDblClick} onAddRelation={onAddRelation} onSavePosition={savePosition}
      onSaveRelationPosition={saveRelationPosition}
      onDropIntoGroup={onDropIntoGroup} onMemberContext={onMemberContext}
      selectedNodeId={selectedNode?.id}
      selectedRelationId={editRel?.id||""}
      onEditRelation={onEditRelation}
      canManage={canManage}
      onSetNodeColor={saveNodeColor}
      lineageEdges={lineageData.edges} showLineage={showLineage}
      onNodeRightClick={(e,node)=>{
        // v8.8.2: 모든 노드(table/db_ref/group) 에 대해 "맵에서만 제거" 지원.
        if(!confirm(`맵에서 "${node.name||node.id}" 를 제거할까요?\n\n※ 원본 테이블/DB 파일은 영향 받지 않고 그래프 참조만 제거됩니다.`))return;
        unlinkNodeFromMap(node.id).then(()=>setSelectedNode(null));
      }}/>}
    {/* v8.8.13: 계보 상태바 제거 */}
    {/* DB ref detail modal */}
    {dbInfo&&<div className="tm-overlay" onClick={()=>{setDbInfo(null);setSelectedNode(null);}}>
      <div onClick={e=>e.stopPropagation()} className="tm-modal" style={{width:"90%",maxWidth:600,maxHeight:"85vh",overflow:"auto"}}>
        <div style={{display:"flex",justifyContent:"space-between",alignItems:"center",marginBottom:14}}>
          <div style={{fontSize:16,fontWeight:700,color:"#3b82f6"}}>🗄️ {dbInfo.name}</div>
          <span onClick={()=>{setDbInfo(null);setSelectedNode(null);}} style={{cursor:"pointer",fontSize:18}}>✕</span>
        </div>
        {/* Info grid */}
        <div style={{display:"grid",gridTemplateColumns:"110px 1fr",gap:"6px 12px",fontSize:14,marginBottom:14}}>
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
          <div style={{fontSize:14,fontWeight:600,marginBottom:6}}>컬럼 ({dbInfo.columns.length})</div>
          <div style={{maxHeight:180,overflow:"auto",border:"1px solid var(--border)",borderRadius:6}}>
            <table style={{width:"100%",borderCollapse:"collapse",fontSize:14}}>
              <thead><tr><th style={{textAlign:"left",padding:"4px 10px",background:"var(--bg-tertiary)",borderBottom:"1px solid var(--border)",fontSize:14}}>컬럼</th><th style={{textAlign:"left",padding:"4px 10px",background:"var(--bg-tertiary)",borderBottom:"1px solid var(--border)",fontSize:14}}>유형</th></tr></thead>
              <tbody>{dbInfo.columns.map(c=><tr key={c}><td style={{padding:"3px 10px",borderBottom:"1px solid var(--border)",fontFamily:"monospace"}}>{c}</td><td style={{padding:"3px 10px",borderBottom:"1px solid var(--border)",color:"var(--text-secondary)",fontSize:14}}>{dbInfo.dtypes?.[c]||""}</td></tr>)}</tbody>
            </table>
          </div>
        </div>}
        {/* Description */}
        <div style={{marginBottom:14}}>
          <div style={{fontSize:14,fontWeight:600,marginBottom:4}}>설명</div>
          <textarea value={dbDesc} onChange={e=>setDbDesc(e.target.value)} rows={3}
            style={{width:"100%",padding:"8px 12px",borderRadius:6,border:"1px solid var(--border)",background:"var(--bg-primary)",color:"var(--text-primary)",fontSize:14,outline:"none",resize:"vertical",fontFamily:"monospace"}}
            placeholder="이 데이터베이스에 대한 메모 추가..."/>
        </div>
        <div style={{display:"flex",gap:8}}>
          <button onClick={saveDbDesc} style={{padding:"8px 20px",borderRadius:6,border:"none",background:"var(--accent)",color:"#fff",fontWeight:600,cursor:"pointer"}}>설명 저장</button>
          <button onClick={()=>{setDbInfo(null);setSelectedNode(null);}} style={{padding:"8px 16px",borderRadius:6,border:"1px solid var(--border)",background:"transparent",color:"var(--text-secondary)",cursor:"pointer"}}>닫기</button>
          {isAdmin&&<button onClick={()=>{if(confirm("맵에서 제거할까요? (DB 데이터는 영향 없음)")){sf(API+"/db-ref/delete?node_id="+dbInfo.node_id,{method:"POST"}).then(()=>{setDbInfo(null);setSelectedNode(null);loadAll();});}}} style={{marginLeft:"auto",padding:"8px 16px",borderRadius:6,border:"1px solid #ef4444",background:"transparent",color:"#ef4444",cursor:"pointer",fontSize:14}}>맵에서 해제</button>}
        </div>
      </div>
    </div>}

    {view==="manage"&&<div style={{display:"grid",gridTemplateColumns:"repeat(auto-fill,minmax(280px,1fr))",gap:12}}>
      {visibleGroups.map(g=>(<div key={g.id} style={{background:"var(--bg-secondary)",borderRadius:8,border:"1px solid #a855f7",padding:12}}>
        <div style={{fontSize:14,color:"#a855f7",fontWeight:700,marginBottom:4}}>📚 그룹</div>
        <div style={{fontSize:14,fontWeight:600,marginBottom:4}}>{g.name}</div>
        <div style={{fontSize:14,color:"var(--text-secondary)"}}>{g.tables?.length||0} 테이블 | {g.updated?.slice(0,10)}</div>
        <div style={{display:"flex",gap:4,marginTop:6}}>
          <span onClick={()=>sf(API+"/groups/"+g.id).then(d=>setEditingGroup(d)).catch(()=>{})} style={{color:"var(--accent)",cursor:"pointer",fontSize:14}}>편집</span>
          {canManage&&<span onClick={()=>deleteGroup(g.id)} style={{color:"#ef4444",cursor:"pointer",fontSize:14}}>삭제</span>}
        </div>
      </div>))}
      {visibleTables.map(t=>(<div key={t.id} style={{background:"var(--bg-secondary)",borderRadius:8,border:"1px solid var(--accent)",padding:12}}>
        <div style={{fontSize:14,color:"var(--accent)",fontWeight:700,marginBottom:4}}>📋 테이블{t.group_id?" (그룹 내)":""}</div>
        <div style={{fontSize:14,fontWeight:600,marginBottom:4}}>{t.name}</div>
        <div style={{fontSize:14,color:"var(--text-secondary)"}}>{t.updated?.slice(0,10)} · <span style={{fontFamily:"monospace",color:"#f97316"}}>📄 {(t.name||t.id).replace(/[^a-zA-Z0-9_-]/g,"_")}.csv</span></div>
        <div style={{display:"flex",gap:4,marginTop:6}}>
          <span onClick={()=>sf(API+"/tables/"+t.id).then(d=>setEditingTable(d)).catch(()=>{})} style={{color:"var(--accent)",cursor:"pointer",fontSize:14}}>편집</span>
          {canManage&&<span onClick={()=>{if(confirm("삭제할까요? (아카이브됨)"))deleteTable(t.id);}} style={{color:"#ef4444",cursor:"pointer",fontSize:14}}>삭제</span>}
        </div>
      </div>))}
    </div>}

    {view==="configs"&&<ProductConfigPanel canManage={canManage} onChanged={loadAll}/>}

    {view==="relations"&&<div style={{background:"var(--bg-secondary)",borderRadius:8,border:"1px solid var(--border)",overflow:"auto"}}>
      <table style={{width:"100%",borderCollapse:"collapse",fontSize:14}}>
        <thead><tr>{["소스","타겟","소스 컬럼","타겟 컬럼","설명","작업"].map(h=><th key={h} style={{textAlign:"left",padding:"8px 12px",background:"var(--bg-tertiary)",color:"var(--text-secondary)",fontSize:14,borderBottom:"1px solid var(--border)"}}>{h}</th>)}</tr></thead>
        <tbody>{(config.relations||[]).map(r=>{const a=config.nodes.find(n=>n.id===r.from);const b=config.nodes.find(n=>n.id===r.to);return(
          <tr key={r.id}>
            <td style={{padding:"6px 12px",borderBottom:"1px solid var(--border)"}}>{a?.name||"?"}</td>
            <td style={{padding:"6px 12px",borderBottom:"1px solid var(--border)"}}>{b?.name||"?"}</td>
            <td style={{padding:"6px 12px",borderBottom:"1px solid var(--border)",fontFamily:"monospace",fontSize:14}}>{r.from_col||"-"}</td>
            <td style={{padding:"6px 12px",borderBottom:"1px solid var(--border)",fontFamily:"monospace",fontSize:14}}>{r.to_col||"-"}</td>
            <td style={{padding:"6px 12px",borderBottom:"1px solid var(--border)",fontSize:14,color:"var(--text-secondary)"}}>{r.description||""}</td>
            <td style={{padding:"6px 12px",borderBottom:"1px solid var(--border)"}}>{isAdmin&&<span onClick={()=>delRelation(r.id)} style={{color:"#ef4444",cursor:"pointer",fontSize:14}}>삭제</span>}</td>
          </tr>);})}</tbody>
      </table>
      {(config.relations||[]).length===0&&<div style={{padding:40,textAlign:"center",color:"var(--text-secondary)"}}>관계가 없습니다. 그래프 뷰에서 Shift+클릭으로 노드 A 를 선택한 뒤 노드 B 를 클릭하세요.</div>}
    </div>}

    {/* Relation node click opens a clean matched-column table. Admin editing is an explicit mode. */}
    {editRel&&<div className="tm-overlay" onClick={closeRelationModal}>
      <div onClick={e=>e.stopPropagation()} className="tm-modal" style={{width:680,maxWidth:"94vw"}}>
        <div style={{display:"flex",justifyContent:"space-between",alignItems:"flex-start",gap:12,marginBottom:14}}>
          <div>
            <div style={{fontSize:15,fontWeight:800}}>관계 매칭</div>
            <div style={{fontSize:14,color:"var(--text-secondary)",marginTop:5,fontFamily:"monospace"}}>
              <strong style={{color:"var(--accent)"}}>{editRel.from_name}</strong>
              <span style={{padding:"0 8px",color:"var(--text-secondary)"}}>→</span>
              <strong style={{color:"var(--accent)"}}>{editRel.to_name}</strong>
            </div>
          </div>
          <div style={{display:"flex",alignItems:"center",gap:8}}>
            <span style={{padding:"4px 9px",borderRadius:999,background:"var(--accent-glow)",color:"var(--accent)",fontSize:14,fontWeight:900,fontFamily:"monospace"}}>
              {relationPairs.length} MATCH
            </span>
            <span onClick={closeRelationModal} style={{cursor:"pointer",fontSize:18}}>✕</span>
          </div>
        </div>

        {relationEditing&&<div style={{display:"flex",gap:8,alignItems:"center",marginBottom:10,padding:"6px 10px",borderRadius:5,background:"rgba(34,197,94,0.08)",border:"1px solid rgba(34,197,94,0.3)"}}>
          <button onClick={()=>autoMatchRelation()} style={{padding:"4px 12px",borderRadius:5,border:"1px solid #22c55e",background:"transparent",color:"#22c55e",fontSize:14,fontWeight:700,cursor:"pointer"}}>자동 매칭</button>
          <span style={{fontSize:14,color:"var(--text-secondary)"}}>대소문자 무시 동명 컬럼을 추가합니다.</span>
          {autoMatchInfo&&<span style={{fontSize:14,marginLeft:"auto",color:autoMatchInfo.matched>0?"#22c55e":"var(--text-secondary)",fontFamily:"monospace"}}>+{autoMatchInfo.matched} ({autoMatchInfo.fromTotal}↔{autoMatchInfo.toTotal})</span>}
        </div>}

        <div style={{border:"1px solid var(--border)",borderRadius:7,overflow:"hidden",marginBottom:10}}>
          <table style={{width:"100%",borderCollapse:"collapse",fontSize:14,tableLayout:"fixed"}}>
            <thead>
              <tr style={{background:"var(--bg-tertiary)"}}>
                <th style={{width:44,padding:"8px 8px",textAlign:"center",borderBottom:"1px solid var(--border)",fontSize:14,color:"var(--text-secondary)",fontFamily:"monospace"}}>#</th>
                <th style={{padding:"8px 10px",textAlign:"left",borderBottom:"1px solid var(--border)",fontSize:14,color:"var(--accent)",fontFamily:"monospace"}}>{editRel.from_name}</th>
                <th style={{padding:"8px 10px",textAlign:"left",borderBottom:"1px solid var(--border)",fontSize:14,color:"var(--accent)",fontFamily:"monospace"}}>{editRel.to_name}</th>
                <th style={{width:104,padding:"8px 10px",textAlign:"left",borderBottom:"1px solid var(--border)",fontSize:14,color:"var(--text-secondary)",fontFamily:"monospace"}}>STATUS</th>
                {relationEditing&&<th style={{width:34,padding:"8px 6px",textAlign:"center",borderBottom:"1px solid var(--border)",fontSize:14,color:"var(--text-secondary)"}}></th>}
              </tr>
            </thead>
            <tbody>
              {relationPairs.length===0&&(
                <tr>
                  <td colSpan={relationEditing?5:4} style={{padding:"18px 10px",textAlign:"center",color:"var(--text-secondary)",fontSize:14}}>
                    {relationEditing?"매칭된 컬럼이 없습니다. 자동 매칭 또는 행 추가로 시작하세요.":"매칭된 컬럼이 없습니다."}
                  </td>
                </tr>
              )}
              {relationPairs.map((p,i)=>{
                const status=relationPairStatus(p);
                return (
                  <tr key={i} style={{background:i%2===0?"var(--bg-card)":"var(--bg-secondary)"}}>
                    <td style={{padding:"7px 8px",textAlign:"center",borderBottom:"1px solid var(--border)",fontFamily:"monospace",color:"var(--text-secondary)",fontWeight:800}}>{i+1}</td>
                    <td style={{padding:"5px 8px",borderBottom:"1px solid var(--border)",minWidth:0}}>
                      {relationEditing?<input value={p.from_col||""} onChange={e=>setRelForm(f=>{const n=(f.pairs||[]).slice();n[i]={...n[i],from_col:e.target.value};return{...f,pairs:n};})}
                        placeholder="source column" style={{width:"100%",padding:"5px 7px",borderRadius:4,border:"1px solid var(--border)",background:"var(--bg-primary)",color:"var(--text-primary)",fontSize:14,fontFamily:"monospace",boxSizing:"border-box"}}/>:
                        <span style={{display:"block",fontFamily:"monospace",fontWeight:800,color:"var(--text-primary)",whiteSpace:"nowrap",overflow:"hidden",textOverflow:"ellipsis"}} title={p.from_col||""}>{p.from_col||"-"}</span>}
                    </td>
                    <td style={{padding:"5px 8px",borderBottom:"1px solid var(--border)",minWidth:0}}>
                      {relationEditing?<input value={p.to_col||""} onChange={e=>setRelForm(f=>{const n=(f.pairs||[]).slice();n[i]={...n[i],to_col:e.target.value};return{...f,pairs:n};})}
                        placeholder="target column" style={{width:"100%",padding:"5px 7px",borderRadius:4,border:"1px solid var(--border)",background:"var(--bg-primary)",color:"var(--text-primary)",fontSize:14,fontFamily:"monospace",boxSizing:"border-box"}}/>:
                        <span style={{display:"block",fontFamily:"monospace",fontWeight:800,color:"var(--text-primary)",whiteSpace:"nowrap",overflow:"hidden",textOverflow:"ellipsis"}} title={p.to_col||""}>{p.to_col||"-"}</span>}
                    </td>
                    <td style={{padding:"7px 10px",borderBottom:"1px solid var(--border)",fontFamily:"monospace",color:status.color,fontSize:14,fontWeight:900,whiteSpace:"nowrap"}}>{status.text}</td>
                    {relationEditing&&<td style={{textAlign:"center",borderBottom:"1px solid var(--border)"}}>
                      <span onClick={()=>setRelForm(f=>({...f,pairs:(f.pairs||[]).filter((_,j)=>j!==i)}))} title="이 쌍 제거"
                        style={{cursor:"pointer",color:"#ef4444",fontSize:14,fontWeight:800,padding:"0 6px"}}>×</span>
                    </td>}
                  </tr>
                );
              })}
            </tbody>
          </table>
        </div>

        {relationEditing&&<div style={{marginBottom:10}}>
          <button onClick={()=>setRelForm(f=>({...f,pairs:[...(f.pairs||[]),{from_col:"",to_col:""}]}))}
            style={{padding:"4px 12px",borderRadius:4,border:"1px dashed var(--accent)",background:"transparent",color:"var(--accent)",fontSize:14,cursor:"pointer"}}>+ 행 추가</button>
        </div>}

        {(relationEditing||relForm.description)&&<div style={{marginBottom:12}}>
          <div style={{fontSize:14,color:"var(--text-secondary)",marginBottom:3}}>설명</div>
          {relationEditing?<input value={relForm.description} onChange={e=>setRelForm({...relForm,description:e.target.value})} placeholder="예: Lot 이력 추적"
            style={{width:"100%",padding:"8px 12px",borderRadius:5,border:"1px solid var(--border)",background:"var(--bg-primary)",color:"var(--text-primary)",fontSize:14,outline:"none",boxSizing:"border-box"}}/>:
            <div style={{padding:"8px 12px",borderRadius:5,border:"1px solid var(--border)",background:"var(--bg-primary)",color:"var(--text-primary)",fontSize:14,minHeight:16}}>{relForm.description}</div>}
        </div>}

        <div style={{display:"flex",gap:8}}>
          {relationEditing&&<button onClick={saveRelation} style={{flex:1,padding:"8px",borderRadius:6,border:"none",background:"var(--accent)",color:"#fff",fontWeight:700,cursor:"pointer"}}>저장</button>}
          {canManage&&!relationEditing&&<button onClick={()=>setRelationMode("edit")} style={{flex:1,padding:"8px",borderRadius:6,border:"1px solid var(--accent)",background:"transparent",color:"var(--accent)",fontWeight:700,cursor:"pointer"}}>편집</button>}
          {canManage&&editRel.id&&<button onClick={()=>{delRelation(editRel.id);closeRelationModal();}} style={{padding:"8px 16px",borderRadius:6,border:"1px solid #ef4444",background:"transparent",color:"#ef4444",cursor:"pointer"}}>삭제</button>}
          <button onClick={closeRelationModal} style={{padding:"8px 16px",borderRadius:6,border:"1px solid var(--border)",background:"transparent",color:"var(--text-secondary)",cursor:"pointer"}}>{relationEditing?"취소":"닫기"}</button>
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
        <div style={{fontSize:14,color:"var(--text-secondary)",marginBottom:12}}>Base/DB 의 parquet/csv 를 TableMap 테이블로 가져옵니다. 스키마 + 최대 rows 건.</div>
        <div style={{marginBottom:8}}>
          <div style={{fontSize:14,color:"var(--text-secondary)",marginBottom:4}}>소스</div>
          <select value={importForm.source} onChange={e=>setImportForm({...importForm,source:e.target.value})} style={{width:"100%",padding:"6px 10px",borderRadius:5,border:"1px solid var(--border)",background:"var(--bg-primary)",color:"var(--text-primary)",fontSize:14,fontFamily:"monospace"}}>
            <option value="">-- 선택 --</option>
            {importSrcs.map(s=><option key={s.label} value={s.label}>{s.label}</option>)}
          </select>
        </div>
        <div style={{display:"flex",gap:8,marginBottom:8}}>
          <div style={{flex:1}}><div style={{fontSize:14,color:"var(--text-secondary)"}}>테이블 이름 (선택)</div><input value={importForm.name} onChange={e=>setImportForm({...importForm,name:e.target.value})} placeholder="자동(파일명)" style={{width:"100%",padding:"6px 10px",borderRadius:5,border:"1px solid var(--border)",background:"var(--bg-primary)",color:"var(--text-primary)",fontSize:14}}/></div>
          <div style={{flex:1}}><div style={{fontSize:14,color:"var(--text-secondary)"}}>표시 라벨 (선택)</div><input value={importForm.display_name} onChange={e=>setImportForm({...importForm,display_name:e.target.value})} placeholder="그래프 라벨" style={{width:"100%",padding:"6px 10px",borderRadius:5,border:"1px solid var(--border)",background:"var(--bg-primary)",color:"var(--text-primary)",fontSize:14}}/></div>
        </div>
        <div style={{marginBottom:12}}>
          <div style={{fontSize:14,color:"var(--text-secondary)"}}>최대 행 수 (기본 1000)</div>
          <input type="number" value={importForm.rows_limit} onChange={e=>setImportForm({...importForm,rows_limit:parseInt(e.target.value)||1000})} style={{width:120,padding:"6px 10px",borderRadius:5,border:"1px solid var(--border)",background:"var(--bg-primary)",color:"var(--text-primary)",fontSize:14}}/>
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
        <div style={{fontSize:14,color:"var(--text-secondary)",marginBottom:8}}>맵 노드로 추가할 DB 소스를 선택하세요 (참조만, 실제 데이터는 변경되지 않습니다)</div>
        {dbSources.map(s=><div key={s.label} onClick={()=>addDbRef(s)} style={{padding:"8px 12px",background:"var(--bg-card,var(--bg-primary,#fff))",color:"var(--text-primary,#111827)",borderRadius:6,marginBottom:4,cursor:"pointer",fontSize:14,fontWeight:600,border:"1px solid var(--border)"}}>
          {s.label} <span style={{fontSize:14,color:"var(--accent,#ea580c)",fontWeight:700}}>[{s.source_type}]</span>
        </div>)}
      </div></div>}
  </div>);
}
