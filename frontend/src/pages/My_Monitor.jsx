import { useState, useEffect } from "react";
import Loading from "../components/Loading";

export default function My_Monitor() {
  const [sys, setSys] = useState(null);
  const [loading, setLoading] = useState(true);

  const load = () => {
    fetch("/api/monitor/system").then(r=>r.ok?r.json():Promise.reject(new Error("HTTP "+r.status))).then(d=>{setSys(d&&typeof d==="object"?d:null);setLoading(false);}).catch(e=>{console.warn("[Monitor] load failed:",e);setLoading(false);});
  };
  useEffect(() => { let alive=true; const tick=()=>{if(alive)load();}; tick(); const iv = setInterval(tick, 15000); return () => {alive=false; clearInterval(iv);}; }, []);

  if (loading) return <div style={{display:"flex",alignItems:"center",justifyContent:"center",height:"calc(100vh-48px)",background:"var(--bg-primary)"}}><Loading text="Loading..."/></div>;

  const bar = (pct, color) => (
    <div style={{height:8,borderRadius:4,background:"var(--bg-hover,#333)",overflow:"hidden",flex:1}}>
      <div style={{height:"100%",borderRadius:4,background:color,width:pct+"%",transition:"width 0.5s"}} />
    </div>
  );

  const pctColor = (v) => v > 80 ? "#ef4444" : v > 60 ? "#fbbf24" : "#22c55e";

  return (
    <div style={{padding:"24px 32px",background:"var(--bg-primary,#1a1a1a)",minHeight:"calc(100vh - 48px)",color:"var(--text-primary)",fontFamily:"'Pretendard',sans-serif",maxWidth:1200}}>
      <div style={{fontSize:14,fontWeight:700,marginBottom:24,fontFamily:"'JetBrains Mono',monospace",color:"var(--accent,#f97316)"}}>{">"} system_monitor</div>

      {/* System Info */}
      <div style={{display:"grid",gridTemplateColumns:"repeat(3,1fr)",gap:16,marginBottom:28}}>
        {/* CPU */}
        <div style={{background:"var(--bg-secondary,#262626)",borderRadius:10,border:"1px solid var(--border,#333)",padding:"20px"}}>
          <div style={{display:"flex",justifyContent:"space-between",alignItems:"center",marginBottom:12}}>
            <span style={{fontSize:12,color:"var(--text-secondary)"}}>CPU</span>
            <span style={{fontSize:20,fontWeight:700,fontFamily:"monospace",color:pctColor(sys?.cpu_percent||0)}}>{sys?.cpu_percent||0}%</span>
          </div>
          {bar(sys?.cpu_percent||0, pctColor(sys?.cpu_percent||0))}
        </div>

        {/* Memory */}
        <div style={{background:"var(--bg-secondary,#262626)",borderRadius:10,border:"1px solid var(--border,#333)",padding:"20px"}}>
          <div style={{display:"flex",justifyContent:"space-between",alignItems:"center",marginBottom:12}}>
            <span style={{fontSize:12,color:"var(--text-secondary)"}}>Memory</span>
            <span style={{fontSize:20,fontWeight:700,fontFamily:"monospace",color:pctColor(sys?.memory_percent||0)}}>{sys?.memory_percent||0}%</span>
          </div>
          {bar(sys?.memory_percent||0, pctColor(sys?.memory_percent||0))}
          <div style={{fontSize:11,color:"var(--text-secondary)",marginTop:8}}>{sys?.memory_used_gb||0} / {sys?.memory_total_gb||0} GB</div>
        </div>

        {/* Disk */}
        <div style={{background:"var(--bg-secondary,#262626)",borderRadius:10,border:"1px solid var(--border,#333)",padding:"20px"}}>
          <div style={{display:"flex",justifyContent:"space-between",alignItems:"center",marginBottom:12}}>
            <span style={{fontSize:12,color:"var(--text-secondary)"}}>Disk (/config)</span>
            <span style={{fontSize:20,fontWeight:700,fontFamily:"monospace",color:pctColor(sys?.disk_percent||0)}}>{sys?.disk_percent||0}%</span>
          </div>
          {bar(sys?.disk_percent||0, pctColor(sys?.disk_percent||0))}
          <div style={{fontSize:11,color:"var(--text-secondary)",marginTop:8}}>{sys?.disk_used_gb||0} / {sys?.disk_total_gb||0} GB</div>
        </div>
      </div>

      {/* DB Health - Under Development */}
      <div style={{background:"var(--bg-secondary,#262626)",borderRadius:10,border:"1px solid var(--border,#333)",padding:"24px"}}>
        <div style={{display:"flex",alignItems:"center",gap:10,marginBottom:16}}>
          <span style={{fontSize:14,fontWeight:700,fontFamily:"'JetBrains Mono',monospace"}}>DB Pipeline Status</span>
          <span style={{fontSize:10,fontWeight:700,padding:"2px 8px",borderRadius:4,background:"#f9731622",color:"#f97316"}}>DEV</span>
        </div>
        <div style={{display:"flex",alignItems:"center",gap:12,padding:"16px",background:"var(--bg-primary,#1a1a1a)",borderRadius:8}}>
          <span style={{fontSize:28}}>🚧</span>
          <div>
            <div style={{fontSize:13,color:"var(--text-primary)",fontWeight:600}}>Under Development</div>
            <div style={{fontSize:12,color:"var(--text-secondary)",marginTop:2}}>DB update log monitoring will be available in a future update</div>
          </div>
        </div>
      </div>

      {/* Last checked */}
      <div style={{marginTop:16,fontSize:11,color:"var(--text-secondary)",fontFamily:"monospace",textAlign:"right"}}>
        Last checked: {(typeof sys?.checked_at==="string"?sys.checked_at.slice(0,19):"-")} (auto-refresh 15s)
      </div>
    </div>
  );
}
