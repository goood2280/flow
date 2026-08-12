import re

with open(r'd:\semi all\flow\frontend\src\pages\My_RamCache.jsx', 'r', encoding='utf-8') as f:
    content = f.read()

# Add mainTab state
content = content.replace('  const [selProd, setSelProd] = useState("");', '  const [selProd, setSelProd] = useState("");\n  const [mainTab, setMainTab] = useState("products");')

# Replace top bar
top_bar_old = """      <div style={{ display: "flex", alignItems: "center", justifyContent: "flex-end", gap: 8, marginBottom: 14 }}>
        {canManage && <button onClick={openBudgetModal} title="캐시 예산 조절"
          style={{ ...S_BTN, display: "flex", alignItems: "center", gap: 4 }}>⚙ 예산 설정</button>}
        <button onClick={refreshAll} style={{ ...S_BTN, color: "var(--accent)" }}>새로고침</button>
      </div>"""

top_bar_new = """      <div style={{ display: "flex", alignItems: "center", justifyContent: "space-between", gap: 8, marginBottom: 16 }}>
        <div style={{ display: "flex", flex: 1, maxWidth: 450 }}>
          <TabStrip active={mainTab} onChange={setMainTab}
            items={[
              { k: "products", l: "제품별 현황" },
              { k: "jobs", l: "캐싱 진행 및 로그" },
              { k: "speed_config", l: "검색 속도 & 설정" },
            ]} />
        </div>
        <div style={{ display: "flex", gap: 8 }}>
          {canManage && <button onClick={openBudgetModal} title="캐시 예산 조절"
            style={{ ...S_BTN, display: "flex", alignItems: "center", gap: 4 }}>⚙ 예산 설정</button>}
          <button onClick={refreshAll} style={{ ...S_BTN, color: "var(--accent)" }}>새로고침</button>
        </div>
      </div>"""

content = content.replace(top_bar_old, top_bar_new)

b1_start = "{/* 전체 사용량 바"
b2_start = "{/* 관리자 — 캐시 수동 스캔/설정"
b3_start = "{/* 검색 속도 (히트/미스)"
b4_start = "{/* 쿼리 병렬 코어 수"
b5_start = "{/* 관리자 — Peak RAM"
b6_start = "{/* 선택 제품 상세"
b6_end = "    </div>\n  );\n}"

idx_b1 = content.find(b1_start)
idx_b2 = content.find(b2_start)
idx_b3 = content.find(b3_start)
idx_b4 = content.find(b4_start)
idx_b5 = content.find(b5_start)
idx_b6 = content.find(b6_start)

part_before_b1 = content[:idx_b1]

block_1 = content[idx_b1:idx_b2]
block_2 = content[idx_b2:idx_b3]
block_3 = content[idx_b3:idx_b4]
block_4 = content[idx_b4:idx_b5]
block_5 = content[idx_b5:idx_b6]

idx_end = content.find(b6_end)
block_6 = content[idx_b6:idx_end]
part_after_b6 = content[idx_end:]

tab1 = f"""
      {{mainTab === "products" && (
        <div style={{{{ display: "grid", gap: 14 }}}}>
{block_1}
{block_6}
        </div>
      )}}
"""

block_2_clean = block_2.replace('        <div style={{ fontSize: 14, fontWeight: 800 }}>관리자 · 캐시 수동 스캔 / 설정</div>', '        <div style={{ fontSize: 14, fontWeight: 800 }}>캐시 수동 스캔 / 큐 관리</div>')
block_2_clean += '      </div>}\n\n'

tab2 = f"""
      {{mainTab === "jobs" && (
        <div style={{{{ display: "grid", gap: 14 }}}}>
          {{!canManage ? <div style={{{{ padding: 20, textAlign: "center", color: "var(--text-secondary)" }}}}>이 탭은 관리자 권한이 필요합니다.</div> : null}}
{block_2_clean}
{block_5}
        </div>
      )}}
"""

block_3_4 = f"""
      {{canManage && <div style={{{{ display: "grid", gap: 10, padding: "10px 12px", borderRadius: 8,
        border: "1px solid var(--border)", background: "var(--bg-card)", marginBottom: 16 }}}}>
        <div style={{{{ fontSize: 14, fontWeight: 800 }}}}>검색 속도 및 설정</div>
{block_3}
{block_4.strip()}
"""

tab3 = f"""
      {{mainTab === "speed_config" && (
        <div style={{{{ display: "grid", gap: 14 }}}}>
          {{!canManage ? <div style={{{{ padding: 20, textAlign: "center", color: "var(--text-secondary)" }}}}>이 탭은 관리자 권한이 필요합니다.</div> : null}}
{block_3_4}
        </div>
      )}}
"""

new_content = part_before_b1 + tab1 + tab2 + tab3 + part_after_b6

with open(r'd:\semi all\flow\frontend\src\pages\My_RamCache.jsx', 'w', encoding='utf-8') as f:
    f.write(new_content)

print("Done")
