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
        </div>
        <div style={{...card,padding:0,overflow:"hidden"}}>
          <table style={{width:"100%",borderCollapse:"collapse",fontSize:13}}>
            <thead><tr style={{background:"var(--bg-tertiary)",color:"var(--text-secondary)"}}><th style={{padding:9,textAlign:"left"}}>source</th><th style={{padding:9,textAlign:"left"}}>relation</th><th style={{padding:9,textAlign:"left"}}>target</th></tr></thead>
            <tbody>{(graph?.edges||[]).slice(0,300).map(e=><tr key={e.edge_id}><td style={{padding:9,borderTop:"1px solid var(--border)",fontFamily:"monospace"}}>{e.source}</td><td style={{padding:9,borderTop:"1px solid var(--border)",color:"var(--accent)",fontWeight:700}}>{e.relation}</td><td style={{padding:9,borderTop:"1px solid var(--border)",fontFamily:"monospace"}}>{e.target}</td></tr>)}</tbody>
          </table>
        </div>
      </div>}

      {tab==="ontology"&&<div style={{display:"grid",gridTemplateColumns:"1fr 1fr",gap:14}}>
        <div style={card}><div style={{fontWeight:900,marginBottom:10}}>Concept nodes</div>{(ontology.nodes||[]).map(n=><div key={n.id} style={{fontSize:14,padding:"6px 0",borderBottom:"1px solid var(--border)"}}><b>{n.label}</b> <span style={{color:"var(--text-secondary)"}}>({n.kind})</span></div>)}</div>
        <div style={card}><div style={{fontWeight:900,marginBottom:10}}>Concept edges</div>{(ontology.edges||[]).map((e,i)=><div key={i} style={{fontSize:14,padding:"6px 0",borderBottom:"1px solid var(--border)",fontFamily:"monospace"}}>{e.source} -[{e.relation}]-&gt; {e.target}</div>)}</div>
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
