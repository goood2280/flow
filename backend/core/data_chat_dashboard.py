"""Home dashboard views through the same current-WIP APIs as the dashboard page."""
from copy import deepcopy
import re
from fastapi import HTTPException
from core import auth, knob_resolution

DASH = re.compile(r"대시보드|dashboard", re.I)
VOLUME = re.compile(r"물량|점유율|비중|분포|wafer.*(?:share|volume)", re.I)
LOT_TYPE = re.compile(r"lot[_\s-]*type|랏\s*타입|로트\s*타입", re.I)
SPLIT = re.compile(r"split|스플릿|knob|노브|조건|컬러링|색상|나눠", re.I)
ALL = re.compile(r"전체\s*(?:제품|물량)|모든\s*제품|제품별\s*(?:점유율|비중|물량)|all\s*products?", re.I)


def _reply(message, state, query, **tool):
    from core.data_chat import reply
    return reply(message, context=state, ok=not bool(tool.get("missing") or tool.get("error")),
                 tool={"feature":"dashboard", "action":"dashboard.wip", "query_scope":query, **tool})


def _ask(state, query, kind, options, title):
    state["pending_dashboard"]={"query":query,"kind":kind,"options":options}
    return _reply(title,state,query,missing=[kind],needs_input=True,
        clarification={"kind":kind,"title":title,"options":options,"allow_other":False},
        interpretation={"summary":f"대시보드의 현재 물량을 {query.get('mode','wip')} 기준으로 보려는 요청입니다. 실제 제품 또는 KNOB 후보를 확인한 뒤 집계합니다.",
            "status":"needs_input","origin":"현재 WIP·ML_TABLE 후보","details":[],"unresolved":[title]})


def dispatch(text, context, request=None):
    previous=context.get("dashboard_query") or {}
    pending=context.get("pending_dashboard") or {}
    ordinal=re.fullmatch(r"\s*(\d+)\s*번?\s*",text)
    options=pending.get('options', [])
    choice=options[int(ordinal[1])-1] if ordinal and 0<int(ordinal[1])<=len(options) else next((o for o in options if text.strip().casefold() in {o['value'].casefold(),o['label'].casefold()}),None)
    fresh=bool(DASH.search(text) and not re.search(r"정체|stuck|요약\s*지표|등록.*차트|차트.*목록",text,re.I))
    volume=bool(VOLUME.search(text) and (ALL.search(text) or LOT_TYPE.search(text)))
    follow=bool(previous and (SPLIT.search(text) or LOT_TYPE.search(text) or ALL.search(text)))
    if not (fresh or volume or follow or pending):return None
    state=deepcopy(context)
    if pending and re.fullmatch(r"취소(?:해|해줘)?[.!\s]*",text):
        state.pop("pending_dashboard",None)
        return _reply("대시보드 조건 확인을 취소했습니다.",state,{})
    if pending and not (fresh or volume or follow) and re.search(r"보여|그려|추출|조회",text):
        context.pop("pending_dashboard",None)
        return None
    user=auth.current_user(request)
    tabs=auth.effective_permissions(user).get("tabs") or []
    if user.get("role")!="admin" and tabs!="*" and "*" not in tabs and "dashboard" not in tabs:
        return _reply("대시보드 조회 권한이 필요합니다.",state,{},blocked=True,error="permission_denied")
    from routers import dashboard
    from core.data_chat import product_candidates
    try:
        from core import lot_progress_cache
        _,products=dashboard._wip_split_catalog(lot_progress_cache.filebrowser_cache_parquet_file())
        products=sorted({dashboard._wip_split_product_name(p) for p in products})
        query=deepcopy(previous) if follow else {"mode":"wip","prompt":text}
        if pending and (choice or not (fresh or volume or follow)):
            query=deepcopy(pending["query"])
            options=pending["options"]
            ordinal=re.fullmatch(r"\s*(\d+)\s*번?\s*",text)
            choice=options[int(ordinal[1])-1] if ordinal and 0<int(ordinal[1])<=len(options) else next((o for o in options if text.strip().casefold() in {o['value'].casefold(),o['label'].casefold()}),None)
            if not choice:return _ask(state,query,pending['kind'],options,"표시된 실제 후보를 선택해 주세요.")
            query[pending['kind']]=choice['value']
            state.pop("pending_dashboard",None)
            if pending['kind']=="split_col":
                cols=[o['col'] for o in dashboard._wip_split_column_options(query['product'])[0]]
                knob_resolution.remember(user.get('username',''),query['product'],query.get('alias',''),choice['value'],cols)
        else:
            if LOT_TYPE.search(text):query['mode']='lot_type'
            elif ALL.search(text) and VOLUME.search(text):query['mode']='product'
            elif fresh:query['mode']='wip'
            matches=product_candidates(text,products)
            if len(matches)>1:
                return _ask(state,query,'product',[{'label':p,'value':p} for p in matches],'조회할 제품 하나를 선택해 주세요.')
            if matches:query['product']=matches[0]
            elif ALL.search(text):query['product']='ALL'
            elif not query.get('product'):query['product']=state.get('confirmed_product') or ''
        product=query.get('product','')
        if query['mode']=='product':product=query['product']='ALL'
        if not product or (query['mode']=='lot_type' and product=='ALL'):
            return _ask(state,query,'product',[{'label':p,'value':p} for p in products],'물량을 조회할 실제 제품을 선택해 주세요.')
        if product!='ALL' and product not in products:raise ValueError('현재 WIP 캐시에 해당 제품이 없습니다.')
        recolor_text=query.get('recolor_prompt') or text
        recolor=bool(query.get('recolor_prompt') or ((fresh or follow) and SPLIT.search(text) and not LOT_TYPE.search(text)))
        if choice and pending.get('kind')=='split_col':recolor=False
        if recolor:
            if product=='ALL':return _ask(state,{**query,'product':'','mode':'wip','recolor_prompt':recolor_text},'product',[{'label':p,'value':p} for p in products],'KNOB Split을 볼 제품을 먼저 선택해 주세요.')
            cols=[o['col'] for o in dashboard._wip_split_column_options(product)[0] if o['col'].upper().startswith('KNOB_')]
            clean=DASH.sub(' ',recolor_text)
            query.pop('recolor_prompt',None)
            resolution=knob_resolution.resolve(product,clean,cols,user.get('username',''))
            query.update(mode='wip',alias=resolution['alias'])
            if not resolution['exact']:
                options=resolution['options'] or [{'label':c,'value':c} for c in cols]
                if not options:raise ValueError('이 제품의 ML_TABLE에 KNOB Split 열이 없습니다.')
                return _ask(state,query,'split_col',options,'대시보드 색상을 나눌 실제 KNOB 열을 선택해 주세요.')
            query['split_col']=resolution['exact']
        if query['mode']=='wip':
            data=dashboard.wip_split_summary(request=request,product=product,bin_size=30000,split_col=query.get('split_col',''),axis='step_desc',exclude_root_prefix='',lot_type='')
            query['split_col']=data['split_col']
            chart={**data,'kind':'dashboard_wip_split','chart_type':'wip_stacked','title':f"{product} WIP · {data['split_col'] or '제품별'}"}
            rows=[{'공정':b['label'],'Wafer':b['total'],**b['splits']} for b in data['bins']]
            summary=f"{product}의 현재 WIP를 대시보드와 동일한 공정별 wafer 누적 막대로 표시했습니다. 색상 기준은 {data['split_col'] or '제품'}입니다. 미매칭 wafer도 미지정 범주로 유지합니다."
        else:
            data=dashboard.volume_distribution(request=request,product=product,group_by=query['mode'],exclude_root_prefix='')
            label='제품별' if query['mode']=='product' else f'{product} Lot type별'
            rows=data['rows']
            chart={'chart_type':'bar','title':f"{label} 현재 물량 · 총 {data['total_wafers']} wafers",'x_label':label,'y_label':'Wafer 수',
                   'points':[{'x':r['label'],'y':r['wafer_count'],'label':f"{r['share_pct']:.2f}%"} for r in rows]}
            summary=f"{label} wafer 물량과 점유율을 계산했습니다. 분모는 현재 조회 범위의 전체 wafer {data['total_wafers']}개이며, 중복 제품·Root Lot·Wafer는 한 번만 셉니다."
        state.pop('pending_dashboard',None)
        state.update(dashboard_query=query,last_action='dashboard.wip')
        if product!='ALL':state.update(product=product,confirmed_product=product)
        return _reply(summary,state,query,chart_result=chart,table={'columns':list(rows[0]) if rows else [],'rows':rows,'total':len(rows)},
            sources=['/api/dashboard/wip-split' if query['mode']=='wip' else '/api/dashboard/volume-distribution'],
            interpretation={'summary':summary,'status':'completed','origin':'대시보드 API · 현재 WIP 캐시',
                'details':[{'label':'제품','value':product},{'label':'집계 단위','value':'Wafer'},{'label':'현재 총 물량','value':str(data['total_wafers'])},{'label':'제외 Root Lot','value':'없음'}],'unresolved':[]})
    except (HTTPException,ValueError,OSError) as exc:
        state.pop('pending_dashboard',None)
        return _reply(str(exc.detail) if isinstance(exc,HTTPException) else str(exc),state,{},error='dashboard_failed')
