# HOL_UPDATE_GUIDE — Claude 와 업데이트 진행 방법 (v8.1.5)

**웹 Claude (이 대화 창) 와 함께 HOL WEB 을 업데이트할 때 따라야 할 흐름.**

---

## 1. 핵심 원칙

1. **setup_v8.py 는 Project Knowledge 에 11개 파트로 저장** (gzip+base64, 피처 단위 그룹핑)
2. **수정 배포는 `update_vXXX.py` 로만** — monolithic base64 self-contained 스크립트
3. **시점별로 필요한 파일만 교체**:
   - 대화 중: `update_vXXX.py` 만 받아서 실행
   - 대화 마무리: 변경된 part 파일만 Project Knowledge 에 동기화

## 2. 대화 시작 템플릿

```
HOL WEB v8.1.x 수정 요청.

[수정 내용]
- Dashboard 의 Y축 log scale 옵션 추가
- Tracker Gantt 에 우클릭 메뉴

[참고]
v8.1.5 기준. setup_v8 파트 최신 업로드 상태.
```

Claude 가 자동으로:
- Project Knowledge 의 v8.1.5 파트에서 현재 파일 내용 추출
- 수정된 파일만 base64 로 묶어 `update_vXXX.py` 생성
- 업데이트 후 교체할 part 번호 알려줌

## 3. 배포 실행

```bash
cd /config/work/holweb-fastapi
python update_v8XX.py             # 파일 덮어쓰기 + npm build

pkill -f "uvicorn app:app" || true
cd backend && uvicorn app:app --host 0.0.0.0 --port 8080 &

# 브라우저 Ctrl+Shift+R
```

## 4. Part 파일 동기화

대화 마무리 시 Claude 가 **수정된 part 파일만** 제공. Project Knowledge 에서 해당 파트만 교체.

### 일반적 업데이트 예시 (Dashboard 수정 시)

| 수정 파일 | 교체할 Part |
|---|---|
| VERSION / changelog | `part01.txt` |
| `dashboard.py` + `My_Dashboard.jsx` | `part07.txt` (feat_dashboard) |

→ **단 2개 파트만 업로드**.

### 업데이트 범위별 영향 Part

| 수정 영역 | 갱신 Part |
|---|---|
| Dashboard | 01 + 07 |
| File Browser (+ S3) | 01 + 08 |
| Split Table / Tracker | 01 + 09 |
| Admin / Table Map | 01 + 10 |
| App.jsx / config.js | 01 + 04 |
| 백엔드 core/ | 01 + 02 |
| Home 페이지 | 01 + 05 |
| ML 백엔드 | 01 + 03 |
| ML 프론트 | 01 + 06 |

## 5. 파일 → Part 매핑 빠른 참조

| Part | 담긴 파일 |
|---|---|
| **02 backend_stable** | `backend/core/*`, `app.py`, `routers/{auth,catalog,monitor,ettime,reformatter,session_api}.py` |
| **03 backend_ml** | `backend/routers/ml.py` |
| **04 frontend_infra** | `index.html`, `vite.config.js`, `main.jsx`, `App.jsx`, `config.js`, `components/*`, `lib/api.js` |
| **05 frontend_stable_pages** | `My_Login`, `My_DevGuide`, `My_ETTime`, `My_Monitor`, `My_Home` |
| **06 frontend_ml** | `My_ML.jsx` |
| **07 feat_dashboard** | `dashboard.py`, `My_Dashboard.jsx` |
| **08 feat_filebrowser** | `filebrowser.py`, `s3_ingest.py`, `My_FileBrowser.jsx` |
| **09 feat_split_tracker** | `splittable.py`, `tracker.py`, `My_SplitTable.jsx`, `My_Tracker.jsx` |
| **10 feat_admin_tablemap** | `admin.py`, `dbmap.py`, `My_Admin.jsx`, `My_TableMap.jsx` |
| **11 scripts** | `scripts/seed_v73_matching.py` + `if __name__=="__main__": setup()` |

## 6. 자주 발생하는 실수

- ❌ setup_v8 전체 재생성 → ✅ 변경 part 만
- ❌ update_vXXX.py 없이 part 만 교체 → ✅ update 먼저, 그 후 part 동기화
- ❌ 여러 버그를 한 update 에 묶기 → ✅ 버그당 update 분리 (롤백 쉽게)
- ❌ VERSION dict 갱신 빼먹기 → ✅ `part01` 에 반드시 새 changelog entry prepend
- ❌ v8.1.5 변경을 v8.0.4 part 에 덮어쓰기 → ✅ 항상 Project Knowledge 의 **최신 part 상태** 기준으로 수정

## 7. 긴급 롤백

```bash
cd /config/work/holweb-fastapi
# 가장 최근 정상 버전의 setup_v8.py 실행 (부분 롤백은 해당 파일만 git checkout)
python setup_v8.py
pkill -f "uvicorn app:app"; cd backend && uvicorn app:app --host 0.0.0.0 --port 8080 &
```

## 8. 체크리스트 (대화 마무리 전)

- [ ] update_vXXX.py 실행 → 서버 반영 확인
- [ ] 브라우저에서 버전 표시 확인 (홈 화면)
- [ ] Project Knowledge 에 바뀐 part 교체 (보통 2~3개)
- [ ] 메모리에 현재 버전 기록 (Claude 가 자동)
