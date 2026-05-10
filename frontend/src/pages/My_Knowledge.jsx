import { useEffect, useMemo, useRef, useState } from "react";
import { sf } from "../lib/api";
import { canManagePage } from "../lib/permissions";

const API = "/api/knowledge";

const KIND_OPTIONS = ["manual","product","lot","wafer","knob","issue","meeting","report","decision","ontology"];

function fmt(v){
  if(!v)return "-";
  const s=String(v);
  return s.length>19?s.slice(0,19).replace("T"," "):s.replace("T"," ");
}

function splitTags(v){
  return String(v||"").split(",").map(s=>s.trim()).filter(Boolean);
}

function Field({label,children}){
  return <label style={{display:"grid",gridTemplateColumns:"110px 1fr",gap:10,alignItems:"center",fontSize:14,color:"var(--text-secondary)"}}>
    <span>{label}</span>{children}
  </label>;
}

const KIND_COLORS={
  identity:"#3b82f6",
  process:"#8b5cf6",
  split:"#ec4899",
  work:"#f97316",
  output:"#10b981",
  wiki_doc:"#22c55e",
  event:"#94a3b8",
  product:"#f59e0b",
  lot:"#0ea5e9",
  wafer:"#a855f7",
};
function kindColor(k){return KIND_COLORS[k]||"var(--accent)";}

function computeForceLayout(nodes,edges,width,height,nodeW,nodeH){
  const ids=nodes.map(n=>n.id);
  // deterministic initial positions: golden-angle spiral so layout is stable
  const positions=new Map();
  const phi=Math.PI*(3-Math.sqrt(5));
  const cx=width/2,cy=height/2;
  const R=Math.min(width,height)*0.42;
  nodes.forEach((n,i)=>{
    const r=R*Math.sqrt((i+0.5)/nodes.length);
    const a=i*phi;
    positions.set(n.id,{x:cx+r*Math.cos(a),y:cy+r*Math.sin(a)});
  });
  const n=nodes.length||1;
  const area=width*height;
  const k=Math.sqrt(area/n)*0.95; // ideal edge length — slightly longer for breathing room
  const iterations=240;
  const minSep=Math.max(nodeW,nodeH)*1.05;
  for(let iter=0;iter<iterations;iter++){
    const t=Math.max(0.02,1-iter/iterations);
    const force=new Map(ids.map(id=>[id,{fx:0,fy:0}]));
    // repulsion (all pairs) — stronger near-field push so boxes don't sit on top of each other
    for(let i=0;i<nodes.length;i++){
      for(let j=i+1;j<nodes.length;j++){
        const a=positions.get(nodes[i].id);
        const b=positions.get(nodes[j].id);
        let dx=a.x-b.x;
        let dy=a.y-b.y;
        let d2=dx*dx+dy*dy;
        if(d2<0.01){dx=((i*7+3)%5)-2.5;dy=((j*11+5)%5)-2.5;d2=dx*dx+dy*dy+0.01;}
        const d=Math.sqrt(d2);
        let f=(k*k)/d;
        if(d<minSep){f+=(minSep-d)*(minSep-d)*1.2;}
        const fx=(dx/d)*f,fy=(dy/d)*f;
        force.get(nodes[i].id).fx+=fx;force.get(nodes[i].id).fy+=fy;
        force.get(nodes[j].id).fx-=fx;force.get(nodes[j].id).fy-=fy;
      }
    }
    // attraction along edges
    for(const e of edges){
      const a=positions.get(e.source);
      const b=positions.get(e.target);
      if(!a||!b)continue;
      const dx=a.x-b.x,dy=a.y-b.y;
      const d=Math.sqrt(dx*dx+dy*dy)||0.01;
      const f=(d*d)/k;
      const fx=(dx/d)*f,fy=(dy/d)*f;
      force.get(e.source).fx-=fx;force.get(e.source).fy-=fy;
      force.get(e.target).fx+=fx;force.get(e.target).fy+=fy;
    }
    // centering pull (keeps disconnected nodes from drifting away)
    for(const nd of nodes){
      const p=positions.get(nd.id);
      force.get(nd.id).fx+=(cx-p.x)*0.008;
      force.get(nd.id).fy+=(cy-p.y)*0.008;
    }
    // apply
    const maxStep=Math.min(width,height)*0.18*t;
    for(const nd of nodes){
      const p=positions.get(nd.id);
      const f=force.get(nd.id);
      const m=Math.sqrt(f.fx*f.fx+f.fy*f.fy)||0.01;
      const step=Math.min(m,maxStep);
      p.x+=(f.fx/m)*step;
      p.y+=(f.fy/m)*step;
      p.x=Math.max(nodeW/2+8,Math.min(width-nodeW/2-8,p.x));
      p.y=Math.max(nodeH/2+8,Math.min(height-nodeH/2-8,p.y));
    }
  }
  // post-process: hard rectangle collision resolution so boxes never overlap
  const padX=18,padY=14;
  for(let iter=0;iter<120;iter++){
    let anyOverlap=false;
    for(let i=0;i<nodes.length;i++){
      for(let j=i+1;j<nodes.length;j++){
        const a=positions.get(nodes[i].id);
        const b=positions.get(nodes[j].id);
        let dx=b.x-a.x,dy=b.y-a.y;
        const overlapX=(nodeW+padX)-Math.abs(dx);
        const overlapY=(nodeH+padY)-Math.abs(dy);
        if(overlapX>0&&overlapY>0){
          anyOverlap=true;
          if(Math.abs(dx)<0.5)dx=((i*13+7)%7)-3;
          if(Math.abs(dy)<0.5)dy=((j*17+11)%7)-3;
          if(overlapX<overlapY){
            const push=(overlapX/2)+0.6;
            const sign=dx>=0?1:-1;
            a.x-=sign*push;b.x+=sign*push;
          }else{
            const push=(overlapY/2)+0.6;
            const sign=dy>=0?1:-1;
            a.y-=sign*push;b.y+=sign*push;
          }
        }
      }
    }
    for(const nd of nodes){
      const p=positions.get(nd.id);
      p.x=Math.max(nodeW/2+8,Math.min(width-nodeW/2-8,p.x));
      p.y=Math.max(nodeH/2+8,Math.min(height-nodeH/2-8,p.y));
    }
    if(!anyOverlap)break;
  }
  return positions;
}

const KG_NODE_W=152,KG_NODE_H=38;
const KG_ZOOM_MIN=0.3,KG_ZOOM_MAX=3,KG_VIEWPORT_H=560;

function KnowledgeGraphView({graph,viewportHeight=KG_VIEWPORT_H}){
  const nodes=Array.isArray(graph?.nodes)?graph.nodes:[];
  const edges=Array.isArray(graph?.edges)?graph.edges:[];
  const layout=useMemo(()=>{
    if(!nodes.length)return null;
    // canvas size scales with sqrt(N) so each node gets predictable breathing room
    const sqrtN=Math.sqrt(Math.max(1,nodes.length));
    const baseW=Math.max(880,Math.min(1600,320+sqrtN*220));
    const baseH=Math.max(560,Math.min(1100,200+sqrtN*200));
    const validEdges=edges.filter(e=>e&&e.source&&e.target);
    const positions=computeForceLayout(nodes,validEdges,baseW,baseH,KG_NODE_W,KG_NODE_H);
    return {positions,width:baseW,height:baseH,validEdges};
  },[graph]);

  const wrapRef=useRef(null);
  const [view,setView]=useState({scale:1,tx:0,ty:0});
  const viewRef=useRef(view);viewRef.current=view;
  const dragRef=useRef(null);

  const fitToContainer=()=>{
    const el=wrapRef.current;
    if(!el||!layout)return;
    const cw=el.clientWidth||layout.width;
    const ch=viewportHeight;
    const sx=cw/layout.width;
    const sy=ch/layout.height;
    const s=Math.min(sx,sy,1);
    const tx=(cw-layout.width*s)/2;
    const ty=(ch-layout.height*s)/2;
    setView({scale:s,tx,ty});
  };

  // fit on first layout
  useEffect(()=>{fitToContainer();/* eslint-disable-next-line react-hooks/exhaustive-deps */},[layout]);

  // wheel zoom — attach as non-passive so we can preventDefault page scroll
  useEffect(()=>{
    const el=wrapRef.current;
    if(!el)return;
    const onWheel=(e)=>{
      e.preventDefault();
      const rect=el.getBoundingClientRect();
      const cx=e.clientX-rect.left;
      const cy=e.clientY-rect.top;
      const v=viewRef.current;
      const factor=Math.exp(-e.deltaY*0.0015);
      const newScale=Math.max(KG_ZOOM_MIN,Math.min(KG_ZOOM_MAX,v.scale*factor));
      const ratio=newScale/v.scale;
      setView({scale:newScale,tx:cx-(cx-v.tx)*ratio,ty:cy-(cy-v.ty)*ratio});
    };
    el.addEventListener("wheel",onWheel,{passive:false});
    return ()=>el.removeEventListener("wheel",onWheel);
  },[]);

  const onMouseDown=(e)=>{
    if(e.button!==0)return;
    dragRef.current={x:e.clientX,y:e.clientY,tx:view.tx,ty:view.ty};
  };
  const onMouseMove=(e)=>{
    if(!dragRef.current)return;
    setView((v)=>({...v,tx:dragRef.current.tx+(e.clientX-dragRef.current.x),ty:dragRef.current.ty+(e.clientY-dragRef.current.y)}));
  };
  const onMouseUp=()=>{dragRef.current=null;};

  const zoomBy=(factor)=>{
    setView((v)=>{
      const cw=wrapRef.current?.clientWidth||layout?.width||800;
      const ch=viewportHeight;
      const cx=cw/2,cy=ch/2;
      const newScale=Math.max(KG_ZOOM_MIN,Math.min(KG_ZOOM_MAX,v.scale*factor));
      const ratio=newScale/v.scale;
      return {scale:newScale,tx:cx-(cx-v.tx)*ratio,ty:cy-(cy-v.ty)*ratio};
    });
  };

  if(!nodes.length){
    return <div style={{padding:28,textAlign:"center",color:"var(--text-secondary)"}}>그래프 노드가 없습니다. Admin은 Bootstrap 또는 Graph Rebuild 로 시작하세요.</div>;
  }
  const {positions,width,height,validEdges}=layout;
  const NODE_W=KG_NODE_W,NODE_H=KG_NODE_H;
  const ctrlBtn={padding:"3px 9px",borderRadius:5,border:"1px solid var(--border)",background:"var(--bg-secondary)",color:"var(--text-primary)",fontSize:13,cursor:"pointer",fontWeight:700};
  return <div style={{position:"relative",border:"1px solid var(--border)",borderRadius:8,overflow:"hidden",background:"var(--bg-primary)"}}>
    <div style={{position:"absolute",top:8,right:10,zIndex:2,display:"flex",gap:6,alignItems:"center",background:"var(--bg-secondary)",border:"1px solid var(--border)",borderRadius:6,padding:"4px 6px",boxShadow:"0 2px 6px rgba(0,0,0,0.15)"}}>
      <button type="button" onClick={()=>zoomBy(1/1.25)} style={ctrlBtn} title="축소">−</button>
      <span style={{fontSize:12,fontFamily:"monospace",color:"var(--text-secondary)",minWidth:42,textAlign:"center"}}>{Math.round(view.scale*100)}%</span>
      <button type="button" onClick={()=>zoomBy(1.25)} style={ctrlBtn} title="확대">+</button>
      <button type="button" onClick={fitToContainer} style={ctrlBtn} title="전체 화면에 맞춤">맞춤</button>
      <button type="button" onClick={()=>setView({scale:1,tx:0,ty:0})} style={ctrlBtn} title="100% 원본 비율">1:1</button>
    </div>
    <div style={{position:"absolute",bottom:8,left:10,zIndex:2,fontSize:11,color:"var(--text-secondary)",background:"var(--bg-secondary)",border:"1px solid var(--border)",borderRadius:5,padding:"3px 7px",opacity:0.92,pointerEvents:"none"}}>
      휠 = 줌 · 드래그 = 이동 · 노드 hover = 상세
    </div>
    <div
      ref={wrapRef}
      style={{width:"100%",height:viewportHeight,cursor:dragRef.current?"grabbing":"grab",overflow:"hidden",position:"relative",userSelect:"none"}}
      onMouseDown={onMouseDown}
      onMouseMove={onMouseMove}
      onMouseUp={onMouseUp}
      onMouseLeave={onMouseUp}
    >
      <svg width="100%" height="100%" style={{display:"block"}}>
        <defs>
          <marker id="kg-arrow" viewBox="0 0 10 10" refX="9" refY="5" markerWidth="7" markerHeight="7" orient="auto-start-reverse">
            <path d="M 0 0 L 10 5 L 0 10 z" fill="var(--text-secondary)" opacity="0.8"/>
          </marker>
          <filter id="kg-node-shadow" x="-20%" y="-20%" width="140%" height="140%">
            <feDropShadow dx="0" dy="1" stdDeviation="1.2" floodOpacity="0.18"/>
          </filter>
        </defs>
        <g transform={`translate(${view.tx} ${view.ty}) scale(${view.scale})`}>
          <rect x="0" y="0" width={width} height={height} fill="transparent"/>
          {validEdges.map((e,i)=>{
            const s=positions.get(e.source);
            const t=positions.get(e.target);
            if(!s||!t)return null;
            const dx=t.x-s.x,dy=t.y-s.y;
            const dist=Math.sqrt(dx*dx+dy*dy)||1;
            const ux=dx/dist,uy=dy/dist;
            // clip endpoints to the rectangle border (axis-aligned box-ray intersection)
            const halfW=NODE_W/2,halfH=NODE_H/2;
            const clipScale=(hx,hy)=>{
              const txx=hx/Math.max(1e-3,Math.abs(ux));
              const tyy=hy/Math.max(1e-3,Math.abs(uy));
              return Math.min(txx,tyy);
            };
            const scale=clipScale(halfW+2,halfH+2);
            const sx=s.x+ux*scale;
            const sy=s.y+uy*scale;
            const tx=t.x-ux*scale;
            const ty=t.y-uy*scale;
            const mx=(sx+tx)/2;
            const my=(sy+ty)/2;
            const px=-uy*10,py=ux*10;
            const labelText=String(e.relation||e.label||"");
            const labelW=labelText?Math.max(28,labelText.length*6.2+12):0;
            return <g key={(e.edge_id||(e.source+"-"+e.target+"-"+i))}>
              <line x1={sx} y1={sy} x2={tx} y2={ty} stroke="var(--text-secondary)" strokeWidth="1.25" opacity="0.55" markerEnd="url(#kg-arrow)"/>
              {labelText&&<g>
                <rect x={mx+px-labelW/2} y={my+py-8} width={labelW} height={15} rx={4} fill="var(--bg-primary)" stroke="var(--border)" strokeWidth="0.8" opacity="0.92"/>
                <text x={mx+px} y={my+py+3} textAnchor="middle" fontSize="10" fill="var(--text-secondary)" style={{pointerEvents:"none"}}>{labelText}</text>
              </g>}
            </g>;
          })}
          {nodes.map(n=>{
            const p=positions.get(n.id);
            if(!p)return null;
            const k=n.kind||n.type||"node";
            const color=kindColor(k);
            const label=n.label||n.id;
            const labelShort=label.length>20?label.slice(0,20)+"…":label;
            return <g key={"n-"+n.id} transform={`translate(${p.x}, ${p.y})`} filter="url(#kg-node-shadow)">
              <title>{label} ({k}){n.summary?" — "+n.summary:""}</title>
              <rect x={-NODE_W/2} y={-NODE_H/2} width={NODE_W} height={NODE_H} rx={7} fill="var(--bg-secondary)" stroke={color} strokeWidth="1.5"/>
              <rect x={-NODE_W/2} y={-NODE_H/2} width={6} height={NODE_H} fill={color} rx={2}/>
              <text x={-NODE_W/2+14} y={-2} fontSize="11" fontWeight="700" fill="var(--text-primary)">{labelShort}</text>
              <text x={-NODE_W/2+14} y={12} fontSize="9" fill="var(--text-secondary)" fontFamily="monospace">{k}</text>
            </g>;
          })}
        </g>
      </svg>
    </div>
    <div style={{fontSize:12,color:"var(--text-secondary)",padding:"6px 10px",display:"flex",gap:14,flexWrap:"wrap",borderTop:"1px solid var(--border)",background:"var(--bg-secondary)"}}>
      <span>{nodes.length} 노드 · {validEdges.length} 엣지 · force-directed + 충돌 해소</span>
      {Array.from(new Set(nodes.map(n=>n.kind||n.type||"node"))).slice(0,10).map(k=>(
        <span key={k} style={{display:"inline-flex",alignItems:"center",gap:4}}>
          <span style={{width:10,height:10,borderRadius:2,background:kindColor(k)}}/>{k}
        </span>
      ))}
    </div>
  </div>;
}

function inputStyle(multiline=false){
  return {
    width:"100%",
    padding:multiline?"10px 12px":"7px 10px",
    borderRadius:6,
    border:"1px solid var(--border)",
    background:"var(--bg-primary)",
    color:"var(--text-primary)",
    fontSize:14,
    outline:"none",
    fontFamily:multiline?"ui-monospace,SFMono-Regular,Menlo,Consolas,monospace":"inherit",
    boxSizing:"border-box",
  };
}

export default function My_Knowledge({user,embedded=false}){
  const canManage=canManagePage(user,"diagnosis")||canManagePage(user,"knowledge");
  const [tab,setTab]=useState("wiki");
  const [status,setStatus]=useState(null);
  const [docs,setDocs]=useState([]);
  const [events,setEvents]=useState([]);
  const [graph,setGraph]=useState(null);
  const [activeOntology,setActiveOntology]=useState(null);
  const [ontologyPreview,setOntologyPreview]=useState(null);
  const [ontologyBusy,setOntologyBusy]=useState(false);
  const [docDraft,setDocDraft]=useState(null);
  const [docBusy,setDocBusy]=useState(false);
  const [docManualMode,setDocManualMode]=useState(false);
  const [searchQ,setSearchQ]=useState("");
  const [searchResults,setSearchResults]=useState([]);
  const [selectedDoc,setSelectedDoc]=useState(null);
  const [msg,setMsg]=useState("");
  const [busy,setBusy]=useState(false);
  const [form,setForm]=useState({
    doc_id:"",
    kind:"manual",
    title:"",
    summary:"",
    product:"",
    root_lot_id:"",
    wafer_id:"",
    tags:"",
    body:"",
  });

  const load=()=>{
    setBusy(true);
    Promise.all([
      sf(API+"/status"),
      sf(API+"/wiki?limit=200"),
      sf(API+"/events?limit=100"),
      sf(API+"/graph"),
      sf(API+"/ontology").catch(()=>null),
    ]).then(([st,w,e,g,o])=>{
      setStatus(st);
      setDocs(w.docs||[]);
      setEvents(e.events||[]);
      setGraph(g);
      setActiveOntology(o||null);
      setMsg("");
    }).catch(e=>setMsg(e.message||"로드 실패"))
      .finally(()=>setBusy(false));
  };

  useEffect(()=>{load();},[]);

  const counts=status?.counts||{};
  const ontology=activeOntology||status?.ontology||{};
  const ontologySource=activeOntology?.source||(status?.ontology?.source)||"default_ontology";
  const nodeKindCounts=useMemo(()=>{
    const out={};
    for(const n of graph?.nodes||[])out[n.kind]=(out[n.kind]||0)+1;
    return out;
  },[graph]);

  const bootstrap=()=>{
    if(!canManage)return;
    setBusy(true);
    sf(API+"/bootstrap",{method:"POST"}).then(()=>{setMsg("Knowledge Vault bootstrap 완료");load();})
      .catch(e=>setMsg(e.message||"bootstrap 실패")).finally(()=>setBusy(false));
  };

  const rebuildGraph=()=>{
    if(!canManage)return;
    setBusy(true);
    sf(API+"/graph/rebuild",{method:"POST"}).then(d=>{setGraph(d.graph);setMsg("Graph rebuild 완료");})
      .catch(e=>setMsg(e.message||"graph rebuild 실패")).finally(()=>setBusy(false));
  };

  const generateAiOntology=()=>{
    if(!canManage)return;
    setOntologyBusy(true);
    setMsg("");
    sf(API+"/ontology/ai-generate",{method:"POST"})
      .then(d=>{
        if(d?.ok){
          setOntologyPreview(d.ontology||{nodes:[],edges:[]});
          setMsg(`AI 분류 완료: ${d.ontology?.nodes?.length||0} concept · ${d.ontology?.edges?.length||0} relation`);
        }else{
          setMsg("AI 분류 실패: "+(d?.error||"unknown"));
        }
      })
      .catch(e=>setMsg(e.message||"AI 분류 실패"))
      .finally(()=>setOntologyBusy(false));
  };

  const saveAiOntology=()=>{
    if(!canManage||!ontologyPreview)return;
    setOntologyBusy(true);
    setMsg("");
    sf(API+"/ontology/save",{method:"POST",headers:{"Content-Type":"application/json"},body:JSON.stringify(ontologyPreview)})
      .then(d=>{
        setActiveOntology({source:"ai_ontology",nodes:ontologyPreview.nodes||[],edges:ontologyPreview.edges||[]});
        setOntologyPreview(null);
        if(d?.graph)setGraph(d.graph);
        setMsg("AI ontology 저장 완료");
      })
      .catch(e=>setMsg(e.message||"AI ontology 저장 실패"))
      .finally(()=>setOntologyBusy(false));
  };

  const clearAiOntology=()=>{
    if(!canManage)return;
    if(!confirm("저장된 AI ontology 를 지우고 기본 분류로 돌아갑니다. 계속할까요?"))return;
    setOntologyBusy(true);
    setMsg("");
    sf(API+"/ontology/clear",{method:"POST"})
      .then(d=>{
        setOntologyPreview(null);
        if(d?.graph)setGraph(d.graph);
        sf(API+"/ontology").then(o=>setActiveOntology(o||null)).catch(()=>{});
        setMsg(d?.cleared?"기본 ontology 로 복원":"저장된 AI ontology 가 없습니다");
      })
      .catch(e=>setMsg(e.message||"clear 실패"))
      .finally(()=>setOntologyBusy(false));
  };

  const runSearch=()=>{
    const q=searchQ.trim();
    if(!q)return;
    setBusy(true);
    sf(API+"/search?q="+encodeURIComponent(q)+"&scope=all&limit=50").then(d=>setSearchResults(d.results||[]))
      .catch(e=>setMsg(e.message||"검색 실패")).finally(()=>setBusy(false));
  };

  const openDoc=(docId)=>{
    if(!docId)return;
    setBusy(true);
    sf(API+"/wiki/doc?doc_id="+encodeURIComponent(docId)).then(d=>{setSelectedDoc(d);setTab("doc");})
      .catch(e=>setMsg(e.message||"문서 로드 실패")).finally(()=>setBusy(false));
  };

  const saveDoc=()=>{
    if(!canManage)return;
    if(!form.title.trim()&&!form.doc_id.trim()){setMsg("title 또는 doc_id가 필요합니다.");return;}
    const body={
      doc_id:form.doc_id,
      kind:form.kind,
      title:form.title,
      summary:form.summary,
      body:form.body,
      entity:{product:form.product,root_lot_id:form.root_lot_id,wafer_id:form.wafer_id},
      tags:splitTags(form.tags),
    };
    setBusy(true);
    sf(API+"/wiki/upsert",{method:"POST",headers:{"Content-Type":"application/json"},body:JSON.stringify(body)})
      .then(d=>{setMsg("문서 저장 완료: "+(d.doc?.doc_id||""));setForm({...form,doc_id:d.doc?.doc_id||form.doc_id});load();})
      .catch(e=>setMsg(e.message||"문서 저장 실패")).finally(()=>setBusy(false));
  };

  const aiDraftDoc=()=>{
    if(!canManage)return;
    if(!form.body.trim()){setMsg("body 가 필요합니다 (AI 가 본문을 읽고 분류합니다).");return;}
    setDocBusy(true);
    setMsg("");
    sf(API+"/wiki/ai-draft",{method:"POST",headers:{"Content-Type":"application/json"},body:JSON.stringify({doc_id:form.doc_id.trim(),tags:splitTags(form.tags),body:form.body})})
      .then(d=>{
        if(d?.ok){
          setDocDraft(d);
          setMsg("AI 분석 완료. 미리보기 확인 후 저장하세요.");
        }else{
          setMsg("AI 분석 실패: "+(d?.error||"unknown"));
        }
      })
      .catch(e=>setMsg(e.message||"AI 분석 실패"))
      .finally(()=>setDocBusy(false));
  };

  const aiUpsertDoc=()=>{
    if(!canManage)return;
    if(!form.body.trim()){setMsg("body 가 필요합니다.");return;}
    setDocBusy(true);
    setMsg("");
    sf(API+"/wiki/ai-upsert",{method:"POST",headers:{"Content-Type":"application/json"},body:JSON.stringify({doc_id:form.doc_id.trim(),tags:splitTags(form.tags),body:form.body})})
      .then(d=>{
        if(d?.ok){
          setMsg("AI 저장 완료: "+(d.doc?.doc_id||""));
          setDocDraft(null);
          setForm({...form,doc_id:d.doc?.doc_id||form.doc_id});
          load();
        }else{
          setMsg("AI 저장 실패: "+(d?.error||"unknown"));
        }
      })
      .catch(e=>setMsg(e.message||"AI 저장 실패"))
      .finally(()=>setDocBusy(false));
  };

  const card={background:"var(--bg-secondary)",border:"1px solid var(--border)",borderRadius:10,padding:14};
  const btn=(active=false)=>({padding:"7px 12px",borderRadius:6,border:"1px solid "+(active?"var(--accent)":"var(--border)"),background:active?"var(--accent-glow)":"var(--bg-secondary)",color:active?"var(--accent)":"var(--text-primary)",fontSize:14,fontWeight:700,cursor:"pointer"});

  const rootStyle=embedded
    ? {display:"flex",flexDirection:"column",background:"var(--bg-primary)",color:"var(--text-primary)",fontFamily:"'Pretendard',sans-serif",border:"1px solid var(--border)",borderRadius:10,overflow:"hidden"}
    : {height:"calc(100vh - 52px)",display:"flex",flexDirection:"column",background:"var(--bg-primary)",color:"var(--text-primary)",fontFamily:"'Pretendard',sans-serif"};

  return <div style={rootStyle}>
    <div style={{padding:embedded?"12px 14px":"18px 22px",borderBottom:"1px solid var(--border)",background:"linear-gradient(135deg,var(--bg-secondary),var(--bg-primary))"}}>
      <div style={{display:"flex",alignItems:"center",gap:10}}>
        <div>
          <div style={{fontSize:embedded?18:22,fontWeight:900}}>Knowledge Vault</div>
          <div style={{fontSize:14,color:"var(--text-secondary)",marginTop:4}}>raw events, wiki pages, graph, search index 골격</div>
        </div>
        <div style={{marginLeft:"auto",display:"flex",gap:8,alignItems:"center"}}>
          {canManage&&<button onClick={bootstrap} disabled={busy} style={btn(false)}>Bootstrap</button>}
          {canManage&&<button onClick={rebuildGraph} disabled={busy} style={btn(false)}>Graph Rebuild</button>}
          <button onClick={load} disabled={busy} style={btn(false)}>새로고침</button>
        </div>
      </div>
      {msg&&<div style={{marginTop:10,padding:"8px 10px",borderRadius:6,border:"1px solid var(--border)",background:"var(--bg-card)",fontSize:13,color:msg.includes("실패")||msg.includes("필요")?"#ef4444":"var(--text-secondary)"}}>{msg}</div>}
    </div>

    <div style={{display:"grid",gridTemplateColumns:"repeat(4,minmax(120px,1fr))",gap:10,padding:embedded?"10px 14px":"14px 22px",borderBottom:"1px solid var(--border)"}}>
      <div style={card}><div style={{fontSize:12,color:"var(--text-secondary)"}}>Wiki Docs</div><div style={{fontSize:24,fontWeight:900}}>{counts.docs??0}</div></div>
      <div style={card}><div style={{fontSize:12,color:"var(--text-secondary)"}}>Raw Events</div><div style={{fontSize:24,fontWeight:900}}>{counts.events??0}</div></div>
      <div style={card}><div style={{fontSize:12,color:"var(--text-secondary)"}}>Graph Nodes</div><div style={{fontSize:24,fontWeight:900}}>{counts.graph_nodes??graph?.nodes?.length??0}</div></div>
      <div style={card}><div style={{fontSize:12,color:"var(--text-secondary)"}}>Graph Edges</div><div style={{fontSize:24,fontWeight:900}}>{counts.graph_edges??graph?.edges?.length??0}</div></div>
    </div>

    <div style={{display:"flex",gap:8,padding:embedded?"8px 14px":"10px 22px",borderBottom:"1px solid var(--border)",background:"var(--bg-secondary)",alignItems:"center",flexWrap:"wrap"}}>
      {["wiki","events","search","graph","ontology","write","doc"].filter(k=>k!=="write"||canManage).map(k=>
        <button key={k} onClick={()=>setTab(k)} style={btn(tab===k)}>{({wiki:"Wiki",events:"Raw Events",search:"Search",graph:"Graph",ontology:"Ontology",write:"Write",doc:"Doc"})[k]}</button>
      )}
      {!canManage&&<span style={{marginLeft:"auto",fontSize:13,color:"var(--text-secondary)"}}>읽기 모드 · 쓰기는 admin 또는 knowledge page_admin</span>}
    </div>

    <div style={{flex:1,minHeight:0,overflow:"auto",padding:embedded?14:22,maxHeight:embedded?680:undefined}}>
      {tab==="wiki"&&<div style={{...card,padding:0,overflow:"hidden"}}>
        <table style={{width:"100%",borderCollapse:"collapse",fontSize:14}}>
          <thead><tr style={{background:"var(--bg-tertiary)",color:"var(--text-secondary)"}}>
            <th style={{padding:10,textAlign:"left"}}>kind</th><th style={{padding:10,textAlign:"left"}}>title</th><th style={{padding:10,textAlign:"left"}}>entity</th><th style={{padding:10,textAlign:"left"}}>updated</th><th style={{padding:10,textAlign:"left"}}>path</th>
          </tr></thead>
          <tbody>{docs.map(d=>{
            const ent=d.entity||{};
            return <tr key={d.doc_id} onClick={()=>openDoc(d.doc_id)} style={{cursor:"pointer"}}>
              <td style={{padding:10,borderTop:"1px solid var(--border)",color:"var(--accent)",fontWeight:700}}>{d.kind}</td>
              <td style={{padding:10,borderTop:"1px solid var(--border)"}}><b>{d.title||d.doc_id}</b><div style={{fontSize:12,color:"var(--text-secondary)"}}>{d.summary}</div></td>
              <td style={{padding:10,borderTop:"1px solid var(--border)",fontFamily:"monospace",fontSize:12}}>{[ent.product,ent.root_lot_id,ent.wafer_id].filter(Boolean).join(" / ")||"-"}</td>
              <td style={{padding:10,borderTop:"1px solid var(--border)",fontFamily:"monospace",fontSize:12}}>{fmt(d.updated_at)}</td>
              <td style={{padding:10,borderTop:"1px solid var(--border)",fontFamily:"monospace",fontSize:12,color:"var(--text-secondary)"}}>{d.path}</td>
            </tr>;
          })}</tbody>
        </table>
        {!docs.length&&<div style={{padding:28,textAlign:"center",color:"var(--text-secondary)"}}>Wiki 문서가 없습니다. Admin은 Bootstrap 또는 Write로 시작하세요.</div>}
      </div>}

      {tab==="events"&&<div style={{display:"grid",gap:10}}>
        {events.map(e=>{
          const ent=e.entity||{};
          return <div key={e.event_id} style={card}>
            <div style={{display:"flex",gap:8,alignItems:"center"}}>
              <span style={{fontSize:12,padding:"2px 7px",borderRadius:999,background:"var(--accent-glow)",color:"var(--accent)",fontWeight:800}}>{e.source_type}</span>
              <b>{e.title||e.event_id}</b>
              <span style={{marginLeft:"auto",fontSize:12,color:"var(--text-secondary)",fontFamily:"monospace"}}>{fmt(e.created_at)} · {e.actor||"-"}</span>
            </div>
            <div style={{fontSize:14,color:"var(--text-secondary)",marginTop:8}}>{e.summary}</div>
            <div style={{fontSize:12,color:"var(--text-secondary)",marginTop:8,fontFamily:"monospace"}}>{[ent.product,ent.root_lot_id,ent.wafer_id].filter(Boolean).join(" / ")||"-"} · {e.raw_path}</div>
          </div>;
        })}
        {!events.length&&<div style={card}>Raw event가 없습니다.</div>}
      </div>}

      {tab==="search"&&<div>
        <div style={{display:"flex",gap:8,marginBottom:14}}>
          <input value={searchQ} onChange={e=>setSearchQ(e.target.value)} onKeyDown={e=>e.key==="Enter"&&runSearch()} placeholder="wiki/event 검색어"
            style={{...inputStyle(),maxWidth:520}}/>
          <button onClick={runSearch} style={btn(false)}>검색</button>
        </div>
        <div style={{display:"grid",gap:10}}>
          {searchResults.map(r=><div key={r.result_type+":"+r.id} style={card}>
            <div style={{display:"flex",gap:8,alignItems:"center"}}>
              <span style={{fontSize:12,color:"var(--accent)",fontWeight:800}}>{r.result_type}</span>
              <b>{r.title}</b>
              <span style={{marginLeft:"auto",fontSize:12,color:"var(--text-secondary)"}}>score {r.score}</span>
            </div>
            <div style={{fontSize:14,color:"var(--text-secondary)",marginTop:8}}>{r.snippet}</div>
            <div style={{fontSize:12,color:"var(--text-secondary)",marginTop:8,fontFamily:"monospace"}}>{r.path}</div>
          </div>)}
        </div>
      </div>}

      {tab==="graph"&&<div style={{display:"grid",gridTemplateColumns:"260px 1fr",gap:14}}>
        <div style={card}>
          <div style={{fontWeight:900,marginBottom:10}}>Node kinds</div>
          {Object.entries(nodeKindCounts).map(([k,v])=><div key={k} style={{display:"flex",justifyContent:"space-between",fontSize:14,padding:"5px 0",borderBottom:"1px solid var(--border)"}}><span>{k}</span><b>{v}</b></div>)}
          {!Object.keys(nodeKindCounts).length&&<div style={{fontSize:13,color:"var(--text-secondary)"}}>그래프 노드가 없습니다.</div>}
        </div>
        <div style={{display:"grid",gap:12}}>
          <div style={{...card,padding:0,overflow:"hidden"}}>
            <div style={{padding:"10px 12px",borderBottom:"1px solid var(--border)",background:"var(--bg-tertiary)",display:"flex",alignItems:"center",gap:8,fontSize:13}}>
              <b>연결 그래프</b>
              <span style={{color:"var(--text-secondary)"}}>kind 별로 노드를 배치하고 edge 를 곡선으로 연결합니다.</span>
              <span style={{marginLeft:"auto",color:"var(--text-secondary)",fontFamily:"monospace",fontSize:12}}>{graph?.nodes?.length||0} nodes · {graph?.edges?.length||0} edges</span>
            </div>
            <KnowledgeGraphView graph={graph} />
          </div>
          <div style={{...card,padding:0,overflow:"hidden"}}>
            <div style={{padding:"8px 12px",borderBottom:"1px solid var(--border)",background:"var(--bg-tertiary)",fontSize:13}}><b>Edge 목록</b> <span style={{color:"var(--text-secondary)",marginLeft:6}}>그래프와 같은 edge 를 표로 다시 확인합니다.</span></div>
            <table style={{width:"100%",borderCollapse:"collapse",fontSize:13}}>
              <thead><tr style={{background:"var(--bg-tertiary)",color:"var(--text-secondary)"}}><th style={{padding:9,textAlign:"left"}}>source</th><th style={{padding:9,textAlign:"left"}}>relation</th><th style={{padding:9,textAlign:"left"}}>target</th></tr></thead>
              <tbody>{(graph?.edges||[]).slice(0,300).map(e=><tr key={e.edge_id}><td style={{padding:9,borderTop:"1px solid var(--border)",fontFamily:"monospace"}}>{e.source}</td><td style={{padding:9,borderTop:"1px solid var(--border)",color:"var(--accent)",fontWeight:700}}>{e.relation}</td><td style={{padding:9,borderTop:"1px solid var(--border)",fontFamily:"monospace"}}>{e.target}</td></tr>)}</tbody>
            </table>
            {!(graph?.edges||[]).length&&<div style={{padding:18,textAlign:"center",color:"var(--text-secondary)"}}>Edge 가 없습니다.</div>}
          </div>
        </div>
      </div>}

      {tab==="ontology"&&<div style={{display:"grid",gap:14}}>
        <div style={{...card,display:"flex",gap:10,alignItems:"center",flexWrap:"wrap"}}>
          <div style={{display:"flex",flexDirection:"column",gap:2}}>
            <div style={{fontSize:14,fontWeight:900}}>Ontology 분류</div>
            <div style={{fontSize:12,color:"var(--text-secondary)"}}>현재 적용 중: <b style={{color:ontologySource==="ai_ontology"?"#10b981":"var(--accent)"}}>{ontologySource}</b> · {(ontology.nodes||[]).length} concept · {(ontology.edges||[]).length} relation</div>
          </div>
          <div style={{marginLeft:"auto",display:"flex",gap:8,flexWrap:"wrap"}}>
            {canManage&&<button onClick={generateAiOntology} disabled={ontologyBusy} style={{...btn(false),background:"var(--accent-glow)",color:"var(--accent)"}}>{ontologyBusy?"AI 호출 중":"AI 로 분류 생성"}</button>}
            {canManage&&ontologyPreview&&<button onClick={saveAiOntology} disabled={ontologyBusy} style={{...btn(true),background:"#10b981",borderColor:"#10b981",color:"#fff"}}>Preview 저장</button>}
            {canManage&&ontologyPreview&&<button onClick={()=>setOntologyPreview(null)} disabled={ontologyBusy} style={btn(false)}>Preview 취소</button>}
            {canManage&&ontologySource==="ai_ontology"&&<button onClick={clearAiOntology} disabled={ontologyBusy} style={btn(false)}>기본 분류로 복원</button>}
            {!canManage&&<span style={{fontSize:12,color:"var(--text-secondary)"}}>읽기 모드</span>}
          </div>
        </div>
        {ontologyPreview&&<div style={{...card,padding:0,overflow:"hidden",borderColor:"#10b981"}}>
          <div style={{padding:"8px 12px",borderBottom:"1px solid var(--border)",background:"rgba(16,185,129,0.1)",fontSize:13,display:"flex",alignItems:"center",gap:10}}>
            <b style={{color:"#10b981"}}>AI 생성 Preview</b>
            <span style={{color:"var(--text-secondary)"}}>{(ontologyPreview.nodes||[]).length} concept · {(ontologyPreview.edges||[]).length} relation · 저장 전까지 적용 안 됨</span>
          </div>
          <KnowledgeGraphView graph={ontologyPreview} viewportHeight={460}/>
        </div>}
        <div style={{...card,padding:0,overflow:"hidden"}}>
          <div style={{padding:"10px 12px",borderBottom:"1px solid var(--border)",background:"var(--bg-tertiary)",fontSize:13}}><b>현재 적용 Ontology</b> <span style={{color:"var(--text-secondary)",marginLeft:6}}>concept 노드와 의미 관계 edge 를 시각화합니다. 휠로 확대/축소, 드래그로 이동.</span></div>
          <KnowledgeGraphView graph={ontology} />
        </div>
        <div style={{display:"grid",gridTemplateColumns:"1fr 1fr",gap:14}}>
          <div style={card}><div style={{fontWeight:900,marginBottom:10}}>Concept nodes ({(ontology.nodes||[]).length})</div>{(ontology.nodes||[]).map(n=><div key={n.id} style={{fontSize:14,padding:"6px 0",borderBottom:"1px solid var(--border)"}}><b>{n.label}</b> <span style={{color:"var(--text-secondary)"}}>({n.kind})</span></div>)}{!(ontology.nodes||[]).length&&<div style={{fontSize:13,color:"var(--text-secondary)"}}>concept 노드 없음.</div>}</div>
          <div style={card}><div style={{fontWeight:900,marginBottom:10}}>Concept edges ({(ontology.edges||[]).length})</div>{(ontology.edges||[]).map((e,i)=><div key={i} style={{fontSize:14,padding:"6px 0",borderBottom:"1px solid var(--border)",fontFamily:"monospace"}}>{e.source} -[{e.relation}]-&gt; {e.target}</div>)}{!(ontology.edges||[]).length&&<div style={{fontSize:13,color:"var(--text-secondary)"}}>concept edge 없음.</div>}</div>
        </div>
      </div>}

      {tab==="write"&&canManage&&<div style={{display:"grid",gap:14,maxWidth:980}}>
        <div style={card}>
          <div style={{display:"flex",alignItems:"center",gap:10,marginBottom:14,flexWrap:"wrap"}}>
            <div>
              <div style={{fontSize:17,fontWeight:900}}>Wiki 문서 생성/수정</div>
              <div style={{fontSize:13,color:"var(--text-secondary)",marginTop:2}}>{docManualMode?"수동 입력 모드 — 모든 필드를 직접 채웁니다.":"본문만 적으면 사내 AI 가 title/summary/kind/entity/related docs 를 채우고 ontology 그래프에 반영합니다."}</div>
            </div>
            <div style={{marginLeft:"auto",display:"flex",gap:8,alignItems:"center"}}>
              <span style={{fontSize:12,color:"var(--text-secondary)"}}>입력 방식</span>
              <button type="button" onClick={()=>{setDocManualMode(false);setDocDraft(null);}} style={btn(!docManualMode)}>AI 자동</button>
              <button type="button" onClick={()=>{setDocManualMode(true);setDocDraft(null);}} style={btn(docManualMode)}>수동</button>
            </div>
          </div>

          {!docManualMode&&<div style={{display:"grid",gap:10}}>
            <Field label="doc_id"><input value={form.doc_id} onChange={e=>setForm({...form,doc_id:e.target.value})} placeholder="비우면 본문 기준으로 자동 생성" style={inputStyle()}/></Field>
            <Field label="tags"><input value={form.tags} onChange={e=>setForm({...form,tags:e.target.value})} placeholder="comma separated (AI 가 본문 키워드 자동 추가)" style={inputStyle()}/></Field>
            <Field label="body (본문 — Markdown OK)"><textarea value={form.body} onChange={e=>setForm({...form,body:e.target.value})} rows={14} placeholder="본문을 그대로 적으세요. AI 가 읽고 title, summary, kind, product/lot/wafer, 다른 wiki 문서와의 관계를 채웁니다." style={inputStyle(true)}/></Field>
            <div style={{display:"flex",gap:8,justifyContent:"flex-end",flexWrap:"wrap"}}>
              <button onClick={aiDraftDoc} disabled={docBusy||!form.body.trim()} style={btn(false)}>{docBusy?"AI 분석 중":"AI 분석 (미리보기)"}</button>
              <button onClick={aiUpsertDoc} disabled={docBusy||!form.body.trim()} style={{...btn(true),background:"var(--accent)",color:"#fff",borderColor:"var(--accent)"}}>{docBusy?"저장 중":"AI 분석 + 저장"}</button>
            </div>
          </div>}

          {docManualMode&&<div style={{display:"grid",gap:10}}>
            <Field label="doc_id"><input value={form.doc_id} onChange={e=>setForm({...form,doc_id:e.target.value})} placeholder="비우면 자동 생성" style={inputStyle()}/></Field>
            <Field label="kind"><select value={form.kind} onChange={e=>setForm({...form,kind:e.target.value})} style={inputStyle()}>{KIND_OPTIONS.map(k=><option key={k} value={k}>{k}</option>)}</select></Field>
            <Field label="title"><input value={form.title} onChange={e=>setForm({...form,title:e.target.value})} style={inputStyle()}/></Field>
            <Field label="summary"><input value={form.summary} onChange={e=>setForm({...form,summary:e.target.value})} style={inputStyle()}/></Field>
            <Field label="product"><input value={form.product} onChange={e=>setForm({...form,product:e.target.value})} style={inputStyle()}/></Field>
            <Field label="root_lot_id"><input value={form.root_lot_id} onChange={e=>setForm({...form,root_lot_id:e.target.value})} style={inputStyle()}/></Field>
            <Field label="wafer_id"><input value={form.wafer_id} onChange={e=>setForm({...form,wafer_id:e.target.value})} style={inputStyle()}/></Field>
            <Field label="tags"><input value={form.tags} onChange={e=>setForm({...form,tags:e.target.value})} placeholder="comma separated" style={inputStyle()}/></Field>
            <Field label="body"><textarea value={form.body} onChange={e=>setForm({...form,body:e.target.value})} rows={12} style={inputStyle(true)}/></Field>
            <div style={{display:"flex",justifyContent:"flex-end"}}><button onClick={saveDoc} disabled={busy} style={{...btn(true),background:"var(--accent)",color:"#fff"}}>저장</button></div>
          </div>}
        </div>

        {!docManualMode&&docDraft&&<div style={{...card,borderColor:"var(--accent)"}}>
          <div style={{display:"flex",alignItems:"center",gap:10,marginBottom:10,flexWrap:"wrap"}}>
            <div style={{fontSize:14,fontWeight:900,color:"var(--accent)"}}>AI 분석 결과 (미리보기)</div>
            <span style={{fontSize:12,color:"var(--text-secondary)"}}>저장 전까지는 wiki/그래프에 반영되지 않습니다.</span>
            <div style={{marginLeft:"auto",display:"flex",gap:8}}>
              <button onClick={aiUpsertDoc} disabled={docBusy} style={{...btn(true),background:"var(--accent)",color:"#fff",borderColor:"var(--accent)"}}>이대로 저장</button>
              <button onClick={()=>setDocDraft(null)} disabled={docBusy} style={btn(false)}>닫기</button>
            </div>
          </div>
          <div style={{display:"grid",gridTemplateColumns:"140px 1fr",gap:8,fontSize:14}}>
            <div style={{color:"var(--text-secondary)"}}>title</div><div><b>{docDraft.title||"(없음)"}</b></div>
            <div style={{color:"var(--text-secondary)"}}>summary</div><div>{docDraft.summary||"(없음)"}</div>
            <div style={{color:"var(--text-secondary)"}}>kind</div><div><span style={{padding:"2px 7px",borderRadius:999,background:"var(--accent-glow)",color:"var(--accent)",fontWeight:800,fontSize:12}}>{docDraft.kind}</span></div>
            <div style={{color:"var(--text-secondary)"}}>tags</div><div style={{display:"flex",gap:6,flexWrap:"wrap"}}>{(docDraft.tags||[]).map(t=><span key={t} style={{padding:"2px 7px",borderRadius:4,background:"var(--bg-tertiary)",fontSize:12}}>{t}</span>)}{!(docDraft.tags||[]).length&&<span style={{color:"var(--text-secondary)"}}>(없음)</span>}</div>
            <div style={{color:"var(--text-secondary)"}}>entity</div><div style={{fontFamily:"monospace",fontSize:13}}>{[docDraft.entity?.product,docDraft.entity?.root_lot_id,docDraft.entity?.wafer_id].filter(Boolean).join(" / ")||"(빈 entity)"}</div>
            <div style={{color:"var(--text-secondary)"}}>related docs</div><div style={{display:"grid",gap:4}}>{(docDraft.related_doc_ids||[]).map(rid=><div key={rid} style={{fontSize:13,fontFamily:"monospace"}}><b>{rid}</b> <span style={{color:"var(--accent)"}}>[{docDraft.relations?.[rid]||"relates_to"}]</span></div>)}{!(docDraft.related_doc_ids||[]).length&&<span style={{color:"var(--text-secondary)"}}>연결된 기존 문서 없음</span>}</div>
          </div>
        </div>}
      </div>}

      {tab==="doc"&&<div style={card}>
        {selectedDoc?<>
          <div style={{fontSize:12,color:"var(--accent)",fontWeight:800}}>{selectedDoc.kind} · {selectedDoc.path}</div>
          <div style={{fontSize:24,fontWeight:900,margin:"8px 0"}}>{selectedDoc.title||selectedDoc.doc_id}</div>
          <div style={{fontSize:14,color:"var(--text-secondary)",marginBottom:14}}>{selectedDoc.summary}</div>
          <pre style={{whiteSpace:"pre-wrap",fontSize:14,lineHeight:1.65,color:"var(--text-primary)",fontFamily:"ui-monospace,SFMono-Regular,Menlo,Consolas,monospace",background:"var(--bg-primary)",border:"1px solid var(--border)",borderRadius:8,padding:14,overflow:"auto"}}>{selectedDoc.body}</pre>
        </>:<div style={{color:"var(--text-secondary)"}}>문서를 선택하세요.</div>}
      </div>}
    </div>
  </div>;
}
