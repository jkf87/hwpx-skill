# v1.11.0 — government(관공서) 템플릿 제거 (2026-08-21)

컬러 배너·섹션 바로 관공서 문서를 "흉내내는" `government` 템플릿을 제거했습니다.
정부 양식 문서는 실제 부처 문서를 복제하는 경로(`gyehoek.py`, `bodojaryo.py`,
`gonmun.py`)가 훨씬 정확하므로, 합성 템플릿을 유지할 이유가 없습니다.

## ⚠️ 제거된 것 (BREAKING)

- `templates/government/` (header.xml, section0.xml) 삭제
  → `build_hwpx.py --template government` 을 더 이상 쓸 수 없습니다.
- `scripts/hwpx_helpers.py` 에서 government 전용 함수 제거
  - `make_cover_banner()` — 3×2 컬러 표지 배너
  - `make_section_bar()` — 1×3 컬러 섹션 바
  - `make_cover_page()` — 위 두 함수에 의존하던 표지 조립
  - `validate_header_for_government()`
  - 나머지 함수(`make_first_para`·`make_body_para`·`make_image_para` 등)는
    그대로입니다. 다만 charPr/paraPr 기본값은 예시일 뿐이니 사용하는 템플릿의
    ID 를 인자로 넘기세요.

### 대신 이렇게 하세요

| 만들려는 것 | 사용할 것 |
|---|---|
| 정부·공공기관 추진계획서 | `scripts/gyehoek.py` (행안부 업무계획 복제) |
| 정부 표준 보도자료 | `scripts/bodojaryo.py` |
| 행안부 표준 기안문 | `scripts/gonmun.py` (별지 제1호서식) |
| 일반 보고서·마크다운 문서 | `scripts/md2hwpx.py`, `build_hwpx.py --template report` |

## K-Teacher 활동지 변환은 그대로 동작합니다

`scripts/html2hwpx.py` 가 스타일 원본으로 government header 를 읽고 있었습니다.
이를 **기존 활동지 양식**(`assets/problem-answer-reference.hwpx`)의 header 로
옮겼습니다. 두 자산 모두 **맑은 고딕**을 같은 크기로 담고 있어 결과물의 글꼴과
디자인은 이전과 동일합니다(변환 결과 K-Teacher 스타일 14개 charPr 전부 맑은 고딕
유지 확인).

- 글꼴 원본 charPr 를 ID 고정(`id='8'`) 대신 **글꼴 이름으로 조회**하도록 바꿔,
  원본 자산이 바뀌어도 깨지지 않습니다.

## 문서

- `SKILL.md` 워크플로우 A 에서 government 전제를 걷어냈습니다. 마크다운 한 편은
  `md2hwpx.py`, 문단을 직접 조립해야 할 때만 `hwpx_helpers.py` 를 쓰도록 정리.
- `README.md`, `references/template-styles.md` 의 government 항목 제거.

## 검증

- 테스트 21개 파일 전부 통과(`test_html2hwpx` 포함).
- 계획서·보도자료·기안문·활동지·마크다운 생성 경로를 실제로 돌려 산출물이
  `fill_hwpx.py check --strict` 를 통과하는 것까지 확인했습니다.
