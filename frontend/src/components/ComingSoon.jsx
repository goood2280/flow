export default function ComingSoon({ name }) {
  return (
    <div style={{display:"flex",alignItems:"center",justifyContent:"center",height:"calc(100vh - 48px)",
      background:"var(--bg-primary,#1a1a1a)",fontFamily:"'JetBrains Mono',monospace"}}>
      <div style={{textAlign:"center"}}>
        <div style={{fontSize:48,marginBottom:16}}>🚧</div>
        <div style={{fontSize:18,fontWeight:700,color:"var(--accent,#f97316)",marginBottom:8}}>{name || "Feature"}</div>
        <div style={{fontSize:13,color:"var(--text-secondary,#a3a3a3)"}}>Coming Soon</div>
        <div style={{marginTop:20,padding:"8px 16px",borderRadius:6,border:"1px solid var(--border,#333)",
          fontSize:12,color:"var(--text-secondary)"}}>
          이 기능은 준비 중입니다
        </div>
      </div>
    </div>
  );
}
