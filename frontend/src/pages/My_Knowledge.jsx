import { useEffect, useMemo, useState } from "react";
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

function KnowledgeGraphView({graph}){
  const nodes=Array.isArray(graph?.nodes)?graph.nodes:[];
  const edges=Array.isArray(graph?.edges)?graph.edges:[];
  if(!nodes.length){
    return <div style={{padding:28,textAlign:"center",color:"var(--text-secondary)"}}>그래프 노드가 없습니다. Admin은 Bootstrap 또는 Graph Rebuild 로 시작하세요.</div>;
  }
  const byKind=new Map();
  for(const n of nodes){
    const k=n.kind||n.type||"node";
    if(!byKind.has(k))byKind.set(k,[]);
    byKind.get(k).push(n);
  }
  const sortedKinds=Array.from(byKind.keys()).sort((a,b)=>byKind.get(b).length-byKind.get(a).length);
  const MAX_PER_KIND=8;
  const positions=new Map();
  const COL_W=Math.max(180,Math.min(240,Math.floor(900/Math.max(1,sortedKinds.length))));
  const ROW_H=58;
  const HDR=34;
  const PAD_X=20;
  const totalW=PAD_X*2+sortedKinds.length*COL_W;
  const maxItems=Math.max(...sortedKinds.map(k=>Math.min(byKind.get(k).length,MAX_PER_KIND)),1);
  const height=HDR+maxItems*ROW_H+24;
  sortedKinds.forEach((kind,idx)=>{
    const x=PAD_X+(idx+0.5)*COL_W;
    const items=byKind.get(kind).slice(0,MAX_PER_KIND);
    items.forEach((n,ni)=>{
      const y=HDR+(ni+0.5)*ROW_H;
      positions.set(n.id,{x,y,kind,label:n.label||n.id,detail:n.summary||n.title||""});
    });
  });
  const limitedEdges=edges.filter(e=>positions.has(e.source)&&positions.has(e.target));
  return <div style={{overflowX:"auto",padding:"4px"}}>
    <svg width={totalW} height={height} style={{minWidth:"100%",background:"var(--bg-primary)"}}>
      {sortedKinds.map((kind,idx)=>(
        <g key={"kind-"+kind}>
          <text x={PAD_X+(idx+0.5)*COL_W} y={20} textAnchor="middle" fontSize="12" fontWeight="800" fill="var(--accent)">{kind}</text>
          <text x={PAD_X+(idx+0.5)*COL_W} y={32} textAnchor="middle" fontSize="10" fill="var(--text-secondary)">{byKind.get(kind).length} nodes</text>
        </g>
      ))}
      {limitedEdges.map((e,i)=>{
        const s=positions.get(e.source);
        const t=positions.get(e.target);
        if(!s||!t)return null;
        const mx=(s.x+t.x)/2;
        const my=Math.min(s.y,t.y)-Math.min(40,Math.abs(s.x-t.x)/3+12);
        return <g key={(e.edge_id||(e.source+"-"+e.target+"-"+i))}>
          <path d={`M ${s.x} ${s.y} Q ${mx} ${my}, ${t.x} ${t.y}`} stroke="var(--text-secondary)" fill="none" strokeWidth="1.2" opacity="0.55"/>
          <text x={mx} y={my+10} textAnchor="middle" fontSize="10" fill="var(--text-secondary)">{e.relation||e.label||""}</text>
        </g>;
      })}
      {Array.from(positions.entries()).map(([id,p])=>{
        const labelShort=p.label.length>22?p.label.slice(0,22)+"…":p.label;
        return <g key={"n-"+id} transform={`translate(${p.x}, ${p.y})`}>
          <title>{p.label}{p.detail?" — "+p.detail:""}</title>
          <rect x={-(COL_W/2-12)} y={-18} width={COL_W-24} height={36} rx={6} fill="var(--bg-secondary)" stroke="var(--accent)" strokeWidth="1.2"/>
          <text x={0} y={-2} textAnchor="middle" fontSize="11" fontWeight="700" fill="var(--text-primary)">{labelShort}</text>
          <text x={0} y={12} textAnchor="middle" fontSize="9" fill="var(--text-secondary)" fontFamily="monospace">{id.length>26?id.slice(0,26)+"…":id}</text>
        </g>;
      })}
    </svg>
    {(edges.length>limitedEdges.length||sortedKinds.some(k=>byKind.get(k).length>MAX_PER_KIND))&&(
      <div style={{fontSize:12,color:"var(--text-secondary)",padding:"6px 10px"}}>kind 당 최대 {MAX_PER_KIND}개 노드만 표시. 전체 {nodes.length} 노드 · {edges.length} 엣지.</div>
    )}
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
    ]).then(([st,w,e,g])=>{
      setStatus(st);
      setDocs(w.docs||[]);
      setEvents(e.events||[]);
      setGraph(g);
      setMsg("");
    }).catch(e=>setMsg(e.message||"로드 실패"))
      .finally(()=>setBusy(false));
  };

  useEffect(()=>{load();},[]);

  const counts=status?.counts||{};
  const ontology=status?.ontology||{};
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
        <div style={{...card,padding:0,overflow:"hidden"}}>
          <div style={{padding:"10px 12px",borderBottom:"1px solid var(--border)",background:"var(--bg-tertiary)",fontSize:13}}><b>Ontology 그래프</b> <span style={{color:"var(--text-secondary)",marginLeft:6}}>concept 노드와 의미 관계 edge 를 시각화합니다.</span></div>
          <KnowledgeGraphView graph={ontology} />
        </div>
        <div style={{display:"grid",gridTemplateColumns:"1fr 1fr",gap:14}}>
          <div style={card}><div style={{fontWeight:900,marginBottom:10}}>Concept nodes</div>{(ontology.nodes||[]).map(n=><div key={n.id} style={{fontSize:14,padding:"6px 0",borderBottom:"1px solid var(--border)"}}><b>{n.label}</b> <span style={{color:"var(--text-secondary)"}}>({n.kind})</span></div>)}{!(ontology.nodes||[]).length&&<div style={{fontSize:13,color:"var(--text-secondary)"}}>concept 노드 없음.</div>}</div>
          <div style={card}><div style={{fontWeight:900,marginBottom:10}}>Concept edges</div>{(ontology.edges||[]).map((e,i)=><div key={i} style={{fontSize:14,padding:"6px 0",borderBottom:"1px solid var(--border)",fontFamily:"monospace"}}>{e.source} -[{e.relation}]-&gt; {e.target}</div>)}{!(ontology.edges||[]).length&&<div style={{fontSize:13,color:"var(--text-secondary)"}}>concept edge 없음.</div>}</div>
        </div>
      </div>}

      {tab==="write"&&canManage&&<div style={{...card,maxWidth:900}}>
        <div style={{fontSize:17,fontWeight:900,marginBottom:14}}>Wiki 문서 생성/수정</div>
        <div style={{display:"grid",gap:10}}>
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
        </div>
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
