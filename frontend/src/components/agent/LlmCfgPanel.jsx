import { useEffect, useState } from "react";
import { sf } from "../../lib/api";
import { statusPalette } from "../UXKit";

const OK = statusPalette.ok;
const BAD = statusPalette.bad;
const WHITE = "var(--bg-secondary)";

export default function LlmCfgPanel({ readOnly = false } = {}){
  const FALLBACK_LLM_DEFAULTS={
    openai:{enabled:false,api_url:"",model:"",mode:"fast",admin_token:"",provider:"openai",auth_mode:"bearer",system_name:"",user_id:"",user_type:"",format:"openai",timeout_s:20},
    openai_compatible:{enabled:false,api_url:"",model:"",mode:"fast",admin_token:"",provider:"openai_compatible",auth_mode:"bearer",system_name:"",user_id:"",user_type:"",format:"openai",timeout_s:60},
    local:{enabled:false,api_url:"",model:"",mode:"fast",admin_token:"",provider:"local",auth_mode:"none",system_name:"",user_id:"",user_type:"",format:"openai",timeout_s:60},
    generic:{enabled:false,api_url:"",model:"",mode:"fast",admin_token:"",provider:"generic",auth_mode:"bearer",system_name:"",user_id:"",user_type:"",format:"openai",timeout_s:20},
    playground:{enabled:false,api_url:"",model:"",mode:"fast",admin_token:"",provider:"playground",auth_mode:"dep_ticket",system_name:"playground",user_id:"",user_type:"",format:"openai",timeout_s:20},
    vertex_gemini:{enabled:false,api_url:"",model:"google/gemini-2.5-flash",mode:"fast",admin_token:"",provider:"vertex_gemini",auth_mode:"google_adc",system_name:"",user_id:"",user_type:"",format:"openai",timeout_s:30},
  };
  const PROVIDERS=Object.keys(FALLBACK_LLM_DEFAULTS);
  const[cfg,setCfg]=useState(FALLBACK_LLM_DEFAULTS.generic);
  const[profiles,setProfiles]=useState({});
  const[profileDefaults,setProfileDefaults]=useState({});
  const[msg,setMsg]=useState("");
  const[busy,setBusy]=useState(false);
  const[testBusy,setTestBusy]=useState(false);
  const[testPrompt,setTestPrompt]=useState("연결 확인입니다. 정상 수신했다면 확인완료 라고만 답하세요.");
  const[showToken,setShowToken]=useState(false);
  const cleanProvider=(provider)=>{
    const p=(provider||"generic").toString().trim();
    return PROVIDERS.includes(p)?p:"generic";
  };
  const providerDefaults=(provider,defaultsMap=profileDefaults)=>{
    const p=cleanProvider(provider);
    return {...(FALLBACK_LLM_DEFAULTS[p]||FALLBACK_LLM_DEFAULTS.generic),...((defaultsMap||{})[p]||{})};
  };
  const normalizeWithDefaults=(l={},providerHint="",defaultsMap=profileDefaults)=>{
    const provider=cleanProvider(l.provider||providerHint);
    const base=providerDefaults(provider,defaultsMap);
    const out={...base,...l,provider};
    const timeout=Number(out.timeout_s||base.timeout_s||20);
    out.enabled=!!out.enabled;
    out.api_url=(out.api_url||"").toString();
    out.model=(out.model||"").toString();
    out.mode=(out.mode||"fast").toString()||"fast";
    out.admin_token=(out.admin_token||"").toString();
    out.auth_mode=(out.auth_mode||base.auth_mode||"bearer").toString();
    if(!["bearer","dep_ticket","google_adc","none"].includes(out.auth_mode))out.auth_mode=base.auth_mode||"bearer";
    out.system_name=(out.system_name||(provider==="playground"?"playground":"")).toString();
    out.user_id=(out.user_id||"").toString();
    out.user_type=(out.user_type||"").toString();
    out.format=(out.format||"openai").toString();
    out.timeout_s=Math.max(3,Math.min(120,Number.isFinite(timeout)?timeout:(base.timeout_s||20)));
    return out;
  };
  const reload=()=>{
    if(readOnly){
      sf("/api/llm/status").then(d=>{
        const active=normalizeWithDefaults(d.config||{},(d.config||{}).provider||"generic",{});
        setProfileDefaults({});
        setProfiles(active.provider?{[active.provider]:active}:{});
        setCfg(active);
        setMsg("");
      }).catch(e=>setMsg("로드 오류: "+e.message));
      return;
    }
    sf("/api/admin/settings").then(d=>{
      const defaults=d.llm_profile_defaults||{};
      const saved=d.llm_profiles||{};
      const nextProfiles={};
      Object.entries(saved).forEach(([provider,payload])=>{
        const normalized=normalizeWithDefaults(payload||{},provider,defaults);
        nextProfiles[normalized.provider]=normalized;
      });
      const active=normalizeWithDefaults(d.llm||{},(d.llm||{}).provider||"generic",defaults);
      if(active.provider&&!nextProfiles[active.provider])nextProfiles[active.provider]=active;
      setProfileDefaults(defaults);
      setProfiles(nextProfiles);
      setCfg(active);
    }).catch(e=>setMsg("로드 오류: "+e.message));
  };
  useEffect(()=>{reload();},[readOnly]);
  const patch=(next)=>setCfg(c=>{
    if(readOnly)return c;
    const updated=normalizeWithDefaults({...c,...next},c.provider,profileDefaults);
    setProfiles(p=>({...p,[updated.provider]:updated}));
    return updated;
  });
  const setProvider=(provider)=>{
    if(readOnly)return;
    const nextProvider=cleanProvider(provider);
    setProfiles(prev=>{
      const curProvider=cleanProvider(cfg.provider);
      const withCurrent={...prev,[curProvider]:normalizeWithDefaults(cfg,curProvider,profileDefaults)};
      const nextCfg=normalizeWithDefaults(withCurrent[nextProvider]||{},nextProvider,profileDefaults);
      setCfg(nextCfg);
      return withCurrent;
    });
  };
  const save=()=>{
    if(readOnly)return;
    setBusy(true);setMsg("");
    const active=normalizeWithDefaults(cfg,cfg.provider,profileDefaults);
    sf("/api/admin/settings").then(cur=>sf("/api/admin/settings/save",{method:"POST",headers:{"Content-Type":"application/json"},body:JSON.stringify({
      dashboard_refresh_minutes:cur.dashboard_refresh_minutes??10,
      dashboard_bg_refresh_minutes:cur.dashboard_bg_refresh_minutes??10,
      llm:{
        enabled:!!active.enabled,
        api_url:active.api_url,
        model:active.model,
        mode:active.mode||"fast",
        admin_token:active.admin_token,
        provider:active.provider||"generic",
        auth_mode:active.auth_mode||providerDefaults(active.provider).auth_mode||"bearer",
        system_name:active.system_name,
        user_id:active.user_id,
        user_type:active.user_type,
        format:active.format||"openai",
        timeout_s:Number(active.timeout_s)||20,
      },
    })})).then(()=>{setMsg("저장됨");reload();}).catch(e=>setMsg("오류: "+e.message)).finally(()=>setBusy(false));
  };
  const test=()=>{
    if(readOnly)return;
    setTestBusy(true);setMsg("");
    sf("/api/llm/test",{method:"POST",headers:{"Content-Type":"application/json"},body:JSON.stringify({prompt:testPrompt||"연결 확인"})})
      .then(d=>setMsg(d?.ok===false?"테스트 실패: "+(d.error||"unknown"):"테스트 완료: "+String(d?.text||"응답 있음").slice(0,160)))
      .catch(e=>setMsg("테스트 오류: "+e.message))
      .finally(()=>setTestBusy(false));
  };
  const L={fontSize:14,color:"var(--text-secondary)",marginBottom:4,marginTop:10,fontWeight:600};
  const I={width:"100%",padding:"8px 12px",borderRadius:5,border:"1px solid var(--border)",background:"var(--bg-primary)",color:"var(--text-primary)",fontSize:14,outline:"none",boxSizing:"border-box"};
  const provider=cleanProvider(cfg.provider);
  const isPlayground=provider==="playground";
  const isLocal=provider==="local";
  const isVertex=provider==="vertex_gemini";
  const showMode=provider==="generic";
  const authMode=cfg.auth_mode||providerDefaults(provider).auth_mode||"bearer";
  const previewHeaders={
    Accept:"application/json",
    "Content-Type":"application/json",
    ...(authMode==="bearer"&&cfg.admin_token?{Authorization:"Bearer <admin_token>"}:{}),
    ...(authMode==="dep_ticket"&&cfg.admin_token?{"x-dep-ticket":"<credential_key>"}:{}),
    ...(authMode==="google_adc"?{Authorization:"Bearer <Google ADC access token>"}:{}),
    ...(isPlayground?{
      "Send-System-Name":cfg.system_name||"playground",
      "User-Id":cfg.user_id||"(입력 필요)",
      "User-Type":cfg.user_type||"(입력 필요)",
      "Prompt-Msg-Id":"<uuid4>",
      "Completion-Msg-Id":"<uuid4>",
    }:{}),
  };
  const previewBody=isPlayground?{
    model:cfg.model||"(입력 필요)",
    messages:[{role:"system",content:"You are a helpful assistant."},{role:"user",content:"..."}],
    temperature:0.5,
    stream:false,
  }:(cfg.format==="vertex_gemini"?{
    contents:[{role:"user",parts:[{text:"..."}]}],
    systemInstruction:{parts:[{text:"..."}]},
    generationConfig:{},
  }:{
    ...(showMode&&cfg.mode?{mode:cfg.mode}:{}),
    ...(cfg.model?{model:cfg.model}:{}),
    [cfg.format==="raw"?"prompt":"messages"]:cfg.format==="raw"?"...":[{role:"system",content:"..."},{role:"user",content:"..."}],
  });
  const preview={
    enabled:!!cfg.enabled,
    request:"POST "+(cfg.api_url||"(설정 필요)"),
    provider,
    auth_mode:authMode,
    headers:previewHeaders,
    body:previewBody,
    users:"홈 Flowi 사용자는 별도 LLM token 입력 없이 서버 설정으로 실행",
  };
  return(<div className="llm-cfg-panel" style={{background:"transparent",border:"0",padding:0,maxWidth:"none",opacity:readOnly?0.58:1,pointerEvents:readOnly?"none":"auto"}}>
    <div style={{fontSize:14,fontWeight:700,marginBottom:4}}>Flowi LLM 설정</div>
    <div style={{fontSize:14,color:"var(--text-secondary)",marginBottom:10,lineHeight:1.6}}>
      Admin 이 저장한 credential 을 서버에서 사용합니다. 일반 사용자는 홈 Flowi 콘솔에서 질문만 입력하고, 답변과 기능별 표/추천은 홈에서 바로 확인합니다.
    </div>
    <label style={{display:"flex",alignItems:"center",gap:6,fontSize:14,marginBottom:6}}>
      <input type="checkbox" checked={!!cfg.enabled} onChange={e=>patch({enabled:e.target.checked})}/>
      LLM 기능 활성화
    </label>
    <div style={{display:"grid",gridTemplateColumns:"1fr 2fr",gap:10}}>
      <div>
        <div style={L}>API Profile</div>
        <select value={cfg.provider||"generic"} onChange={e=>setProvider(e.target.value)} style={I}>
          <option value="openai">OpenAI API</option>
          <option value="openai_compatible">OpenAI 호환 API</option>
          <option value="local">사내 Local LLM</option>
          <option value="generic">Custom Generic</option>
          <option value="playground">사내 Playground API</option>
          <option value="vertex_gemini">Vertex Gemini ADC</option>
        </select>
      </div>
      <div>
        <div style={L}>API URL</div>
        <input value={cfg.api_url} onChange={e=>patch({api_url:e.target.value})} placeholder={isVertex?"https://aiplatform.googleapis.com/v1beta1/projects/<project>/locations/us-central1/endpoints/openapi/chat/completions":(isLocal?"http://llm.internal/v1":"https://llm.internal/v1/chat/completions")} style={I}/>
      </div>
    </div>
    <div style={{display:"grid",gridTemplateColumns:isPlayground||!showMode?"2fr 1fr 1fr":"2fr 1fr 1fr 1fr",gap:10}}>
      <div>
        <div style={L}>Model</div>
        <input value={cfg.model} onChange={e=>patch({model:e.target.value})} placeholder={isVertex?"google/gemini-2.5-flash":"model name"} style={I}/>
      </div>
      {showMode&&<div>
        <div style={L}>Mode</div>
        <select value={cfg.mode||"fast"} onChange={e=>patch({mode:e.target.value})} style={I}>
          <option value="fast">fast</option>
          <option value="balanced">balanced</option>
          <option value="quality">quality</option>
        </select>
      </div>}
      <div>
        <div style={L}>Format</div>
        <select value={cfg.format||"openai"} onChange={e=>patch({format:e.target.value})} disabled={isPlayground} style={{...I,opacity:isPlayground?0.65:1}}>
          <option value="openai">openai</option>
          <option value="raw">raw</option>
          <option value="vertex_gemini">vertex_gemini</option>
        </select>
      </div>
      <div>
        <div style={L}>Timeout (sec)</div>
        <input type="number" min={3} max={120} value={cfg.timeout_s||20} onChange={e=>patch({timeout_s:Number(e.target.value)})} style={{...I,fontFamily:"monospace"}}/>
      </div>
    </div>
    <div style={L}>{isPlayground?"Credential Key":(isVertex?"Optional Token Override":"Admin LLM Token")} <span style={{fontWeight:400,color:"var(--text-secondary)"}}>({isPlayground?"x-dep-ticket 로 전송":(authMode==="google_adc"?"Google ADC access token을 요청 직전 사용":(authMode==="none"?"인증 없음":"Authorization Bearer 로 전송"))})</span></div>
    <div style={{display:"flex",gap:8}}>
      <input type={showToken?"text":"password"} value={cfg.admin_token} onChange={e=>patch({admin_token:e.target.value})} placeholder={isPlayground?"사내 credential key":"admin token"} autoComplete="off" style={{...I,fontFamily:"monospace",flex:1}}/>
      <button type="button" onClick={()=>setShowToken(!showToken)} style={{padding:"8px 12px",borderRadius:5,border:"1px solid var(--border)",background:"transparent",color:"var(--text-secondary)",fontSize:14,cursor:"pointer"}}>{showToken?"숨김":"보기"}</button>
    </div>
    <div style={{display:isPlayground?"grid":"none",gridTemplateColumns:"1fr 1fr 1fr",gap:10}}>
      <div>
        <div style={L}>Send-System-Name</div>
        <input value={cfg.system_name} onChange={e=>patch({system_name:e.target.value})} placeholder="playground" style={I}/>
      </div>
      <div>
        <div style={L}>User-Id</div>
        <input value={cfg.user_id} onChange={e=>patch({user_id:e.target.value})} placeholder="Knox ID" style={I}/>
      </div>
      <div>
        <div style={L}>User-Type</div>
        <input value={cfg.user_type} onChange={e=>patch({user_type:e.target.value})} placeholder="admin/user type" style={I}/>
      </div>
    </div>
    <div style={{marginTop:18,padding:12,background:"var(--bg-card)",borderRadius:6,border:"1px solid var(--border)"}}>
      <div style={{fontSize:14,fontWeight:700,marginBottom:6}}>요청 미리보기</div>
      <pre style={{fontSize:14,lineHeight:1.45,padding:10,background:"var(--bg-primary)",border:"1px solid var(--border)",borderRadius:4,overflow:"auto",maxHeight:260,fontFamily:"monospace",margin:0,color:"var(--text-primary)"}}>
{JSON.stringify(preview, null, 2)}
      </pre>
    </div>
    <div style={{marginTop:14}}>
      <div style={L}>테스트 프롬프트</div>
      <textarea value={testPrompt} onChange={e=>setTestPrompt(e.target.value)} rows={3} style={{...I,resize:"vertical"}}/>
    </div>
    <div style={{marginTop:14,display:"flex",gap:8,alignItems:"center",flexWrap:"wrap"}}>
      <button onClick={save} disabled={busy} style={{padding:"8px 18px",borderRadius:6,border:"none",background:"var(--accent)",color:WHITE,fontWeight:600,cursor:busy?"wait":"pointer"}}>{busy?"저장 중...":"저장"}</button>
      <button onClick={test} disabled={testBusy||!cfg.enabled||!cfg.api_url} style={{padding:"8px 16px",borderRadius:6,border:"1px solid var(--border)",background:"transparent",color:"var(--text-primary)",fontWeight:600,cursor:testBusy?"wait":"pointer",opacity:(!cfg.enabled||!cfg.api_url)?0.45:1}}>{testBusy?"테스트 중...":"연결 테스트"}</button>
      <button onClick={reload} disabled={busy||testBusy} style={{padding:"8px 14px",borderRadius:6,border:"1px solid var(--border)",background:"transparent",color:"var(--text-secondary)",cursor:"pointer"}}>새로고침</button>
      {msg&&<span style={{fontSize:14,color:msg.startsWith("오류")||msg.includes("실패")?BAD.fg:OK.fg}}>{msg}</span>}
    </div>
  </div>);
}
