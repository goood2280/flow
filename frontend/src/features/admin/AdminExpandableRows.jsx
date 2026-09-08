import { useState } from "react";
import { Button, Pill } from "../../components/UXKit";

const cell = {padding:"8px 12px",borderBottom:"1px solid var(--border)",whiteSpace:"nowrap",overflow:"hidden",textOverflow:"ellipsis"};
const detail = {margin:0,whiteSpace:"pre-wrap",overflowWrap:"anywhere"};

function DetailField({label, children}) {
  return <div style={{minWidth:0}}><div style={{fontSize:12,color:"var(--text-secondary)",marginBottom:4}}>{label}</div><div style={detail}>{children || "(없음)"}</div></div>;
}

export function PermissionGroupRow({group, permissionLabel, onEdit, onDelete}) {
  const [open,setOpen]=useState(false);
  const departments=Array.isArray(group.departments)?group.departments:[];
  const members=Array.isArray(group.members)?group.members:[];
  return <>
    <tr>
      <td style={{...cell,fontWeight:700}} title={group.name}>{group.name}</td>
      <td style={cell}>{departments.length}개 부서</td>
      <td style={cell} title={permissionLabel}>{permissionLabel}</td>
      <td style={cell}>{members.length}명</td>
      <td style={{...cell,textAlign:"right"}}>
        <Button size="compact" variant="ghost" aria-expanded={open} onClick={()=>setOpen(v=>!v)}>{open?"상세 접기":"상세 펼치기"}</Button>
        <Button size="compact" variant="ghost" onClick={onEdit}>편집</Button>
        <Button size="compact" variant="ghost" onClick={onDelete} style={{color:"var(--bad,#ef4444)"}}>삭제</Button>
      </td>
    </tr>
    {open&&<tr><td colSpan={5} style={{padding:16,borderBottom:"1px solid var(--border)",background:"var(--bg-tertiary)"}}>
      <div style={{display:"grid",gap:14}}>
        <DetailField label="그룹">{group.name}</DetailField>
        <DetailField label="기본 부서">{departments.join(", ")}</DetailField>
        <DetailField label="권한">{permissionLabel}</DetailField>
        <DetailField label={`직접 멤버 · ${members.length}명`}>{members.join(", ")}</DetailField>
      </div>
    </td></tr>}
  </>;
}

export function DownloadHistoryRow({download:d}) {
  const [open,setOpen]=useState(false);
  return <>
    <tr>
      <td style={cell} title={d.timestamp}>{String(d.timestamp||"").slice(0,19).replace("T"," ")}</td>
      <td style={cell} title={d.source}><Pill tone={d.sourceTone}>{d.source}</Pill></td>
      <td style={cell} title={d.username}>{d.username}</td>
      <td style={{...cell,fontFamily:"monospace"}} title={d.target}>{d.target}</td>
      <td style={cell}><Button size="compact" variant="ghost" aria-expanded={open} onClick={()=>setOpen(v=>!v)}>{open?"상세 접기":"상세 펼치기"}</Button></td>
      <td style={cell} title={d.aux||""}>{d.aux||"-"}</td>
      <td style={cell}>{d.rows}</td>
      <td style={cell}>{d.size}</td>
    </tr>
    {open&&<tr><td colSpan={8} style={{padding:16,borderBottom:"1px solid var(--border)",background:"var(--bg-tertiary)"}}>
      <div style={{display:"grid",gap:14}}>
        <DetailField label="시간">{d.timestamp}</DetailField>
        <DetailField label="구분">{d.source}</DetailField>
        <DetailField label="사용자">{d.username}</DetailField>
        <DetailField label="대상">{d.target}</DetailField>
        <DetailField label="상세">{d.detail||"-"}</DetailField>
        <DetailField label="컬럼">{d.aux||"-"}</DetailField>
      </div>
    </td></tr>}
  </>;
}
