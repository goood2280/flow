// FabCanvas.ai (flow) 소개 PPT 생성 — 3장, fab 엔지니어 대상, 기능 가치 중심
const pptxgen = require('pptxgenjs');
const path = require('path');

const pres = new pptxgen();
pres.layout = 'LAYOUT_WIDE'; // 13.33 x 7.5 inch
pres.title = 'FabCanvas.ai (flow) — 기능 소개';
pres.author = 'flow team';

// Ocean Gradient 팔레트
const C = {
  bg:       '0A1628', // near-black navy (다크 배경)
  bgAlt:    '101F36',
  primary:  '065A82', // deep blue
  second:   '1C7293', // teal
  accent:   '21295C', // midnight
  line:     '2A4A6B',
  text:     'E8EEF5',
  textDim:  '9FB3C8',
  highlight:'7FB8D3',
  white:    'FFFFFF',
};

const FONT = 'Segoe UI';
const FONT_BOLD = 'Segoe UI Semibold';

// 공통: 배경 + 상단 바 + 하단 푸터
function baseFrame(slide, pageLabel) {
  slide.background = { color: C.bg };
  // 상단 얇은 액센트 바
  slide.addShape('rect', { x: 0, y: 0, w: 13.33, h: 0.08, fill: { color: C.primary } });
  slide.addShape('rect', { x: 0, y: 0.08, w: 4.5, h: 0.04, fill: { color: C.second } });
  // 하단 푸터
  slide.addText('FabCanvas.ai · flow v8.8.15', {
    x: 0.5, y: 7.15, w: 8, h: 0.25,
    fontFace: FONT, fontSize: 9, color: C.textDim,
  });
  slide.addText(pageLabel, {
    x: 11.8, y: 7.15, w: 1, h: 0.25,
    fontFace: FONT, fontSize: 9, color: C.textDim, align: 'right',
  });
}

// ───────────────────────── 슬라이드 1: 타이틀 ─────────────────────────
{
  const s = pres.addSlide();
  s.background = { color: C.bg };

  // 좌측 강조 컬러 바 (세로)
  s.addShape('rect', { x: 0, y: 0, w: 0.25, h: 7.5, fill: { color: C.primary } });
  s.addShape('rect', { x: 0.25, y: 0, w: 0.08, h: 7.5, fill: { color: C.second } });

  // 상단 작은 라벨
  s.addText('SEMICONDUCTOR FAB · DATA ANALYSIS HUB', {
    x: 1.0, y: 1.2, w: 11, h: 0.4,
    fontFace: FONT, fontSize: 14, color: C.highlight, bold: true, charSpacing: 4,
  });

  // 메인 타이틀
  s.addText('FabCanvas.ai', {
    x: 1.0, y: 1.7, w: 11, h: 1.3,
    fontFace: FONT_BOLD, fontSize: 72, color: C.white, bold: true,
  });

  // 서브 워드마크 (flow)
  s.addText('> flow', {
    x: 1.0, y: 3.0, w: 11, h: 0.8,
    fontFace: 'Consolas', fontSize: 40, color: C.second, bold: true,
  });

  // 구분선
  s.addShape('line', {
    x: 1.0, y: 4.1, w: 3.5, h: 0,
    line: { color: C.primary, width: 2 },
  });

  // 한줄 설명
  s.addText('반도체 fab 운영·개발 데이터를 한 곳에서 모으고, 기록하고, 공유하는 분석 허브', {
    x: 1.0, y: 4.3, w: 11.5, h: 0.5,
    fontFace: FONT, fontSize: 20, color: C.text,
  });

  // 대상 사용자 pill
  const pills = ['Fab 엔지니어', '공정 개발', '수율 분석', '장비 운영'];
  pills.forEach((p, i) => {
    const x = 1.0 + i * 2.1;
    s.addShape('roundRect', {
      x, y: 5.1, w: 1.9, h: 0.45,
      fill: { color: C.accent }, line: { color: C.second, width: 1 },
      rectRadius: 0.1,
    });
    s.addText(p, {
      x, y: 5.1, w: 1.9, h: 0.45,
      fontFace: FONT, fontSize: 12, color: C.highlight, align: 'center', valign: 'middle',
    });
  });

  // 하단 태그라인
  s.addText('기록이 쌓이면, 판단이 빨라집니다', {
    x: 1.0, y: 6.3, w: 11, h: 0.4,
    fontFace: FONT, fontSize: 16, color: C.textDim, italic: true,
  });

  baseFrame(s, '01 / 03');
}

// ───────────────────────── 슬라이드 2: 주요 기능 ─────────────────────────
{
  const s = pres.addSlide();
  s.background = { color: C.bg };

  // 상단 타이틀
  s.addShape('rect', { x: 0, y: 0, w: 0.25, h: 7.5, fill: { color: C.primary } });
  s.addText('주요 기능', {
    x: 0.6, y: 0.3, w: 7, h: 0.6,
    fontFace: FONT_BOLD, fontSize: 28, color: C.white, bold: true,
  });
  s.addText('사용자에게 주는 가치 중심', {
    x: 0.6, y: 0.85, w: 7, h: 0.3,
    fontFace: FONT, fontSize: 13, color: C.highlight,
  });
  // 상단 구분선
  s.addShape('line', {
    x: 0.6, y: 1.25, w: 12.2, h: 0,
    line: { color: C.line, width: 1 },
  });

  // 6-카드 그리드 (3x2)
  const cards = [
    {
      icon: '📋',
      title: 'InformLog',
      sub: '접수 → 완료 2단계 업무 흐름',
      body: '제품·Lot 기반으로 요청을 받고 이력 타임라인으로 흐름을 남깁니다. 공동편집·메일 발송·모듈별 요약으로 "누가 무엇을, 어디까지 했는지" 한눈에.',
    },
    {
      icon: '🤝',
      title: 'Meeting',
      sub: '차수 기반 회의 · 실시간 동시편집',
      body: '아젠다·결정사항·액션아이템을 회의록 한 장에서 관리. 결정된 일정은 달력에 자동 반영, 담당자에게 메일 발송까지 한 번에.',
    },
    {
      icon: '📅',
      title: 'Calendar',
      sub: '회의·액션아이템을 하나의 일정 뷰로',
      body: '회의별 고유 색상과 그룹 공개범위로 내 팀 일정만 골라봅니다. 회의에서 만든 action item 이 자동으로 달력 이벤트가 됩니다.',
    },
    {
      icon: '📊',
      title: 'Dashboard',
      sub: '데이터소스 · X/Y · 차트를 몇 클릭으로',
      body: '내 데이터를 불러 멀티 Y 차트로 즉시 시각화. 시리즈별 색상·공개범위(전체/admin/그룹)로 혼자 보거나 팀과 공유합니다.',
    },
    {
      icon: '🗂️',
      title: 'FileBrowser · S3',
      sub: '사내 저장소 · S3 · 내 PC 를 한 트리로',
      body: '제품별 신호등(↑/↓)으로 업·다운로드 상태를 바로 확인. AWS 프로파일을 골라 양방향 동기화, 엑셀은 표로 바로 미리보기.',
    },
    {
      icon: '🔬',
      title: 'SplitTable · Rulebook',
      sub: 'KNOB / INLINE / VM 3분할 뷰',
      body: 'ML_TABLE_ 필터로 학습용 스냅샷만 골라보고, 컬럼 오버라이드·룰북으로 팀 표준을 코드 없이 유지합니다.',
    },
  ];

  const cols = 3, rows = 2;
  const gx = 0.6, gy = 1.5; // grid origin
  const cw = 4.05, ch = 2.65; // card size
  const gap = 0.12;

  cards.forEach((c, i) => {
    const cx = i % cols, cy = Math.floor(i / cols);
    const x = gx + cx * (cw + gap);
    const y = gy + cy * (ch + gap);

    // Card
    s.addShape('roundRect', {
      x, y, w: cw, h: ch,
      fill: { color: C.bgAlt }, line: { color: C.line, width: 1 },
      rectRadius: 0.08,
    });
    // Left accent stripe
    s.addShape('rect', {
      x, y, w: 0.08, h: ch,
      fill: { color: C.second }, line: { color: C.second },
    });

    // Icon
    s.addText(c.icon, {
      x: x + 0.2, y: y + 0.15, w: 0.7, h: 0.6,
      fontFace: 'Segoe UI Emoji', fontSize: 28,
    });
    // Title
    s.addText(c.title, {
      x: x + 0.95, y: y + 0.18, w: cw - 1.1, h: 0.45,
      fontFace: FONT_BOLD, fontSize: 18, color: C.white, bold: true,
    });
    // Sub
    s.addText(c.sub, {
      x: x + 0.95, y: y + 0.62, w: cw - 1.1, h: 0.3,
      fontFace: FONT, fontSize: 11, color: C.highlight,
    });
    // Divider
    s.addShape('line', {
      x: x + 0.25, y: y + 1.0, w: cw - 0.5, h: 0,
      line: { color: C.line, width: 1 },
    });
    // Body
    s.addText(c.body, {
      x: x + 0.25, y: y + 1.1, w: cw - 0.5, h: ch - 1.2,
      fontFace: FONT, fontSize: 11, color: C.text, valign: 'top',
      paraSpaceAfter: 4,
    });
  });

  baseFrame(s, '02 / 03');
}

// ───────────────────────── 슬라이드 3: 향후 계획 + 기대효과 ─────────────────────────
{
  const s = pres.addSlide();
  s.background = { color: C.bg };

  s.addShape('rect', { x: 0, y: 0, w: 0.25, h: 7.5, fill: { color: C.primary } });
  s.addText('앞으로 & 기대효과', {
    x: 0.6, y: 0.3, w: 8, h: 0.6,
    fontFace: FONT_BOLD, fontSize: 28, color: C.white, bold: true,
  });
  s.addText('fab 운영·개발에 가져올 변화', {
    x: 0.6, y: 0.85, w: 8, h: 0.3,
    fontFace: FONT, fontSize: 13, color: C.highlight,
  });
  s.addShape('line', {
    x: 0.6, y: 1.25, w: 12.2, h: 0,
    line: { color: C.line, width: 1 },
  });

  // 좌: 향후 계획 패널
  const leftX = 0.6, leftY = 1.5, leftW = 6.1, leftH = 4.6;
  s.addShape('roundRect', {
    x: leftX, y: leftY, w: leftW, h: leftH,
    fill: { color: C.bgAlt }, line: { color: C.line, width: 1 },
    rectRadius: 0.08,
  });
  s.addShape('rect', {
    x: leftX, y: leftY, w: leftW, h: 0.5,
    fill: { color: C.primary }, line: { color: C.primary },
  });
  s.addText('🗺️  향후 로드맵', {
    x: leftX + 0.2, y: leftY + 0.05, w: leftW - 0.4, h: 0.4,
    fontFace: FONT_BOLD, fontSize: 16, color: C.white, bold: true,
  });

  const roadmap = [
    { t: '쿼리 VM 데모 + ML Table 파이프라인',
      d: '쿼리 결과를 학습용 테이블로 바로 굳혀 재현 가능한 분석 루프' },
    { t: '공정 영역 태깅 + DVC 룰 테이블',
      d: '어떤 수치가 어느 공정에 속하는지 시스템이 스스로 분류' },
    { t: '인과 매트릭스',
      d: '이상 지표들 사이의 인과 관계를 한 장의 표로 요약' },
    { t: '테라팹 전체 데모',
      d: 'fab 전체 규모 데이터에서도 끊김 없이 돌아가는 운영 수준' },
  ];
  roadmap.forEach((r, i) => {
    const y = leftY + 0.75 + i * 1.12;
    // Step badge
    s.addShape('ellipse', {
      x: leftX + 0.25, y: y + 0.05, w: 0.5, h: 0.5,
      fill: { color: C.second }, line: { color: C.second },
    });
    s.addText(String(i + 1), {
      x: leftX + 0.25, y: y + 0.05, w: 0.5, h: 0.5,
      fontFace: FONT_BOLD, fontSize: 16, color: C.white, bold: true,
      align: 'center', valign: 'middle',
    });
    s.addText(r.t, {
      x: leftX + 0.9, y: y, w: leftW - 1.1, h: 0.35,
      fontFace: FONT_BOLD, fontSize: 14, color: C.white, bold: true,
    });
    s.addText(r.d, {
      x: leftX + 0.9, y: y + 0.38, w: leftW - 1.1, h: 0.6,
      fontFace: FONT, fontSize: 11, color: C.textDim,
    });
  });

  // 우: 기대효과 패널
  const rightX = 6.9, rightY = 1.5, rightW = 5.9, rightH = 4.6;
  s.addShape('roundRect', {
    x: rightX, y: rightY, w: rightW, h: rightH,
    fill: { color: C.bgAlt }, line: { color: C.line, width: 1 },
    rectRadius: 0.08,
  });
  s.addShape('rect', {
    x: rightX, y: rightY, w: rightW, h: 0.5,
    fill: { color: C.second }, line: { color: C.second },
  });
  s.addText('✨  기대효과', {
    x: rightX + 0.2, y: rightY + 0.05, w: rightW - 0.4, h: 0.4,
    fontFace: FONT_BOLD, fontSize: 16, color: C.white, bold: true,
  });

  const benefits = [
    { h: '흩어진 기록을 한 곳으로',
      b: '메일·엑셀·메신저에 흩어지던 업무 이력이 제품·Lot 기준으로 한 타임라인에 모입니다.' },
    { h: '회의가 실제 일정이 됩니다',
      b: '회의록에서 정한 action 이 달력·메일로 자동 연결되어 "결정만 하고 끝"을 막습니다.' },
    { h: '내가 보고 싶은 데이터로',
      b: '코드 없이 데이터소스·축·공개범위만 골라 대시보드를 만들고 팀과 공유합니다.' },
    { h: '파일 이동 시간을 줄입니다',
      b: '사내 저장소·S3·내 PC 가 하나의 트리, 신호등으로 상태가 보이니 찾는 시간이 줄어듭니다.' },
    { h: '표준은 코드 없이 유지',
      b: '룰북·오버라이드로 팀 표준을 손으로 관리, 분석가는 분석에 집중합니다.' },
  ];
  benefits.forEach((bf, i) => {
    const y = rightY + 0.7 + i * 0.92;
    // Check mark
    s.addText('✓', {
      x: rightX + 0.25, y: y, w: 0.4, h: 0.35,
      fontFace: FONT_BOLD, fontSize: 18, color: C.highlight, bold: true,
    });
    s.addText(bf.h, {
      x: rightX + 0.7, y: y - 0.02, w: rightW - 0.9, h: 0.32,
      fontFace: FONT_BOLD, fontSize: 13, color: C.white, bold: true,
    });
    s.addText(bf.b, {
      x: rightX + 0.7, y: y + 0.3, w: rightW - 0.9, h: 0.5,
      fontFace: FONT, fontSize: 10.5, color: C.textDim,
    });
  });

  // Claude 협업 성과 강조 배너
  s.addShape('roundRect', {
    x: 0.6, y: 6.2, w: 12.2, h: 0.55,
    fill: { color: C.primary }, line: { color: C.second, width: 1 },
    rectRadius: 0.06,
  });
  s.addText([
    { text: '⚡ ', options: { color: C.white, bold: true } },
    { text: '총 36,757줄 · 약 196만 자', options: { color: C.white, bold: true } },
    { text: '  ·  ', options: { color: C.highlight } },
    { text: 'Claude AI 협업으로 약 2주 만에 구축', options: { color: C.white, bold: true } },
    { text: '    일반 SI 외주 시 약 1억원 · 6~8개월 소요 예상', options: { color: C.highlight } },
  ], {
    x: 0.6, y: 6.2, w: 12.2, h: 0.55,
    fontFace: FONT, fontSize: 12.5, align: 'center', valign: 'middle',
  });

  // 하단 기술 스택 한 줄 (작게)
  s.addText([
    { text: '⚙ 기술 스택: ', options: { bold: true, color: C.highlight } },
    { text: 'React', options: { bold: true, color: C.text } },
    { text: '(프론트엔드) + ', options: { color: C.textDim } },
    { text: 'FastAPI', options: { bold: true, color: C.text } },
    { text: '(백엔드 API) + ', options: { color: C.textDim } },
    { text: 'Polars', options: { bold: true, color: C.text } },
    { text: '(고속 데이터 처리) · 사내망 전용 서비스', options: { color: C.textDim } },
  ], {
    x: 0.6, y: 6.82, w: 12.2, h: 0.25,
    fontFace: FONT, fontSize: 9.5, align: 'left', valign: 'middle',
  });

  baseFrame(s, '03 / 03');
}

// 저장
const outPath = path.resolve(__dirname, '..', 'docs', 'FabCanvas_flow_intro.pptx');
pres.writeFile({ fileName: outPath }).then((f) => {
  console.log('WROTE', f);
});
