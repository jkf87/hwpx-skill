# hwpx-skill

[![GitHub stars](https://img.shields.io/github/stars/jkf87/hwpx-skill?style=social)](https://github.com/jkf87/hwpx-skill/stargazers)
[![GitHub release](https://img.shields.io/github/v/release/jkf87/hwpx-skill)](https://github.com/jkf87/hwpx-skill/releases)
[![License](https://img.shields.io/github/license/jkf87/hwpx-skill)](LICENSE)

HWP/HWPX 문서 생성, 변환, 읽기, 편집을 위한 Claude 스킬.

> ⭐ **이 스킬이 도움이 되셨다면 [GitHub에서 Star](https://github.com/jkf87/hwpx-skill)를 눌러주세요!** 한글 문서 자동화가 필요한 다른 분들에게도 닿을 수 있게 도와주세요.

## 기능

| 워크플로우 | 설명 |
|-----------|------|
| **A** | 마크다운/텍스트/URL → HWPX 문서 생성 |
| **B** | 템플릿 플레이스홀더 치환 |
| **C** | 기존 HWPX 문서 편집 (unpack → 수정 → pack) |
| **D** | 레퍼런스 HWPX 기반 새 문서 생성 |
| **E** | HWPX 텍스트 읽기/추출 |
| **F** | 양식 복제 (테이블/이미지/스타일 100% 보존) |
| **G** | 행정안전부 표준 기안문(별지 제1호서식) 생성 + 작성법 자동 검수 (2025 행정업무운영 편람) |
| **H** | **HWP(바이너리) → HWPX 변환** |
| **I** | 문제지 1장 + 답안지 1장 HWPX 생성 |
| **J** | **서식 보존 양식 필드 채우기** |

## 설치

```bash
# 기본 의존성
pip install python-hwpx lxml --break-system-packages

# HWP→HWPX 변환 (워크플로우 H) 추가 의존성
pip install pyhwp5 olefile --break-system-packages
```

## 빠른 시작

### HWP → HWPX 변환

```bash
python3 scripts/convert_hwp.py input.hwp -o output.hwpx
```

### 마크다운 → HWPX 문서 생성

```python
import sys
from pathlib import Path
sys.path.insert(0, str(Path("scripts")))
from hwpx_helpers import *

# section0.xml 조립 → build_hwpx.py로 빌드 → fix_namespaces.py 후처리
```

### 행정안전부 표준 기안문(별지 제1호서식) 생성

```bash
# 샘플 기안문 생성 (두문·본문·결문 + 맑은 고딕 11.5pt)
python3 scripts/gonmun.py --sample --output 기안문.hwpx

# JSON 입력으로 생성
python3 scripts/gonmun.py --input gonmun.json --output 기안문.hwpx

# 작성법 자동 검수 (날짜·시간·금액·붙임·물결표·외국어 병기 등)
python3 scripts/gonmun_lint.py --hwpx 기안문.hwpx --format text
```

### 정부 표준 보도자료 생성 (레퍼런스 복제, 양식 고정)

```bash
# 실제 정부 보도자료 양식(assets/bodojaryo-reference.hwpx)을 복제 — 표·로고·글꼴 100% 보존,
# 본문(□/ㅇ/*)·머리표(보도시점·제목·부제·담당자)만 교체
python3 scripts/bodojaryo.py --sample --output 보도자료.hwpx
python3 scripts/bodojaryo.py --input bodo.json --output 보도자료.hwpx
```

### 공공기관 계획서 생성 (행안부 업무계획 양식, 제목/목차 토글)

```bash
# ⚠️ 계획서 생성 전 제목·목차 포함 여부를 사용자에게 먼저 질문 (PreToolUse 훅 gyehoek_hook.py가 강제)
python3 scripts/gyehoek.py --title "2026년 ○○ 추진계획" --date "2026. 1." --toc  --output 계획서.hwpx
python3 scripts/gyehoek.py --no-title --no-toc --output 계획서.hwpx
```

### 양식 복제

```bash
# 분석
python3 scripts/clone_form.py --analyze sample.hwpx

# 복제 + 텍스트 치환
python3 scripts/clone_form.py sample.hwpx output.hwpx --map replacements.json
python3 scripts/fix_namespaces.py output.hwpx
```

### 양식 필드 채우기

`scripts/fill_hwpx.py`는 kordoc의 `fillHwpx` 설계에서 가져온 라벨 인식,
self-closing 빈 run 처리, 수정 엔트리만 ZIP 패치하는 보존형 채우기 방식을
Python으로 포팅한 도구다. 신청서·정산서·강사카드처럼 라벨-값 셀이나
체크박스/괄호 빈칸이 있는 서식에 먼저 사용한다.

```bash
# 채울 수 있는 필드 분석
python3 scripts/fill_hwpx.py analyze form.hwpx

# values.json 예: {"성명": "홍길동", "연락처": "010-1234-5678"}
python3 scripts/fill_hwpx.py fill form.hwpx output.hwpx --values values.json

# 값 삽입과 비변경 엔트리 보존 검증
python3 scripts/fill_hwpx.py verify output.hwpx --values values.json --original form.hwpx

# 머리말·꼬리말·자동 쪽번호 사후 삽입/제거 (원본 보존, 중복 방지)
python3 scripts/fill_hwpx.py set-header doc.hwpx out.hwpx --text "대외주의" --align center
python3 scripts/fill_hwpx.py set-footer doc.hwpx out.hwpx --text "한국연구재단"
python3 scripts/fill_hwpx.py set-pagenum doc.hwpx out.hwpx --where footer --align center
python3 scripts/fill_hwpx.py remove-header doc.hwpx out.hwpx

# 표 구조/스타일 in-place (셀 배경/테두리·열추가·행삭제·셀병합)
python3 scripts/fill_hwpx.py set-cell doc.hwpx out.hwpx --table 0 --row 0 --col 1 --bg FFE600 --border on
python3 scripts/fill_hwpx.py merge-cells doc.hwpx out.hwpx --table 0 --row 0 --col 0 --row2 0 --col2 2

# 네이티브 수식 삽입 (문법: references/equation-syntax.md)
python3 scripts/fill_hwpx.py add-equation doc.hwpx out.hwpx --after "기준 문구" --script "x^2+y^2=z^2"

# 본문 글자/문단 서식 (굵게·색·크기·정렬·줄간격)
python3 scripts/fill_hwpx.py set-text-style doc.hwpx out.hwpx --after "제목 문구" --bold --color C00000 --size 16
python3 scripts/fill_hwpx.py set-para-style doc.hwpx out.hwpx --after "제목 문구" --align center --line-spacing 180

# 직인/서명 이미지(사용자 제공 PNG)를 기준 문구 위에 떠있게
python3 scripts/fill_hwpx.py place-seal doc.hwpx out.hwpx --image seal.png --anchor "발신명의" --size-mm 20
# 각주·하이퍼링크·책갈피 / 페이지·다단·쪽나누기 / 목록 / 차트
python3 scripts/fill_hwpx.py add-footnote doc.hwpx out.hwpx --after "본문" --text "각주"
python3 scripts/fill_hwpx.py set-page doc.hwpx out.hwpx --orientation landscape --margin-mm 15 --size a4
python3 scripts/fill_hwpx.py set-columns doc.hwpx out.hwpx --count 2 --gap-mm 8
python3 scripts/fill_hwpx.py set-bullet-list doc.hwpx out.hwpx --para 3 --to 6
python3 scripts/fill_hwpx.py insert-chart doc.hwpx out.hwpx --type col --cat cat.json --series series.json

# 문서 테마(제목색·표머리색 일괄) / 도형·글상자 / 이미지 편집
python3 scripts/fill_hwpx.py set-theme doc.hwpx out.hwpx --theme 남색
python3 scripts/fill_hwpx.py insert-textbox doc.hwpx out.hwpx --after "여기" --text "참고" --fill FFF2CC
python3 scripts/fill_hwpx.py list-images doc.hwpx
python3 scripts/fill_hwpx.py resize-image doc.hwpx out.hwpx --index 0 --width-mm 30

# 개인정보(PII) 비경유 양식 채우기 — 값이 stdout/로그/모델 컨텍스트를 안 거침
python3 scripts/secure_fill.py fill form.hwpx out.hwpx --profile profile.json --shred-profile

# 한컴 열림 위험 신호까지 엄격 점검
python3 scripts/fill_hwpx.py check output.hwpx --strict
```

### Final validation and layout QA

```bash
python3 scripts/fix_namespaces.py output.hwpx
python3 scripts/finalize_hwpx.py output.hwpx --strip-linesegarray --layout
python3 scripts/validate.py output.hwpx --layout

# Windows + Hancom Office only
python3 scripts/validate.py output.hwpx --hancom
```

`finalize_hwpx.py` removes stale `hp:linesegarray` layout caches after XML
text replacement and reports likely layout risks, including long single
paragraphs in table cells, short row heights for dense cells, and body text
that lost visible indentation after headings.

### 텍스트 추출

```bash
python3 scripts/text_extract.py doc.hwpx
python3 scripts/text_extract.py doc.hwpx --format markdown
```

### 문제지 + 답안지 생성

```bash
python3 scripts/build_problem_answer_sheet.py \
  --input-json lesson.json \
  --output lesson-sheet.hwpx
python3 scripts/validate.py lesson-sheet.hwpx
```

`lesson.json`에는 제목, 단원, 장면/문항 요약, 예시 답안을 넣는다. 결과물은 1쪽 `문제지`, 2쪽 `답안지` 구조로 만들어진다.

## 프로젝트 구조

```
hwpx-skill/
├── SKILL.md                    # 스킬 전체 문서 (Decision Tree, 워크플로우, 규칙)
├── scripts/
│   ├── hwpx_helpers.py         # 헬퍼 라이브러리 (배너/섹션바/이미지/빌드)
│   ├── convert_hwp.py          # HWP→HWPX 변환
│   ├── build_hwpx.py           # 템플릿+XML → .hwpx 조립
│   ├── fix_namespaces.py       # 네임스페이스 후처리 (필수)
│   ├── clone_form.py           # 양식 복제
│   ├── md2hwpx.py              # 마크다운→HWPX 변환
│   ├── gonmun.py               # 행정안전부 표준 기안문 생성기
│   ├── gonmun_lint.py          # 공문서 작성법 자동 검수기
│   ├── bodojaryo.py            # 정부 표준 보도자료 생성기(레퍼런스 복제)
│   ├── gyehoek.py             # 공공기관 계획서 생성기(행안부 업무계획 복제)
│   ├── gyehoek_hook.py        # PreToolUse 훅 — 계획서 제목/목차 포함 여부 강제 질문
│   ├── analyze_template.py     # HWPX 심층 분석
│   ├── verify_hwpx.py          # 품질 검증
│   ├── validate.py             # 구조 검증
│   ├── finalize_hwpx.py        # line cache removal, layout QA, Hancom open test
│   ├── fill_hwpx.py            # 보존형 양식 채우기 + 머리말/꼬리말/쪽번호/표구조/수식 in-place
│   ├── secure_fill.py          # 개인정보(PII) 비경유 양식 채우기
│   ├── hwpx_guard_hook.py      # 배포 전 HWPX strict gate 보조 훅
│   ├── report_placeholder_hook.py # '브라더 공기관' 예시 보고서 전달 차단 훅
│   ├── text_extract.py         # 텍스트 추출
│   ├── create_document.py      # 문서 생성
│   ├── build_problem_answer_sheet.py # 문제지+답안지 2쪽 생성
│   └── office/                 # unpack/pack 유틸리티
├── templates/                  # 문서 템플릿
│   ├── base/                   # 베이스 skeleton
│   ├── report/                 # 보고서
│   ├── gonmun/                 # 공문
│   ├── minutes/                # 회의록
│   ├── proposal/               # 제안서
│   └── government/             # 관공서 (컬러 배너/섹션 바)
├── assets/                     # 레퍼런스 템플릿
└── references/                 # 기술 문서
```

## HWP→HWPX 변환 지원 범위

| 항목 | 지원 |
|------|------|
| 텍스트 | O |
| 표 | O |
| 이미지 (PNG/JPG/BMP/GIF) | O |
| 도형 (사각형/원/선) | O |
| 컨테이너 (그룹 도형) | O |
| 각주/미주 | O |
| 다단 | O |
| 머리말/꼬리말 | O |
| OLE 객체 | 부분 지원 |
| 수식 | O (`add-equation`) |

## 주요 규칙

1. 모든 빌드 후 `fix_namespaces.py` 필수 실행
2. `.hwp` 파일은 워크플로우 H로 HWPX 변환 후 처리
3. 양식 복제 시 `clone_form.py` 사용 (XML 직접 조작 금지)
4. 템플릿 간 스타일 ID 호환 불가 — 해당 템플릿 ID만 사용
5. `mimetype`은 첫 ZIP 엔트리, `ZIP_STORED`
6. After XML text replacement, remove `hp:linesegarray` before delivery
7. For strict templates, split long table-cell prose and increase row height
8. Use `validate.py --hancom` on Windows when Hancom openability matters
9. 신청서·서식의 빈 필드 채우기는 `fill_hwpx.py analyze → fill → verify → check --strict` 순서로 처리

## 관련 프로젝트

- [hwp2hwpx-python-refactor](https://github.com/jkf87/hwp2hwpx-python-refactor) — HWP→HWPX 변환 엔진
- [kordoc](https://github.com/chrisryugj/kordoc) — 보존형 HWPX 양식 채우기와 ZIP 패치 설계 참고

## 라이선스

MIT
