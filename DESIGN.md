# Design

## Source of truth

- Status: Active
- Last refreshed: 2026-07-22
- Primary product surfaces: A4 학생 활동지 HTML 미리보기, 편집 가능한 HWPX
- Evidence reviewed:
  - `../k-teacher-skills/renderers/render.py`의 실제 학생 활동지 HTML/CSS
  - `../k-teacher-skills/providers/materials/worksheet.py`의 활동지 의미 컴포넌트
  - `../reference-baseline/k-teacher-worksheet-baseline.png`의 원본 Group A 렌더링
  - 사용자 피드백: 원본 복제 수준은 밋밋하며 더 완성도 높은 활동지 디자인이 필요함

## Brand

- Personality: 차분함, 지적 호기심, 교실 친화성, 에듀테크 출판물 수준의 정돈감
- Trust signals: 명확한 학습 흐름, 근거 자료의 출처 표시, 충분한 답안 공간, 인쇄 안정성
- Avoid: 관공서 서식 같은 격자 과밀, 랜딩페이지형 네오브루탈리즘, 원색 대면적, 장식용 그림자 남용, 유아용 캐릭터 톤

## Product goals

- Goals:
  - 학생이 목표 → 자료 → 과제 → 자기 점검 → 출구표를 즉시 이해한다.
  - 교사가 한글에서 모든 문구와 표를 편집할 수 있다.
  - 흑백 인쇄에서도 번호·선·명시적 라벨로 정보 구조가 유지된다.
  - 원본 K-Teacher보다 한 단계 더 세련된 편집 디자인을 제공한다.
- Non-goals:
  - 브라우저 CSS 전체를 HWPX로 복제하지 않는다.
  - 포스터, 카드뉴스, 웹 랜딩페이지처럼 만들지 않는다.
  - 장식 이미지를 본문 정보보다 우선하지 않는다.
- Success signals:
  - 첫 화면에서 제목·과목·활동 단계·이름칸이 분리되어 보인다.
  - 표 머리, 자료 카드, 과제 카드, 출구표가 서로 다른 역할로 인식된다.
  - HWPX 구조·레이아웃 검증과 한컴 실제 열기를 통과한다.

## Personas and jobs

- Primary personas: 중학교 학생, 수업 자료를 제작·수정하는 교사
- User jobs:
  - 학생: 자료를 읽고 근거를 표시한 뒤 문장으로 답한다.
  - 교사: 수업에 맞게 질문·표·답안 줄 수를 빠르게 바꾼다.
- Key contexts of use: A4 컬러/흑백 인쇄, 교실 배부, 한컴오피스에서 사전 수정

## Information architecture

- Primary navigation: 페이지 1에서 목표·자료·과제 1, 페이지 2에서 과제 2·점검·출구표
- Core routes/screens: 2쪽 인쇄 문서 하나
- Content hierarchy:
  1. 과목/활동지 식별
  2. 핵심 제목과 설명
  3. 학습자 정보
  4. 오늘의 목표
  5. 자료와 도움말
  6. 번호가 붙은 과제와 답안 영역
  7. 자기 점검과 출구표

## Design principles

- Principle 1: 색보다 편집 위계가 먼저다. 번호, 정렬, 여백, 선을 먼저 설계한다.
- Principle 2: 한 페이지에 강한 포인트는 하나만 둔다. navy와 teal을 주축으로 coral/amber는 출구표에만 쓴다.
- Principle 3: 자료와 응답을 시각적으로 분리한다. 읽는 영역은 밀도 있게, 쓰는 영역은 넉넉하게 둔다.
- Tradeoffs: HWPX 표 셀은 라운드를 지원하지 않으므로 카드만 네이티브 `hp:rect` 글상자로 만들고, 데이터 표와 답안선은 안정적인 표 구조를 유지한다.

## Visual language

- Color:
  - ink `#243447`
  - navy `#19324D`
  - teal `#128277`
  - cobalt `#3267D6`
  - coral `#E86A5A`
  - amber `#A65E15`
  - band `#EDF3FA`
  - source `#F0F8F6`
  - exit `#FFF4E8`
  - line `#D4DEE9`
  - muted `#627386`
- Typography:
  - HTML: Pretendard → Apple SD Gothic Neo → Malgun Gothic → Noto Sans KR
  - HWPX: 맑은 고딕
  - 제목 19pt 상당, 섹션 11pt, 본문 10~10.5pt, 보조 8~9pt
- Spacing/layout rhythm: 4mm 단위, A4 좌우 18mm, 카드 간 4~5mm, 답안선 8mm
- Shape/radius/elevation: HTML 8~14px 라운드와 매우 약한 그림자, HWPX 카드에는 `hp:rect ratio=24`의 중간 라운드. 그림자는 인쇄 안정성을 위해 생략
- Motion: 없음
- Imagery/iconography: 장식 이미지 없음. 두 자리 번호와 영문 eyebrow를 아이콘 대신 사용

## Components

- Existing components to reuse: `doc_header`, `section.block`, `student_task`, `source_card`, `fill_table`, `data_table`, `answer_box`, `exit_ticket`, `page_break`
- New/changed components:
  - Editorial header: 왼쪽 제목부 + 오른쪽 navy worksheet badge
  - Section rail: 두 자리 번호 셀 + 옅은 배경 제목 셀
  - Rounded mission card: 연청 바탕 + teal 라벨 + 목표 문장
  - Rounded task card: cobalt `STEP 01/02` 라벨 + 과제 문장
  - Rounded source card: teal 테두리 + 자료 본문
  - Rounded exit ticket: coral 테두리 + amber 라벨 + 질문
  - Footer: 얇은 navy/line 구분선과 쪽 정보
- Variants and states: 일반 표, 학습자 정보 표, 점검 표, 답안선 표
- Token/component ownership: `scripts/html2hwpx.py`의 `KTEACHER_PALETTE`, `customize_header()`, 의미 컴포넌트 렌더러

## Accessibility

- Target standard: 인쇄 문서 기준 WCAG AA 수준의 텍스트 대비
- Keyboard/focus behavior: 정적 문서이므로 해당 없음
- Contrast/readability: 흰색 위 navy/ink, navy 위 흰색만 사용; 9pt 미만 본문 금지
- Screen-reader semantics: HTML의 제목·표 caption·data-block-type을 보존
- Reduced motion and sensory considerations: 애니메이션 없음, 색만으로 구분하지 않음

## Responsive behavior

- Supported breakpoints/devices: A4 인쇄, 840px 이하 모바일 미리보기
- Layout adaptations: 모바일에서는 페이지 그림자 제거, 표 줄바꿈, 좌우 여백 축소
- Touch/hover differences: 없음

## Interaction states

- Loading: 해당 없음
- Empty: 빈 답안 영역은 최소 3줄을 유지
- Error: 변환 불가 컴포넌트는 일반 본문으로 보존
- Success: 구조 검증, 레이아웃 검증, 한컴 열기 통과
- Disabled: 해당 없음
- Offline/slow network: 완전한 로컬 HTML/HWPX 산출물

## Content voice

- Tone: 짧고 명확한 교사 안내문, 학생에게 직접 말하는 존댓말 또는 명령형을 일관되게 사용
- Terminology: `오늘의 목표`, `자료 읽기`, `STEP`, `스스로 점검`, `EXIT TICKET`
- Microcopy rules: 라벨은 12자 이내, 과제 문장은 한 문장, 도움말은 한 줄 우선

## Implementation constraints

- Framework/styling system: self-contained HTML/CSS, Python+lxml 기반 HTML→design-plan.xml→OWPML→HWPX
- Design-token constraints: 위 팔레트 외 임의 원색 추가 금지, accent 합계 면적 10% 이하
- Performance constraints: 외부 폰트·이미지·네트워크 의존성 없음
- Compatibility constraints: 한컴오피스 HWPX, A4 세로, 맑은 고딕, 네이티브 표·문단·인라인 사각형 도형만 사용
- Test/screenshot expectations:
  - headless Chrome HTML 스크린샷 육안 확인
  - 의미 컴포넌트·팔레트·쪽 나누기 자동 테스트
  - `finalize_hwpx.py --layout`, `validate.py --layout`, 한컴 실제 열기

## Open questions

- [ ] 실제 배포 시 과목별 accent 변형(과학 teal, 국어 coral 등)을 별도 테마로 둘지 / 사용자 / 후속 확장에 영향
